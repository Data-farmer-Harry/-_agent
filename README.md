<div align="center">
  <img src="docs/assets/matterlab-hero.svg" alt="MatterLab 研材体" width="100%" />
</div>

<p align="center">
  <a href="./技术文档.md"><strong>完整技术文档</strong></a>
  ·
  <a href="docs/ARCHITECTURE.md">架构设计</a>
  ·
  <a href="docs/RAG_PRODUCTION.md">RAG 评测</a>
  ·
  <a href="docs/MATTERLAB_BENCHMARK_500.md">Benchmark</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/FastAPI-Agent_Backend-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-Workbench-149ECA?style=flat-square&logo=react&logoColor=white" alt="React" />
  <img src="https://img.shields.io/badge/LAMMPS-Real_Execution-5B5EA6?style=flat-square" alt="LAMMPS" />
  <img src="https://img.shields.io/badge/MCP-Tool_Protocol-5E6AD2?style=flat-square" alt="MCP" />
  <img src="https://img.shields.io/badge/RAG-Hybrid_%2B_Rerank-17A673?style=flat-square" alt="RAG" />
</p>

## 项目简介

**研材体 MatterLab** 是一个面向材料计算工作流的自适应科学 Agent。系统把自然语言需求转换为可审计的 LAMMPS/CALPHAD 任务，并统一编排动态模型路由、按需 RAG、Function Calling、MCP、共享记忆、DAG 恢复、科学质量门和端到端评测。

项目希望解决的不是“让模型生成一段看起来合理的脚本”，而是如何把材料研究任务变成一条可执行、可恢复、可验证、可追溯的工程链路。LLM 负责语义理解、候选规划和自然语言合成；确定性代码负责路由校准、权限边界、科学约束、真实执行和结果验收。

系统同时服务两类场景：一类是 LAMMPS、CALPHAD、相图识别等计算任务，另一类是材料知识检索、模拟诊断、项目文件分析和外部工具协作。不同请求不会机械地经过同一条重链路，而是由 Supervisor 根据任务证据、复杂度与风险按需组合模型、RAG、工具、记忆和运行时。

| **520 cases** | **4 个模型层级** | **real_required** | **可回归评测** |
| :---: | :---: | :---: | :---: |
| 覆盖 Agent 全链路 | 加权推荐<br/>fast / balanced / strong / vision | 普通 LAMMPS<br/>默认真实执行 | development / frozen<br/>分离管理 |

## 运行效果

<p align="center">
  <img src="docs/assets/screenshots/workbench-lammps-result.jpg" alt="MatterLab 真实 LAMMPS 结果工作台" width="100%" />
</p>
<p align="center"><sub>真实 LAMMPS 结果工作台：计算状态、科学 provenance、轨迹动画与产物导航集中呈现。</sub></p>

## 系统架构

<p align="center">
  <img src="docs/assets/adaptive-agent-routing.svg" alt="MatterLab 自适应 Agent 路由架构" width="100%" />
</p>
<p align="center"><sub>规则给出安全边界，MLP 仅提供模型层级的加权建议；RAG、Tool/MCP 与科学计算运行时均按需启用。</sub></p>

一次请求不会默认经过所有高级模块。Supervisor 先判断任务的领域、难度与风险；简单问题走低延迟直答，专业查证才触发 RAG，需要外部能力才调用 Tool/MCP，计算任务才进入受约束的科学运行时。

## 核心技术实现

### 1. Supervisor：可计算置信度，而非相信 LLM 自报

路由候选来自可观测信号，包括生成/识别/执行意图、材料体系、上传资产、LAMMPS 必填槽位和历史上下文。最终置信度由程序计算：

~~~text
confidence = 0.45 × route_evidence
           + 0.25 × candidate_separation
           + 0.20 × critical_check_pass_rate
           + 0.10 × advisory_check_pass_rate
           − deterministic_penalties
~~~

Supervisor 同时校验 route contract、计算前置条件、跨域冲突和 DAG 拓扑。关键检查失败时先澄清；置信度低于阈值或候选差距过小时才调用 LLM 复核，LLM 不能绕过关键安全门。

### 2. 动态 LLM 路由：规则基线 + 加权推荐

模型路由将请求分配到 <code>fast / balanced / strong / vision</code>。本地 MLP 不负责判断回答是否正确，它只是根据任务正文、上下文负载、多模态标记和代码/材料/审查特征，对四个模型层级生成一组推荐权重；<code>routing_focus_text</code> 会剥离 memory、tool、RAG wrapper 噪声。

| 机制 | 作用 |
| --- | --- |
| <code>shadow</code> | 只记录 MLP 推荐和概率，不改变线上决策 |
| <code>guarded</code> | 推荐稳定且不违反规则时，才允许调整默认层级 |
| capability floor | LAMMPS、repair、judge、vision 不得降到不安全模型 |
| telemetry | 仅保存 prompt hash、特征与决策，不保存原始问题或 API Key |

离线样例用于观察三个效果：简单任务是否倾向低成本层级、复杂任务是否升级、加入长上下文或 deployment wrapper 后推荐是否仍保持稳定。最终选择始终受规则基线和 capability floor 约束，因此评估只讨论推荐行为、能力下限与稳定性，不把它包装成独立分类任务。

### 3. 风险感知 RAG：只有需要证据时才检索

~~~mermaid
flowchart LR
    Q["User Query"] --> GATE{"RAG Gate"}
    GATE -->|simple / follow-up| DIRECT["Direct Answer"]
    GATE -->|knowledge / evidence| RW["Query Rewrite"]
    RW --> BM["BM25 / Structured Lexical"]
    RW --> VE["Dense Embedding"]
    BM --> POOL["Candidate Pool"]
    VE --> POOL
    POOL --> RR["Remote Reranker"]
    RR --> GR["GraphRAG Propagation"]
    GR --> UQ{"Uncertainty"}
    UQ -->|high confidence| ANSWER["Answer + Citation"]
    UQ -->|uncertain| EXPAND["Expand / Escalate / Abstain"]
~~~

Materials RAG 与 Thermo RAG 采用多阶段召回：本地 Query Rewrite、BM25/结构化词法、远程 Embedding、候选池 Rerank 和轻量异构 GraphRAG。冻结盲测主要用于比较改写、扩大候选池和重排前后的检索变化；当前结果表明重排改善了相关证据的前排位置，扩大候选池减少了“正确文档根本没被召回”的情况。它用于验证检索链路改动，不等价于真实用户问答质量。[查看评测设计与边界](docs/RAG_PRODUCTION.md)

### 4. 真实 LAMMPS：神经—符号编译与科学质量门

<p align="center">
  <img src="docs/assets/lammps-md-pipeline.svg" alt="LAMMPS 分子动力学计算示意图" width="100%" />
</p>
<p align="center"><sub>从晶体结构、势函数和系综约束，到真实分子动力学执行、物理质量门与可追溯产物。</sub></p>

~~~text
Natural Language
  → LammpsRequest
  → Typed Simulation IR + Provenance
  → Symbolic Validator / Registry Guard
  → Deterministic in.lammps Compiler
  → Real Local LAMMPS
  → Physical Quality Gate
  → Red-Blue Review
  → thermo.csv / dump.atom / plot / GIF / MP4 / trace
~~~

普通 LAMMPS 请求默认 <code>real_required</code>：执行环境异常时返回诊断，禁止用 synthetic 结果冒充科学计算。类型化 IR 锁定材料、势函数、系综、温压、时间步长和输出契约；质量门检查 lost atoms、温度/能量异常、步数覆盖和 real/mock provenance；Red Agent 发现问题后，Blue Agent 只能执行白名单结构化 patch，并在修复后重新验证。

### 5. Tool、MCP、记忆与长任务编排

| 模块 | 工程设计 |
| --- | --- |
| Tool Router | 只有触发明确能力需求时才 Function Calling；统一 schema、风险等级、超时和审计 |
| MCP | 项目可作为 stdio MCP Server 暴露 LAMMPS/相图能力，也可接入可信外部 MCP 工具 |
| Shared Memory | SQLite 持久化；L1 粗召回 → L2 MMR/TextRank → L3 原文证据保留 |
| Memory Safety | 写前去重、数值/语义矛盾检测、source scope 隔离和多策略消解 |
| Orchestration | <code>asyncio + Semaphore</code> 执行 DAG，支持 checkpoint、动态 replan 与 partial synthesis |
| PRM Planning | 对 baseline/robust/efficient 候选 DAG 做步骤级 reward 和 Best-of-N 选择 |

## 能力矩阵

| 能力域 | 代表能力 | 关键产物 |
| --- | --- | --- |
| LAMMPS Agent | 解析、IR、势函数注册、真实执行、质量审查、轨迹可视化 | <code>in.lammps</code>、log、thermo、dump、GIF、MP4 |
| CALPHAD Agent | TDB 自动选择、二元/三元相图、相区计算 | HTML、相图、trace、数据库 provenance |
| Materials RAG | 材料知识、LAMMPS 文档、多跳证据、引用 | ranked evidence、source refs、uncertainty |
| Recognition | 相图截图结构化识别与交互重构 | labels、axes、structured scene |
| Agent Platform | Tool/MCP、Skills、记忆、动态模型路由 | tool trace、memory refs、route telemetry |
| Observability | Route、Tool、RAG、Memory、LLM、DAG、Red-Blue | 前端状态节点与可展开证据链 |

## 评测体系与观察效果

**MatterLabAgentBench-500+Trajectory** 包含 520 条 case、14 个能力域，其中 333 条 frozen test。新增样例覆盖 LAMMPS planning、轨迹一致性、RAG 多跳、Tool/MCP、共享记忆、恢复策略、相图 registry 和最终回答。

| 评测层 | 指标与约束 |
| --- | --- |
| Layer 1 · Deterministic | route、tool chain、artifact completeness、物理约束、real/mock provenance、引用覆盖 |
| Layer 2 · LLM-as-Judge | factuality、logical consistency、citation quality、physical validity、actionable clarity |
| Layer 3 · Statistics | paired bootstrap 95% CI、Cohen's dz、McNemar、risk difference、版本回归 gate |
| Trajectory Eval | timestep 单调性、帧/原子数一致性、NaN、unwrapped coordinates、OVITO 产物 |

Benchmark 采用 development/frozen 分离、case-level hash、防数据泄漏扫描和 provider 显式开关。Deterministic、真实 LAMMPS 与 live API 评测分开报告，避免把 mock contract 结果包装成真实科学效果。[查看 Benchmark 设计](docs/MATTERLAB_BENCHMARK_500.md)

| 被评估模块 | 目前重点观察的效果 |
| --- | --- |
| 动态路由 | 简单请求避免进入重模型，复杂计算和审查任务不低于能力下限；长上下文噪声不应改变当前任务方向 |
| RAG | Query Rewrite 扩展中英文和材料别名，Reranker 改善证据顺序，低证据场景进入 expand/escalate/abstain |
| Tool / MCP | 只有明确工具意图才调用；schema、权限、超时和失败结果都进入 trace |
| LAMMPS | 普通请求保持真实执行 provenance；失败时给出诊断，质量门拦截 synthetic 或明显异常结果 |
| DAG / Recovery | 节点依赖、并发上限、checkpoint 和降级行为可以通过故障注入重复验证 |
| Shared Memory | 重复事实减少，矛盾信息不被静默覆盖，关键原文证据在压缩后仍可追溯 |

## 技术栈与代码结构

| 层 | 技术 |
| --- | --- |
| Frontend | React、TypeScript、Vite、SSE、Artifact/Trace Dashboard |
| Agent Backend | Python、FastAPI、LangGraph、Pydantic、异步 Job Worker |
| Scientific Runtime | LAMMPS、OVITO、pycalphad、TDB Registry |
| Retrieval & Memory | BM25、OpenRouter Embedding、Cohere Rerank、sqlite-vec、NumPy |
| Learning & Evaluation | Local MLP、PRM-style reward、Bootstrap、LLM-as-Judge |
| Integration | Function Calling、MCP stdio、OpenAI-compatible API |

~~~text
backend/app/
├── agents/          # Supervisor / Chat / Compute / Recognition
├── runtimes/        # LAMMPS 与相图运行时
├── lammps/          # IR、preflight、runner、quality、review、postprocess
├── materials_rag/   # hybrid retrieval、GraphRAG、context builder
├── shared_memory/   # SQLite、检索、去重、冲突检测
├── orchestration/   # DAG、状态机、replan、reward
└── tools/           # Function Tools、policy、MCP adapter
~~~

完整目录、API、配置优先级、排错和开发约定请阅读 [技术文档](./技术文档.md)。

## 快速启动

~~~bash
# Backend
conda env create -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r backend/requirements/all.txt
cp backend/.env.example backend/.env
(cd backend && conda run -n lammps_agent uvicorn app.main:app --host 127.0.0.1 --port 8000)

# Frontend
(cd frontend && npm ci)
(cd frontend && npm run dev -- --host 127.0.0.1 --port 5174)
~~~

API Key 只应写入 <code>backend/.env</code> 或系统 Secret Manager；<code>.env</code>、运行产物、依赖环境和外部 MCP 私有配置均被 Git 忽略。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [技术文档](./技术文档.md) | 完整能力、模块说明、安装、API、测试与排错 |
| [Architecture](docs/ARCHITECTURE.md) | Agent 图、运行时和数据流 |
| [Advanced Learning Methods](docs/ADVANCED_LEARNING_METHODS.md) | MLP、PRM、神经—符号 IR、GraphRAG、多保真 |
| [RAG Production](docs/RAG_PRODUCTION.md) | Embedding/Reranker 配置与冻结盲测 |
| [Benchmark 500+Trajectory](docs/MATTERLAB_BENCHMARK_500.md) | 数据构建、领域分布、指标与统计策略 |

---

> MatterLab 输出的是可审计的计算建议与运行产物。用于论文、生产决策或昂贵计算前，仍应复核势函数来源、边界条件、热力学数据库、运行日志、质量门和引用证据。
