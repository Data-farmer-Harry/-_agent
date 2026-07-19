# 研材体高级学习方法：设计、实现与简历口径

本文记录研材体在原有 RAG、MCP、DAG、共享记忆、Red-Blue 审查和 MLP 路由之上新增的五条方法链。目标不是增加更多 Agent，而是让模型选择、任务规划、科学脚本生成、证据检索和仿真资源分配形成可学习、可验证、可降级的闭环。

## 1. 总体闭环

```text
任务特征
  → 规则/MLP 安全基线
  → prompt-wrapper 去噪与 capability floor
  → Best-of-N DAG 候选生成
  → Process Reward Model 步骤级评分
  → 类型化 LAMMPS IR
  → 神经—符号约束验证与确定性编译
  → 多保真 pilot/full 调度
  → 物理质量门与轨迹奖励
  → 反馈路由器和后续规划

知识问题
  → Query Rewrite
  → BM25 + Dense + Reranker
  → Materials GraphRAG 关系传播
  → 检索不确定性估计
  → answer / expand / escalate / abstain
```

所有学习策略遵守同一安全规则：学习模型只能在显式允许的边界内优化，不能绕过 capability floor、registry、schema、物理质量门或 mock/real provenance。

## 2. 部署态小型 MLP 路由

模型把 `fast / balanced / strong / vision` 建模为四分类任务，使用单隐藏层 ReLU MLP 在本地推理。当前不引入在线 Contextual Bandit：项目还没有稳定的用户反馈、质量 reward、模型价格和探索流量，在线探索既难验证，也会让简单请求产生额外状态和延迟。

特征不保存 prompt 原文，包括总上下文长度、当前任务长度、生成预算、多模态标记、JSON/code/LAMMPS/materials/review marker 和 capability。部署中的 ChatAgent prompt 会包含 memory、tool、RAG、history 等固定栏目；`routing_focus_text()` 先提取 `User message` 正文，任务 marker 只从正文计算，而总 prompt 长度仍作为负载特征。这样不会因为历史里出现 `LAMMPS traceback` 或 `RAG` 就把当前的简单追问错误升级。

```text
完整 prompt → 总上下文负载特征
User message → 任务 marker 特征
capability → 安全下限特征
        ↓
单隐藏层 MLP → fast / balanced / strong / vision 概率
        ↓
Temperature Scaling → 校准概率
        ↓
置信度 + Top-1/Top-2 margin + entropy + OOD 检查
        ↓
模型能力兼容性 + capability floor → 最终 tier
```

| 模式 | 行为 |
| --- | --- |
| `shadow` | 记录 MLP 推荐、概率和置信度，不改变规则决策。 |
| `guarded` | 校准置信度、概率间隔、熵和 OOD 检查全部达标后才允许覆盖规则 tier。 |
| capability floor | LAMMPS、repair、judge、vision 等仍受最低 tier 限制。 |
| model capability check | 模型声明的 structured output、code、reasoning、vision 能力必须覆盖调用要求。 |
| downgrade policy | 是否允许降级由配置控制，高风险能力仍不能低于安全下限。 |

模型与报告位于 `backend/models/llm_route_mlp/`。训练数据分别记录规则构造样本、仿真生产遥测与隐私安全真实遥测；仿真生产遥测根据候选层级的质量、延迟、成本、成功率和 capability floor 生成，并明确标记为非真实用户流量。训练流程使用独立 calibration split 拟合 Temperature Scaling 和 OOD 阈值，再在冻结 test 与手写 probe 上评估。现有结果仍不能直接等价为线上回答质量或真实成本下降；正式实验还应持续增加人工复核流量、时间切分、provider OOD、P95 延迟和质量保持率。

## 3. Process Reward Model 与搜索式 DAG

`backend/app/orchestration/reward.py` 为同一任务生成语义等价候选：

| 候选 | 目标 |
| --- | --- |
| baseline | 保留原 timeout、retry 与全局预算。 |
| robust | 增加可重试节点的 attempt 和 timeout。 |
| efficient | 压缩非关键节点预算，减少等待。 |

候选不会改变材料、势函数、系综、温度或步数。PRM 使用可验证信号：关键节点覆盖、依赖有效性、重试安全、证据输出、预算成本、执行完成、失败、超时与 fallback。

预执行阶段用静态 step reward 做 Best-of-N；结束后根据 `DAGNodeResult` 生成 `process-reward-trace/v1`：

- total/normalized reward；
- progress rate；
- 每节点 reward components；
- missing、failed、timed_out 与 fallback 原因。

接口可在积累真实轨迹后替换成小型 value network 或 LLM-based PRM，无需修改 DAG executor。

## 4. 神经—符号 LAMMPS IR

```text
自然语言
  → LLM/heuristic LammpsRequest
  → LammpsSimulationIR
  → Symbolic Validator
  → Deterministic Template Compiler
  → in.lammps
```

LLM 不直接控制最终脚本。`backend/app/lammps/ir.py` 显式建模材料/结构、势函数、系综、温压控制、timestep、steps、dump/thermo、单位、边界、locked fields 与 provenance。

编译前检查：

- material/potential/task 是否注册；
- 温度与时间单位；
- NVT/NPT 参数完整性；
- EAM 映射或自定义势来源；
- heating 目标温度高于初始温度；
- thermostat damping 至少覆盖 10 个 timestep；
- timestep、温度和步数边界；
- dump interval 是否能产生轨迹。

每轮额外输出 `lammps_ir.json` 和 `lammps_ir_validation.json`，从用户约束追踪到最终脚本。

## 5. Materials GraphRAG 与风险控制

`backend/app/materials_rag/graph.py` 从文档元数据构建轻量异构图：

```text
Document ↔ Material / Method / Tool / Phase / Potential / Domain Community
```

BM25/dense hit 作为 seed，通过共享实体向相邻文档传播分数，并记录 `query → entity → document` 与 `seed document → entity → candidate document` 路径。最终融合 lexical、BM25、dense、reranker 和 graph score；图证据不能覆盖 registry 或执行证据。

`backend/app/rag/uncertainty.py` 使用 top score、top-1/top-2 margin、五通道一致度和 evidence diversity 计算置信度，输出：

| 动作 | 含义 |
| --- | --- |
| `answer` | 证据充分，可生成有引用回答。 |
| `expand` | 扩大 top-k、改写 query 或检索相邻 community。 |
| `escalate` | 升级强模型或严格 evidence critic。 |
| `abstain` | 证据不足，不生成确定性科学结论。 |

`ConformalRiskCalibrator.fit()` 可从冻结校准集的 confidence/correctness 学习 selective threshold。默认阈值只是保守初值。

## 6. 多保真仿真与 Value of Information

`backend/app/lammps/multifidelity.py` 把高成本任务拆成 pilot/full：

```text
请求风险估计
  → 是否需要 pilot
  → 约 5% 短程模拟
  → 物理质量门
  → 稳定性、失败概率、信息增益
  → continue_full / repair / stop
```

初始风险来自温度、timestep、heating ramp 和步数。pilot 后综合执行状态、quality gate、scientific provenance、fatal log anomaly、温度与能量漂移。

```text
Value of Information = entropy reduction / pilot compute ratio
```

该能力默认关闭，以 `LAMMPS_MULTIFIDELITY_ENABLED=true` 启用。启用后生成 `multifidelity_report.json`，pilot 未通过时不继续完整模拟。

## 7. 专项 benchmark

```bash
make test-advanced-methods
```

| 模块 | Case | 内容 |
| --- | ---: | --- |
| MLP route policy | 41 | 冻结 probe 覆盖普通、噪声、部署 wrapper 和安全能力下限。 |
| PRM plan search | 40 | 候选评分、拓扑不变与选择契约。 |
| 神经—符号 IR | 100 | 合法请求通过与 mutation 拒绝。 |
| uncertainty RAG | 80 | 高证据 answer 与无证据 abstain。 |
| 多保真调度 | 80 | 低成本直跑、高风险 pilot 与失败终止。 |

当前本地确定性集合为 341 条。它只能称为 method-contract accuracy，不能称为真实 LLM 回答准确率、RAG 事实准确率或 LAMMPS 科学准确率。

## 8. 简历写法

项目标题：

```text
研材体 MatterLab：面向材料计算的自适应科学 Agent
```

推荐描述：

```text
面向 LAMMPS/CALPHAD 材料计算工作流，构建集动态模型路由、搜索式 DAG 规划、
神经—符号科学编译、风险感知 GraphRAG、共享记忆和轨迹评测于一体的科学 Agent。
```

技术条目：

```text
• 设计轻量 MLP 动态模型路由，将 fast/balanced/strong/vision 建模为四分类任务；
  通过 prompt-wrapper 去噪、deployment-noise 训练、shadow/guarded 与 capability floor，
  避免固定 memory/tool/RAG 上下文污染任务难度判断。

• 将材料计算 DAG 建模为步骤级奖励搜索，为 baseline/robust/efficient 候选计划
  构建 Process Reward Model，并以 Best-of-N 与运行时 reward trace 支持动态恢复。

• 设计神经—符号 LAMMPS 编译链，将自然语言约束转换为带单位和 provenance 的
  类型化 Simulation IR，经材料/势函数/系综/阻尼/时间步验证后确定性生成脚本。

• 构建材料异构 GraphRAG，在 BM25、dense retrieval、reranker 之上融合
  Material/Phase/Potential/Tool 关系传播，并以 conformal calibration 实现
  answer/expand/escalate/abstain 风险控制。

• 实现 Value-of-Information 多保真调度，以短程 pilot simulation 估计稳定性、
  失败概率和信息增益，在完整 LAMMPS 执行前自动继续、修复或终止。
```

正式写入简历前，应把方法描述配上真实冻结实验值：质量保持率、成本下降、P95 延迟、OOD 成功率、IR mutation rejection、RAG risk-coverage AURC 和节省的真实 core-hours。
