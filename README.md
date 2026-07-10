# MatterPilot（材算体）

MatterPilot（材算体）是一个面向材料计算任务的科研智能体工作台。它不是单一的 LAMMPS 包装器，也不是普通聊天机器人，而是把自然语言任务理解、相图计算、分子动力学模拟、材料知识 RAG、共享记忆、自动审查修复、前端 artifact 展示和 benchmark 评测放在同一个可运行系统里。

项目历史名称为 `Phase Diagram Agent`。当前仓库仍保留 `phase_diagram_agent` 目录名，但 README 后续统一用 MatterPilot 指代这个完整系统。

## 1. 当前项目状态

| 项目维度 | 当前状态 |
| --- | --- |
| 仓库根目录 | 当前 Git 仓库根目录，即 `phase_diagram_agent/` |
| Git 远程 | `git@github.com:Data-farmer-Harry/-_agent.git` |
| 主分支 | `main` |
| 后端环境 | Python 3.12，Conda 环境名 `lammps_agent` |
| 前端技术栈 | React 19、TypeScript、Vite、Tailwind 4 |
| 后端技术栈 | FastAPI、LangGraph 风格编排、Pydantic、SQLite、pycalphad、LAMMPS runtime |
| 当前 benchmark 规模 | 22 份 dataset，390 条 case |
| 当前测试规模 | 47 个后端测试文件；quick gate 当前覆盖 164 个快速用例 |
| 主要验证入口 | `make test-quick`、`make audit-advanced-agent`、`make test-benchmark-gate` |

最近一次本地快速验证结果为 `make test-quick` 通过：后端快速单测 `164 passed`，secret scan 通过，benchmark dataset validate 通过，MaterialsAgentBench freeze lock 通过，前端 `npm run build` 通过。

## 2. MatterPilot 能做什么

MatterPilot 当前围绕材料计算研究的常见链路设计：用户可以用自然语言描述任务，系统先判断任务类型，再把任务交给对应 runtime 或知识服务。它能做相图计算，也能做 LAMMPS 分子动力学，还能处理材料知识问答、相图截图识别、结果解释、任务追踪和后续追问。

| 能力 | 当前实现 |
| --- | --- |
| 自然语言路由 | `SupervisorAgent` 读取用户输入、上传文件和上下文，判断是相图、LAMMPS、识别、普通问答还是混合任务。 |
| 相图计算 | `PhaseDiagramRuntime` 使用 thermo registry 找到 TDB，再通过 pycalphad 做真实计算，保留 result、artifact、accuracy gate 和 provenance。 |
| LAMMPS 计算 | `LammpsRuntime` 支持 request parse、preflight、模板生成、本地 LAMMPS 执行、thermo 解析、OVITO 后处理、质量门和报告生成。 |
| 相图截图识别 | `RecognitionAgent` 支持上传图片识别体系、坐标轴、相区和关键点，也能生成 canvas-only 交互式重建页面。 |
| Thermo RAG | 用于增强 TDB 查库，不替代 deterministic registry execution；exact/alias registry 命中仍优先。 |
| Materials RAG | 用于材料概念、LAMMPS 命令解释、势函数建议、报错诊断和多跳材料知识问答。 |
| 共享记忆 | `SharedMemoryService` 用 SQLite 存储跨 agent 共享事实，支持去重、冲突检测、scope 隔离、embedding cache 和 sqlite-vec dense retrieval。 |
| DAG 编排 | LAMMPS preflight 已接入 DAG executor，支持 asyncio 并发、Semaphore 资源控制、checkpoint 和 replan。 |
| Red/Blue 审查修复 | Red review 从事实性、逻辑一致性、引用质量和物理可信度审查；Blue patch 只允许受控字段修改，并记录 parse audit。 |
| 评测体系 | MaterialsAgentBench 将现有 suite 映射成统一 schema，配合 rule evaluator、Judge contract、bootstrap 和 effect size。 |
| 前端工作台 | React 前端展示聊天、artifact bundle、LAMMPS 信任卡、Red/Blue 审查、trace、系统设置和健康诊断。 |

## 3. 顶层目录结构

```text
phase_diagram_agent/
├── README.md
├── Makefile
├── requirements.txt
├── scripts/
├── docs/
├── backend/
├── frontend/
└── .github/workflows/
```

| 路径 | 作用 |
| --- | --- |
| `README.md` | 当前项目入口文档，负责说明定位、结构、运行方式、测试方式和维护约定。 |
| `Makefile` | 本地和 CI 的统一命令入口，封装 quick/full/live benchmark、secret scan、advanced audit 等流程。 |
| `requirements.txt` | 根目录轻量依赖索引，具体后端依赖以 `backend/requirements/` 为准。 |
| `requirements-portable.txt` | 单文件 Python 依赖清单，适合迁移到另一台电脑或平台时快速安装完整后端能力。 |
| `scripts/` | 项目级脚本，包括 secret scan、benchmark gate 和 advanced-agent migration audit。 |
| `docs/` | 设计文档和路线图，记录架构、RAG schema、RAG production 要点和高级 agent 迁移计划。 |
| `backend/` | FastAPI 后端、agent、runtime、RAG、memory、benchmark、tests 和配置文件。 |
| `frontend/` | React/Vite 前端工作台。 |
| `.github/workflows/` | quick CI、nightly benchmark、live backend gate 的 GitHub Actions 配置。 |

运行态输出默认位于 `backend/outputs/`。这个目录保存 run artifact、memory、logs、benchmark report、vector store 和临时产物，默认不作为源码维护。

## 4. 后端结构

后端是系统核心，入口在 `backend/app/api.py` 和 `backend/app/main.py`。`api.py` 创建 FastAPI app，装配 artifact service、memory store、shared memory service、job worker、agents 和 runtimes；`main.py` 提供 uvicorn 可直接导入的 `app`。

```text
backend/
├── app/
│   ├── agents/
│   ├── core/
│   ├── lammps/
│   ├── materials_rag/
│   ├── orchestration/
│   ├── rag/
│   ├── recognition_reconstruction/
│   ├── recognition_simulator/
│   ├── runtimes/
│   ├── shared_memory/
│   ├── thermo/
│   └── utils/
├── benchmarks/
├── configs/
├── examples/
├── requirements/
└── tests/
```

| 后端模块 | 关键文件 | 当前职责 |
| --- | --- | --- |
| `app/agents/` | `supervisor.py`、`recognition.py`、`compute.py`、`chat.py` | 四个主 agent，负责路由、识别、计算分发和最终回答。 |
| `app/core/` | `artifacts.py`、`llm.py`、`observability.py`、`provenance.py` | 通用基础设施，包括 artifact、LLM client、事件日志、provenance 和执行协议。 |
| `app/thermo/` | `registry.py`、`service.py`、`engine.py`、`rag_*` | 相图计算链路、TDB registry、thermo RAG 和 pycalphad 包装。 |
| `app/lammps/` | `preflight.py`、`runner.py`、`validator.py`、`quality/`、`review/` | LAMMPS 请求校验、预检、执行、质量门、Red/Blue 审查和修复策略。 |
| `app/runtimes/` | `phase_diagram.py`、`lammps.py`、`manager.py`、`telemetry.py` | 真实计算 runtime 和 runtime 能力报告。 |
| `app/orchestration/` | `dag.py`、`executor.py`、`lifecycle.py`、`replan.py`、`fingerprint.py` | DAG 拓扑、asyncio 并发、Semaphore 资源控制、checkpoint、replan 和复用判断。 |
| `app/shared_memory/` | `store.py`、`service.py`、`retrieval.py`、`conflicts.py` | 跨 agent 共享记忆、SQLite 存储、embedding cache、冲突检测和 L1/L2/L3 检索压缩。 |
| `app/materials_rag/` | `service.py`、`retriever.py`、`vector.py`、`context_builder.py` | 材料知识 RAG，增强 ChatAgent 和 LammpsRuntime。 |
| `app/rag/` | `data_manager.py`、`reranker.py`、`sqlite_vector_store.py`、`query_rewrite.py` | RAG 通用管理、sqlite-vec 向量库、reranker 和 query rewrite。 |
| `app/recognition_reconstruction/` | `schema.py`、`validator.py`、`renderer.py`、`vector_trace.py` | 相图截图识别后的 canvas 重建、几何验证和 HTML 渲染。 |
| `app/recognition_simulator/` | `service.py`、`models.py` | 识别结果交互模拟器。 |
| `app/api.py` | FastAPI routes | HTTP API、job queue、artifact 下载、配置管理、diagnostics 和 streaming。 |
| `app/jobs.py` | SQLite-backed queue | 长任务 job 创建、事件流、取消、恢复和结果查询。 |
| `app/mcp_server.py` | MCP sidecar | 把现有 runtime 和 diagnostics 封装成 MCP tools。 |

## 5. 前端结构

前端位于 `frontend/`，主要负责把后端 agent 运行过程以工作台形式展示。它不直接执行科学计算，而是调用后端 API，展示聊天、artifact、trace、配置和健康诊断。

```text
frontend/
├── package.json
├── vite.config.ts
├── index.html
└── src/
    ├── app/
    ├── features/
    │   ├── chat/
    │   ├── result/
    │   ├── settings/
    │   └── trace/
    ├── services/
    ├── shared/
    └── types/
```

| 前端模块 | 当前职责 |
| --- | --- |
| `src/app/AgentWorkbench.tsx` | 工作台主界面，整合聊天、结果、设置和 trace。 |
| `src/features/chat/` | 聊天面板、上传文件、job stream、历史消息和 prompt suggestion。 |
| `src/features/result/` | artifact bundle、LAMMPS 结果、相图结果、Red/Blue 审查、质量门和 provenance 展示。 |
| `src/features/settings/` | 系统设置、本地偏好、LLM/RAG/LAMMPS 配置和诊断入口。 |
| `src/features/trace/` | 展示 agent 运行 trace、event stream 和执行过程。 |
| `src/services/api.ts` | 前端 API client。 |
| `src/types/api.ts` | 与后端响应结构对齐的 TypeScript 类型。 |

## 6. Agent 主链路

当前系统是四 agent 主链路加 service-layer enhancer。它没有把所有能力都拆成独立 agent，而是在稳定主链路上增加记忆、RAG、DAG、审查和评测。

| 组件 | 位置 | 行为 |
| --- | --- | --- |
| `SupervisorAgent` | `backend/app/agents/supervisor.py` | 基于当前消息、资产、记忆和上下文做任务路由。 |
| `RecognitionAgent` | `backend/app/agents/recognition.py` | 处理相图截图识别和可选 HTML 重建。 |
| `ComputeAgent` | `backend/app/agents/compute.py` | 将计算任务分发给 `PhaseDiagramRuntime` 或 `LammpsRuntime`。 |
| `ChatAgent` | `backend/app/agents/chat.py` | 普通问答、结果解释、RAG 回答、follow-up 和 artifact 摘要。 |
| `MemoryStore` | `backend/app/memory.py` | 会话级 short-term / long-term memory。 |
| `SharedMemoryService` | `backend/app/shared_memory/service.py` | 跨 agent 共享事实、冲突检测、检索压缩和 evidence expansion。 |

一次典型聊天请求会进入 `/api/jobs/agent-chat`，后台 worker 执行 graph，前端通过 `/api/jobs/{job_id}/events` 订阅进度。旧的 `/api/agent/chat` 和 `/api/agent/chat/stream` 仍保留作为兼容入口。

## 7. 相图计算链路

相图计算仍以 deterministic registry 和真实 pycalphad 执行为主。RAG 是查库增强层，不直接决定执行。

```text
用户请求
  -> SupervisorAgent
  -> ComputeAgent
  -> PhaseDiagramRuntime
  -> thermo registry / thermo RAG
  -> wrapper code generation
  -> pycalphad + TDB
  -> artifact + accuracy gate + provenance
  -> ChatAgent 总结
```

| 子模块 | 当前说明 |
| --- | --- |
| Thermo registry | `backend/configs/thermo_registry.json`，当前包含 29 个真实可计算体系。 |
| TDB 数据 | `backend/configs/thermo_databases/`，当前仓库内有 17 个数据库文件。 |
| Thermo RAG | `backend/app/thermo/rag_service.py` 等文件，当前有 145 条结构化文档。 |
| 执行 runtime | `backend/app/runtimes/phase_diagram.py`。 |
| accuracy gate | `backend/app/thermo/accuracy.py`。 |
| 产物 | `result.html`、图像、数据、trace、provenance 和 artifact manifest。 |

当用户在相图生成后要求“生成交互式 HTML”时，系统会读取上一轮真实相图结果作为内部识别输入，再生成新的 canvas 重建页面。这个页面是交互模拟器，不是每次拖动滑条都重新运行 pycalphad。

## 8. LAMMPS 链路

LAMMPS runtime 是当前高级 agent 能力最密集的部分。它保留真实本地执行路径，同时增加 preflight DAG、物理质量门、Red/Blue 审查和 legacy rollback。

```text
用户请求
  -> SupervisorAgent
  -> ComputeAgent
  -> LammpsRuntime
  -> request parse / registry / validator
  -> optional preflight DAG
  -> in.lammps generation
  -> local LAMMPS execution or controlled mock mode
  -> thermo parser / physical quality gate
  -> Red/Blue review and bounded repair
  -> OVITO/postprocess artifacts
  -> final report
```

| 能力 | 关键路径 | 当前说明 |
| --- | --- | --- |
| 请求配置 | `backend/app/lammps/config.py` | 支持 LAMMPS command、potentials dir、OVITO、mock fallback、preflight DAG、Red/Blue flag。 |
| 预检 DAG | `backend/app/lammps/preflight.py`、`backend/app/orchestration/` | 使用 DAG plan、async executor 和 Semaphore 控制 network/cpu/simulation 资源。 |
| 执行器 | `backend/app/lammps/runner.py` | 运行本地 LAMMPS，保留 controlled mock fallback。 |
| 物理质量门 | `backend/app/lammps/quality/physics_gate.py` | 检查 thermo rows、step coverage、温度、能量漂移、压力、NaN/Inf、dump 和 log anomaly。 |
| Red/Blue 审查 | `backend/app/lammps/review/` | Red review 给出发现，Blue patch 只能 ADD/DELETE/MODIFY/VERIFY 受控字段。 |
| runtime 主逻辑 | `backend/app/runtimes/lammps.py` | 串联配置、DAG、执行、质量门、审查、修复、artifact 和最终响应。 |

当前 Red/Blue 机制有 feature flag：`LAMMPS_RED_BLUE_REVIEW_ENABLED`。关闭时仍走 legacy review path，便于回滚。preflight DAG 使用 `LAMMPS_PREFLIGHT_DAG_ENABLED` 控制，默认可保持保守。

## 9. DAG、状态机和降级策略

高级编排位于 `backend/app/orchestration/`。这部分把“复杂任务怎么可靠执行”从 runtime 里抽象出来。

| 文件 | 作用 |
| --- | --- |
| `dag.py` | 定义 `DAGNode`、`DAGPlan`、拓扑校验和稳定 topological sort。 |
| `executor.py` | 使用 `asyncio` 并发执行 DAG 节点，使用 `Semaphore` 控制 network/cpu/simulation 资源。 |
| `lifecycle.py` | 定义 queued、planning、preflight、ready、running、reviewing、repairing、completed、terminated 九状态生命周期。 |
| `replan.py` | 根据失败类型决定 Level 1 fallback、Level 2 replan 或 Level 3 partial report。 |
| `fingerprint.py` | 为节点输入、输出和配置生成稳定 hash，用于 checkpoint reuse。 |

三级降级策略的语义是：单个 non-critical 节点失败时可以 Level 1 fallback，批量失败或可修复失败进入 Level 2 replan，全局超时进入 Level 3 partial report。部分结果不能伪装成完整科学成功。

## 10. Memory 与 Shared Memory

项目里有两层记忆。第一层是会话 memory，用来维护当前对话、上传资产、上一轮运行和长期摘要。第二层是 shared memory，用来在 agent 和任务之间复用结构化事实。

| 层级 | 文件 | 存储 | 用途 |
| --- | --- | --- | --- |
| Short-term memory | `backend/app/memory.py` | SQLite 优先，JSON 兼容 | 最近消息、上传资产、识别结果、last run context。 |
| Long-term memory | `backend/app/memory.py` | SQLite 优先，JSON 兼容 | 会话摘要、用户偏好、研究主题、已完成运行摘要和开放问题。 |
| Shared memory | `backend/app/shared_memory/store.py` | SQLite | 跨 agent 共享事实、source refs、raw evidence、versions、conflicts、embedding cache。 |
| Retrieval layer | `backend/app/shared_memory/retrieval.py` | 运行时计算 + embedding cache | query rewrite、BM25、deterministic dense fallback、MMR、TextRank 和 L1/L2/L3 压缩。 |
| Service layer | `backend/app/shared_memory/service.py` | SQLite + sqlite-vec sidecar | 统一写入、检索、冲突记录、evidence expansion 和 working state 构建。 |

Shared memory 写入时会做 canonicalization、exact/normalized dedup 和结构化冲突检测。检索时先做 scope filter，再结合 query rewrite、BM25、dense score、MMR/TextRank 和 raw evidence retention，避免跨会话泄漏。

## 11. RAG 体系

项目当前有两个主要 RAG 方向：thermo RAG 和 materials RAG。它们都支持 BM25、structured lexical scoring、API-capable dense vector retrieval 和 local fallback。真实 embedding API 可以通过 OpenRouter 或 DashScope 兼容接口接入，但 deterministic fallback 必须保留。

| RAG | 位置 | 用途 | 执行约束 |
| --- | --- | --- | --- |
| Thermo RAG | `backend/app/thermo/rag_*` | 辅助 TDB 查库、解释数据库、补充候选。 | registry exact/alias 命中优先，RAG 不直接替代 `.tdb` 执行路径。 |
| Materials RAG | `backend/app/materials_rag/` | LAMMPS 命令、MD 工作流、势函数、材料基础概念、报错诊断。 | 增强 ChatAgent 和 LammpsRuntime，不作为独立 agent。 |
| RAG manager | `backend/app/rag/data_manager.py` | 统一查看文档规模、embedding、BM25、benchmark 状态。 | API 路径为 `/api/rag/manager` 和 `/api/rag/manager/search`。 |
| sqlite vector store | `backend/app/rag/sqlite_vector_store.py` | sqlite-vec 向量索引和 cosine KNN。 | 以 content digest 和 embedding signature 判断是否重建。 |
| query rewrite | `backend/app/rag/query_rewrite.py` | 改写材料检索 query，提高召回稳定性。 | 不改变用户原始请求的科学约束。 |

Materials RAG 当前包含 Wikipedia 语料 `backend/configs/materials_rag_wikipedia.jsonl`，覆盖 55 个材料主题和 109 个知识块。可重复构建命令是：

```bash
cd backend
conda run -n lammps_agent python examples/build_wikipedia_materials_rag.py --resume --allow-failures
```

## 12. 相图识别与交互重建

相图截图识别不是主计算链路的替代品。它用于理解用户上传图片，也可以在用户明确要求 HTML/重建时生成交互式 canvas 页面。

| 模块 | 说明 |
| --- | --- |
| `backend/app/agents/recognition.py` | 识别任务入口，输出结构化识别结果。 |
| `backend/app/recognition_reconstruction/schema.py` | 重建 schema。 |
| `backend/app/recognition_reconstruction/validator.py` | 坐标轴、plot region、关键点和几何约束校验。 |
| `backend/app/recognition_reconstruction/vector_trace.py` | 从上传图像提取结构化 primitive。 |
| `backend/app/recognition_reconstruction/renderer.py` | 输出 canvas-only HTML，不直接嵌入原始图片作为底图。 |
| `backend/app/recognition_simulator/` | 构建识别模拟器模型和服务。 |

当前原则是：普通识别请求只返回 recognition result 和解释；只有用户显式要求“交互式 HTML / 重构 / result.html”时才生成独立 HTML artifact。

## 13. API 入口

后端 API 由 `backend/app/api.py` 提供。常用端口是后端 `http://127.0.0.1:8000`，前端 `http://127.0.0.1:5174`。

| API 组 | 路径示例 | 用途 |
| --- | --- | --- |
| 健康检查 | `/healthz`、`/api/health` | 基础服务探活。 |
| Thermo | `/api/thermo/registry`、`/api/thermo/rag/search` | TDB registry 和 thermo RAG 查询。 |
| Materials RAG | `/api/materials-rag/search` | 材料知识和 LAMMPS 知识检索。 |
| RAG manager | `/api/rag/manager`、`/api/rag/manager/search` | 查看 RAG 文档、embedding 和检索状态。 |
| Diagnostics | `/api/system/diagnostics` | 系统健康检查和依赖状态。 |
| LAMMPS config | `/api/lammps/registry`、`/api/config/lammps` | LAMMPS registry 与运行配置。 |
| LLM config | `/api/config/llm` | LLM 和 embedding 配置读取/更新。 |
| Runs | `/api/runs`、`/api/runs/{run_id}`、`/api/runs/{run_id}/result` | 运行记录、结果 HTML 和 artifact 下载。 |
| Conversations | `/api/conversations/{conversation_id}`、`/memory-profile` | 会话快照和 memory profile。 |
| Jobs | `/api/jobs/agent-chat`、`/api/jobs/{job_id}/events`、`/api/jobs/{job_id}/resume` | 长任务创建、事件订阅、恢复和取消。 |
| Agent | `/api/agent/chat`、`/api/agent/chat/stream`、`/api/agent/prompt-suggestion` | 兼容同步/流式聊天和 prompt suggestion。 |

## 14. MCP sidecar

项目提供 MCP server 作为旁路接口，不接管前端主 API，也不重写核心计算内核。入口是 `backend/app/mcp_server.py`。

```bash
cd backend
conda run -n lammps_agent python -m app.mcp_server
```

| MCP tool | 说明 |
| --- | --- |
| `phase_diagram.run` | 自然语言相图计算。 |
| `phase_diagram.run_structured` | 结构化相图请求，绕过部分 parse。 |
| `phase_diagram.registry_search` | 查询 thermo registry。 |
| `phase_diagram.rag_search` | 查询 thermo RAG。 |
| `lammps.run` | 自然语言 LAMMPS 任务。 |
| `lammps.run_structured` | 结构化 LAMMPS 请求。 |
| `lammps.registry_get` | 查询 LAMMPS registry。 |
| `system.diagnostics` | 获取系统诊断信息。 |

## 15. Benchmark 与评测

Benchmark 位于 `backend/benchmarks/`。当前它已经不只是数据草稿，而是包含 dataset builder、runner、MaterialsAgentBench adapter、evaluator、statistics、freeze lock、gate 脚本和测试。

| 资产 | 当前状态 |
| --- | --- |
| dataset 数量 | 22 |
| case 总数 | 390 |
| MaterialsAgentBench development | 140 |
| MaterialsAgentBench frozen_test | 250 |
| frozen split hash | 由 `backend/benchmarks/datasets/materials_agent_bench.freeze.json` 锁定 |
| deterministic suite | 19 个 suite 已纳入 `run-all` |
| threshold checks | 当前完整报告中 57 个 threshold checks 全部通过 |
| Judge 层 | 默认离线 contract / calibration，不调用线上 LLM；live provider 必须显式开启。 |

主要 suite 覆盖 routing、rag_recall、phase_execution、lammps_contract、lammps_e2e、lammps_quality、lammps_red_blue、review_json_fallback、orchestration、judge_calibration、lammps_recovery、recognition、memory、shared_memory、memory_conflict、context_compression、materials_multihop 和 mcp。

## 16. 环境安装

后端统一使用 Python 3.12 的 `lammps_agent` Conda 环境。仓库不再维护 `.venv`。

新建环境时使用：

```bash
conda env create -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r backend/requirements/all.txt
conda activate lammps_agent
```

如果希望迁移时只看一个 Python 依赖文件，可以使用根目录的 `requirements-portable.txt`：

```bash
conda create -n lammps_agent python=3.12 pip
conda activate lammps_agent
python -m pip install -r requirements-portable.txt
```

如果本机已经有 `lammps_agent` 环境，使用：

```bash
conda env update -n lammps_agent -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r backend/requirements/all.txt
```

分层依赖说明见 `backend/requirements/README.md`。其中 `base.txt` 是后端运行依赖，`dev.txt` 是测试工具，`visualization.txt` 是可选可视化依赖，`all.txt` 汇总项目常用依赖。

前端依赖不写入 Python requirements 文件，而是由 `frontend/package-lock.json` 锁定。迁移到新机器后，进入 `frontend/` 执行 `npm ci` 即可恢复前端依赖。LAMMPS、ffmpeg、EAM potential 文件和可选外部 `ovitos` 属于宿主机工具，也不会由 pip 自动安装。

## 17. 本地运行

后端运行：

```bash
cd backend
conda run -n lammps_agent python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端运行：

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5174
```

前端生产构建：

```bash
cd frontend
npm run build
```

配置文件优先级是进程环境变量、中央 JSON、旧 `.env` 回退、代码默认值。中央 JSON 位于 `backend/configs/llm_config.json`。公开 API 和前端诊断只返回 key 是否存在与掩码值，不返回明文 key。

LLM 调用现在带有一层可选的动态路由。业务 agent 仍然正常调用 `chat_text`、`chat_json` 或多模态接口，`LLMClient` 会根据 prompt 长度、结构化输出要求、LAMMPS/代码修复/审查、RAG/文献、视觉识别等信号，自动选择 `fast`、`balanced`、`strong` 或 `vision` 档位。没有额外配置时，所有档位都会继承 `backend/configs/llm_config.json` 里的当前模型，所以原功能不变。

如果要启用多模型分流，复制样例文件：

```bash
cp backend/configs/llm_routing.example.json backend/configs/llm_routing.json
```

然后按需修改各档位的 `api_base_url`、`model`、`timeout_seconds` 和 `max_tokens`。`backend/configs/llm_routing.json` 已被 `.gitignore` 忽略，不会上传；真实 API key 仍放在 `backend/.env`、`backend/configs/.env`、进程环境变量或密钥管理器中。常见用法是：

| 档位 | 典型任务 |
| --- | --- |
| `fast` | 短问答、记忆压缩、prompt 推荐。 |
| `balanced` | Supervisor 路由、query rewrite、RAG/文献上下文整理。 |
| `strong` | LAMMPS 请求解析、代码生成、修复、Red/Blue 审查、评测判断。 |
| `vision` | 相图截图识别、多模态图像解析。 |

当某一档 provider 调用失败时，路由层可以按 `fallbacks` 自动升级，例如 `fast → balanced → strong`。如果 fallback 档位最终解析出的模型和 provider 与原档位相同，则不会重复发送同一个请求。

如果要训练一个小型神经网络做动态路由推荐，可以运行：

```bash
conda run -n lammps_agent python backend/benchmarks/train_llm_route_mlp.py
```

训练脚本会构造 hard synthetic route dataset，包含 clean、mixed、adversarial、long_noise 四类难度样本，例如 LAMMPS 概念解释与 LAMMPS 修复的区分、RAG 检索代码文档与代码修复的区分、文字里出现 image 但没有上传图像、真实多模态图像识别等边界情况。脚本会训练一个单隐藏层 MLP，并把本地产物写到 `backend/outputs/llm_route_mlp/`：

| 文件 | 作用 |
| --- | --- |
| `model.json` | 可被路由层加载的小型 MLP 权重。 |
| `metrics.json` | train/test accuracy、precision、recall、F1、confusion matrix、log loss 等指标。 |
| `report.md` | 人类可读的训练报告。 |
| `train.jsonl` / `test.jsonl` / `probe.jsonl` | 本次构造出的合成训练、测试和手写探针样本。 |

当前默认训练规模约 3266 条样本，其中 mixed/adversarial/long_noise 超过一半；本地最近一次训练的 holdout test macro-F1 约 0.9958，probe set macro-F1 为 1.0000。这个结果仍然只是 synthetic baseline，适合冷启动；后续应逐步用真实调用 telemetry 替换或混合训练。

在 `backend/configs/llm_routing.json` 中设置 `learned_policy.enabled=true` 后，神经网络有两种使用方式：`shadow` 只记录推荐、不改变最终路由；`guarded` 会在置信度超过阈值后接管，但仍受 `capability_min_tiers` 保护，例如 LAMMPS 修复、审查、judge 不会被降级到低风险档位。

## 18. 常用 Makefile 命令

| 命令 | 用途 |
| --- | --- |
| `make test-quick` | 快速本地/PR gate，包含后端快速单测、secret scan、dataset validate、MaterialsAgentBench freeze check 和前端 build。 |
| `make test-secret-scan` | 扫描可提交文件中的高置信 API key、token 和 private path。 |
| `make test-materials-bench-freeze` | 校验 MaterialsAgentBench frozen split 未被同版本误改。 |
| `make test-full` | 全量后端 pytest、benchmark validate、deterministic run-all、benchmark gate 和前端 build。 |
| `make test-benchmark-gate` | 跑 deterministic benchmark smoke 并检查 threshold、critical failure 和可选 baseline regression。 |
| `make audit-advanced-agent` | 审计 roadmap、能力面、可选 deterministic report、dataset manifest 和 freeze lock。 |
| `make record-benchmark-baseline` | 记录当前 deterministic benchmark baseline。 |
| `make test-lammps-real` | LAMMPS 重点长测，显式开启 `--real-lammps`。 |
| `make test-orchestration` | 单独验证 DAG、Semaphore、replan 和 global timeout。 |
| `make test-live-backends` | 真实 embedding、reranker、Judge backend gate，不依赖前端 API。 |
| `make test-live` | API-dependent live benchmark，需要先启动后端。 |
| `make record-lammps-baseline` | 记录 LAMMPS contract baseline JSON 和 Markdown。 |
| `make clean-outputs-dry-run` | 预览将被删除的运行产物、缓存和构建产物。 |
| `make clean-local` | 删除 `backend/outputs`、pytest cache、Python cache、frontend dist、`.DS_Store` 等本地生成文件。 |
| `make clean-local-with-node-modules` | 在 `make clean-local` 基础上同时删除 `frontend/node_modules`，之后需要重新运行 `npm install`。 |

默认 local/CI gate 会清空本机 API key 相关环境变量，避免真实 provider 污染 deterministic 测试。真实 API 测试必须使用 `make test-live-backends` 或 `make test-live` 显式触发。`make audit-advanced-agent` 默认允许本地清理后缺少 ignored benchmark report；如果要强制审计某份 deterministic report，可直接运行 `conda run -n lammps_agent python scripts/advanced_agent_audit.py --require-benchmark-report --benchmark-report <report.json>`。

## 19. CI 与安全

GitHub Actions 位于 `.github/workflows/`。

| Workflow | 触发方式 | 行为 |
| --- | --- | --- |
| `quick-ci.yml` | pull request、push、manual | 运行 `make test-quick`。 |
| `nightly-benchmark.yml` | schedule、manual | 运行 `make test-full`，可选真实 LAMMPS gate，并上传 benchmark artifact。 |
| `live-backends.yml` | manual | 运行 `make test-live-backends`，可选 API live gate；OpenRouter 和 DashScope key 只从 GitHub Secrets 注入。 |

安全相关脚本位于 `scripts/secret_scan.py`。它会扫描可提交文件，避免 API key、token 和私人路径进入仓库。外部 provider key 应放在本地 `.env`、中央配置或 GitHub Secrets 中，不应写进源码。

## 20. 文档地图

| 文档 | 作用 |
| --- | --- |
| `README.md` | 项目入口、结构总览、运行命令和维护约定。 |
| `docs/ARCHITECTURE.md` | 架构说明和主要组件关系。 |
| `docs/ADVANCED_AGENT_MIGRATION_ROADMAP.md` | 高级 agent 改造路线图，包含 DAG、Red/Blue、shared memory、benchmark 和 CI。 |
| `docs/RAG_PRODUCTION.md` | RAG 生产化配置、embedding、reranker 和回退策略。 |
| `docs/THERMO_RAG_SCHEMA.md` | Thermo RAG 文档 schema 和构建规范。 |
| `backend/benchmarks/README.md` | benchmark suite、指标、数据集和 gate 说明。 |
| `backend/requirements/README.md` | Conda 环境、pip 依赖和可选外部工具说明。 |

## 21. 运行态产物

运行产物默认集中在 `backend/outputs/`。这里通常包括 run 目录、artifact、memory SQLite、job SQLite、RAG vector store、logs、benchmark report 和 baseline report。源码提交时应避免把运行态大文件提交进 Git。

如果本地目录变肥，先运行 `make clean-outputs-dry-run` 看清理范围，再用 `make clean-local` 删除可重建产物。若需要把前端依赖也清掉以释放空间，用 `make clean-local-with-node-modules`；之后前端构建前必须在 `frontend/` 重新执行 `npm install`。

| 产物 | 默认位置 |
| --- | --- |
| run artifacts | `backend/outputs/runs/` |
| memory | `backend/outputs/memory/` |
| job queue | `backend/outputs/jobs/` |
| observability logs | `backend/outputs/logs/events.jsonl` |
| RAG vector store | `backend/outputs/rag/vector_store.sqlite3` |
| benchmark report | `backend/outputs/benchmarks/` |
| LAMMPS contract baseline | `backend/outputs/baselines/lammps_contract/` |

## 22. 当前开发约定

1. 真实科学计算链路优先保持稳定。新增能力先作为旁路增强或 feature flag，不直接替换已经验证的主链路。

2. 相图执行以 `pycalphad + TDB` 为真实内核。Thermo RAG 只能增强查库和解释，不能越过 deterministic registry gate。

3. LAMMPS 真实模式和 mock fallback 必须严格区分。真实模式解析失败不能伪装成 mock 成功，synthetic thermo 不能标记为科学成功。

4. Red/Blue 修复只能操作受控结构化请求字段。用户锁定约束、危险字段和原始脚本不能被 LLM 任意改写。

5. Shared memory 必须保留 scope filter。跨会话、跨用户或跨任务的记忆复用需要明确 scope，benchmark 要验证泄漏率为 0。

6. Benchmark、freeze lock、advanced audit 和 secret scan 是代码可信度的一部分。修改 dataset、threshold、feature flag 或核心能力接线时，应同步更新测试和文档。

7. 仓库根目录就是 `phase_diagram_agent`。外层 `相图计算` 目录不再作为 Git 仓库使用，后续请在 `phase_diagram_agent` 内执行 `git status`、`git commit` 和 `git push`。

## 23. 快速恢复上下文

如果之后需要快速接手这个项目，推荐按以下顺序读取：

1. 先读 `README.md`，理解 MatterPilot 的整体结构和运行方式。

2. 再读 `docs/ARCHITECTURE.md`，确认 agent、runtime、memory、artifact 和 API 的关系。

3. 如果要继续高级 agent 改造，读 `docs/ADVANCED_AGENT_MIGRATION_ROADMAP.md` 和 `scripts/advanced_agent_audit.py`。

4. 如果要改 RAG，读 `docs/RAG_PRODUCTION.md`、`docs/THERMO_RAG_SCHEMA.md`、`backend/app/materials_rag/`、`backend/app/thermo/rag_*` 和 `backend/app/rag/`。

5. 如果要改 LAMMPS，读 `backend/app/runtimes/lammps.py`、`backend/app/lammps/`、`backend/app/orchestration/` 和对应测试文件。
