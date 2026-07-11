# MatterLabAgentBench-500+Trajectory 构建说明

MatterLabAgentBench-500+Trajectory 是一个面向“研材体 / MatterLab”完整 Agent 的综合 benchmark。它不是单纯问答集，而是覆盖 route、RAG、工具调用、MCP、LAMMPS、轨迹测评、相图、共享记忆、恢复策略和最终回答质量的端到端评测数据集。

## 1. 为什么这样构建

公开 benchmark 和 eval 文档给出的共同原则是：评测要围绕真实应用目标设计，不能只看一个总分。

| 参考 | 可迁移原则 | 本项目落地方式 |
| --- | --- | --- |
| OpenAI evaluation best practices | 先定义目标，再收集数据、定义指标、运行比较、持续评估；数据要包含典型、边界和对抗样例。 | 每条 case 带 domain、difficulty、required evidence、forbidden claims、tool chain 和 split。 |
| HELM | 多场景、多指标、透明可复现，而不是只报单一 accuracy。 | manifest 按 domain、mode、split、source dataset 统计；保留 frozen split hash。 |
| AgentBench | Agent 要在交互环境里测推理、决策、工具选择和长程执行失败。 | 增加 tool/MCP、DAG recovery、LAMMPS preflight、memory conflict 和 final synthesis case。 |
| RAGAS | RAG 评测应拆成检索相关性、上下文召回、faithfulness 和生成质量。 | RAG case 明确 required evidence、citation gold、context recall/precision、faithfulness 等期望指标。 |

## 2. 数据规模

| 项 | 数量 |
| --- | ---: |
| 总 case | 520 |
| 复用现有 MaterialsAgentBench case | 390 |
| 新增 MatterLab augmented case | 130 |
| 默认 frozen_test 占比 | 约 64% |
| 默认 development 占比 | 约 36% |

构建命令：

```bash
cd backend
conda run -n lammps_agent python benchmarks/build_matterlab_agent_bench_500.py --summary-only
```

运行 deterministic schema/rule-layer 质量门：

```bash
cd backend
conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite matterlab_agent_bench_500
```

输出目录：

```text
backend/benchmarks/datasets/matterlab_agent_bench_500/
├── manifest.json
├── development/cases.jsonl
└── frozen_test/cases.jsonl
```

## 3. 新增 110 条 case 的领域分布

| 新增来源 | 条数 | 目标 |
| --- | ---: | --- |
| `matterlab_lammps_planning_cases` | 25 | 测 LAMMPS request parse、registry guard、preflight、质量门和修复前置条件。 |
| `matterlab_trajectory_cases` | 20 | 测 dump/lammpstrj、帧数、timestep 单调性、原子数一致性、NaN 坐标、OVITO 预览/降级和轨迹 artifact 可追溯。 |
| `matterlab_rag_multihop_cases` | 20 | 测 query rewrite、RAG 召回、rerank、引用和多跳材料知识回答。 |
| `matterlab_tool_mcp_cases` | 10 | 测 function-calling 工具选择、MCP 协议和外部工具风险边界。 |
| `matterlab_memory_cases` | 15 | 测共享记忆去重、scope 隔离、冲突检测和原文证据保留。 |
| `matterlab_recovery_cases` | 10 | 测 DAG 节点超时、批量失败、全局超时、cancel/resume 和 checkpoint 复用。 |
| `matterlab_final_response_cases` | 15 | 测最终回答是否区分证据、推断和建议，是否避免把 mock 当真。 |
| `matterlab_phase_registry_cases` | 10 | 测相图 registry 优先级、别名匹配、TDB 选择和 RAG 不覆盖 exact match。 |
| `matterlab_dynamic_route_cases` | 5 | 测动态 LLM 路由、MLP shadow/guarded 策略和能力下限保护。 |

## 4. 每条 case 的核心字段

| 字段 | 作用 |
| --- | --- |
| `case_id` | 全局唯一 ID。 |
| `domain` | 评测领域，例如 `lammps_execution`、`materials_rag`、`mcp_tooling`。 |
| `difficulty` | `normal`、`edge` 或 `adversarial`。 |
| `prompt` | 用户输入。 |
| `expected_route` | 期望路由。 |
| `expected_compute_domain` | 期望计算域。 |
| `locked_constraints` | 不允许被 Agent 擅自改写的关键参数。 |
| `required_tool_chain` | 必须出现的工具或内部步骤。 |
| `required_evidence` | 必须检索或保留的证据。 |
| `required_artifacts` | 必须生成的运行产物。 |
| `forbidden_claims` | 明确禁止的幻觉或危险说法。 |
| `claim_gold` / `citation_gold` | 给规则 evaluator 或 LLM-as-Judge 使用的金标断言和引用要求。 |
| `metadata` | 记录生成方式、失败模式、语言、style 等可审计信息。 |

## 5. 推荐指标

| 层级 | 指标 |
| --- | --- |
| Route | route accuracy、compute-domain accuracy、clarification accuracy。 |
| Tool/MCP | tool call accuracy、tool call F1、tool argument precision、protocol pass rate。 |
| RAG | context recall、context precision、citation coverage、citation precision、faithfulness、noise sensitivity。 |
| LAMMPS | locked constraint accuracy、artifact completeness、quality-gate pass rate、real/mock provenance accuracy、fatal anomaly recall。 |
| Trajectory | trajectory file presence、frame count validity、atom count consistency、coordinate finite rate、timestep monotonicity、visualization artifact rate、raw dump retention。 |
| Memory | duplicate recall、scope isolation rate、conflict recall、raw evidence retention、compression traceability。 |
| Recovery | checkpoint reuse accuracy、safe replan rate、partial-report honesty、global-timeout handling。 |
| Final answer | factual accuracy、critical hallucination rate、physical validity、actionable clarity、limitation honesty。 |
| Efficiency | latency P50/P95、cost estimate、LLM tier selected、retry count。 |

## 6. 与现有 benchmark 的关系

MatterLabAgentBench-500 不替换原有 `MaterialsAgentBench v1`。它复用原有 390 条 case 作为稳定基础，然后额外补 110 条更偏完整 Agent 的测试。

这样设计的好处是：旧的 quick gate、freeze lock 和 evaluator 不被破坏；新的 500 条 benchmark 可以作为更完整的阶段性质量门，后续如果稳定，再考虑把其中一部分并入主 frozen benchmark。

## 7. 维护策略

新增 case 优先放进生成脚本，而不是手改 JSONL。每次修改后运行：

```bash
cd backend
conda run -n lammps_agent python benchmarks/build_matterlab_agent_bench_500.py --summary-only
conda run -n lammps_agent python -m pytest tests/test_matterlab_benchmark_500.py -q
```

如果要把该 benchmark 纳入 CI，可以先只校验 schema 和 manifest，不默认跑 live API、真实 embedding、真实 reranker 或真实 LAMMPS。

## 8. 参考资料

| 资料 | 链接 |
| --- | --- |
| OpenAI Evaluation best practices | https://developers.openai.com/api/docs/guides/evaluation-best-practices |
| OpenAI Working with evals | https://developers.openai.com/api/docs/guides/evals |
| OpenAI Evaluate agent workflows | https://developers.openai.com/api/docs/guides/agent-evals |
| HELM: Holistic Evaluation of Language Models | https://arxiv.org/abs/2211.09110 |
| HELM project page | https://crfm.stanford.edu/helm/ |
| AgentBench: Evaluating LLMs as Agents | https://arxiv.org/abs/2308.03688 |
| AgentBench repository | https://github.com/THUDM/AgentBench |
| RAGAS paper | https://arxiv.org/abs/2309.15217 |
| RAGAS available metrics | https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ |
