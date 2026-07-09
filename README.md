# Phase Diagram Agent

一个面向材料科研场景的多能力 Agent 项目，当前主线包含两条真实本地计算链路：

- 相图计算：`pycalphad + TDB`
- 分子动力学：`LAMMPS + OVITO`

项目当前以**前后端分离**形式组织：

```text
phase_diagram_agent/
├── frontend/      # 前端界面
├── backend/       # 后端、agent、runtime、registry、tests
├── docs/          # 结构说明和专项设计文档
└── README.md
```

## Python 环境

后端统一使用 Python 3.12 的 `lammps_agent` Conda 环境，不再在仓库内保留 `.venv`。

```bash
conda env create -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r backend/requirements/all.txt
conda activate lammps_agent
```

如果本机已经有 `lammps_agent` 环境，改用：

```bash
conda env update -n lammps_agent -f backend/requirements/environment.yml
conda run -n lammps_agent python -m pip install -r backend/requirements/all.txt
```

分层依赖、可选 OVITO 可视化依赖与外部 LAMMPS/ffmpeg 要求见 `backend/requirements/README.md`。

## 项目定位

这个项目不是单纯的聊天机器人，而是一个面向材料科研任务的 agent 工作台：

- 用 LLM 做真实路由和任务理解
- 用本地工具做真实计算
- 保留会话 memory、artifact、follow-up
- 保持结果可信度、环境诊断和可交接性
- 后端 benchmark 现在同时覆盖：
  - 内生回归样本
  - 外部开放论文相图识别 live 基准

当前默认外部模型配置：

- `qwen3.5-plus`
- `enable_thinking = false`
- RAG embedding 默认配置：
  - OpenRouter endpoint：`https://openrouter.ai/api/v1`
  - OpenRouter 模型：`qwen/qwen3-embedding-8b`
  - 若改用 DashScope / 百炼官方 Qwen3-Embedding，模型名通常为 `text-embedding-v4`

## 当前主结构

### 前端

- 目录：`frontend/`
- 负责：
  - 聊天 UI
  - 截图/文件上传
  - 相图与 LAMMPS artifact 展示
  - 系统设置与环境诊断面板

### 后端

- 目录：`backend/`
- 负责：
  - 统一 API
  - LangGraph 编排
  - 4 agent
  - memory 模块
  - thermo registry / thermo RAG
  - LAMMPS runtime
  - artifact 与 trace 管理

### 工程基础设施

当前后端额外包含 4 个横切模块：

- Agent Protocol
  - 文件：`backend/app/core/agent_protocol.py`
  - 作用：把 agent 间状态转移记录成稳定 JSON envelope，便于 trace、回放和 MCP 化。
- RAG Data Manager
  - 文件：`backend/app/rag/data_manager.py`
  - API：`/api/rag/manager`、`/api/rag/manager/search`
  - 作用：统一查看 materials RAG / thermo RAG 的文档规模、embedding、BM25、benchmark 状态。
  - 向量库：`SQLite + sqlite-vec`，默认路径为 `backend/outputs/rag/vector_store.sqlite3`。
  - 检索：保留 structured lexical + BM25，dense candidate 由 sqlite-vec cosine KNN 执行后加权融合。
  - 索引复用：按语料 content digest 和 embedding signature 判断失效；进程重启不会重复 embedding 未变的文档。
  - Wikipedia 语料：`backend/configs/materials_rag_wikipedia.jsonl`，当前包含 55 个材料主题、109 个知识块，每条保留来源 URL、revision 和 CC BY-SA 归属。
  - 可重复抓取：`cd backend && conda run -n lammps_agent python examples/build_wikipedia_materials_rag.py --resume --allow-failures`。
- Provenance / Reproducibility
  - 文件：`backend/app/core/provenance.py`
  - 输出：每次 run 自动写入 `provenance.json`
  - 作用：记录模型配置、RAG 配置、artifact hash、trace 工具链和运行环境。
- Artifact Lifecycle
  - 输出：每次 run 自动写入 `artifact_manifest.json`
  - API：`/api/artifacts/inventory`、`/api/artifacts/cleanup`
  - 作用：统计 run artifact 占用，按保留最近 N 次 / 最大天数做 dry-run 或显式清理。
- Observability
  - 文件：`backend/app/core/observability.py`
  - 输出：`backend/outputs/logs/events.jsonl`
  - 作用：用统一 `request_id` 串联 request、job、run、agent step 和 artifact。
- Config / Health Diagnostics
  - 配置中心：`backend/configs/llm_config.json`
  - API：`/api/system/diagnostics`
  - 前端入口：系统偏好设置 -> 系统健康检查
  - 作用：集中管理 LLM、RAG embedding、LAMMPS、OVITO、artifact retention 等配置，并一键检查 chat/vision、embedding、RAG、TDB、SQLite memory、artifact、日志和 benchmark 状态。
  - 配置优先级：进程环境变量 > 中央 JSON > 旧 `.env` 回退 > 代码默认值。
  - 模型能力通过 `llm_supports_chat`、`llm_supports_vision`、`llm_supports_embedding` 显式声明，诊断不会再根据 API Key 猜测能力。
  - 中央 JSON 可以保存本机明文 key；公开 API、前端和诊断仅返回是否设置与掩码值。
- Runtime Telemetry / Manager
  - 文件：`backend/app/runtimes/telemetry.py`、`backend/app/runtimes/manager.py`
  - API：`/api/runtimes/manager`
  - 作用：统一展示相图和 LAMMPS runtime 的能力、依赖、配置建议；每次 run 会写入 `runtime_profile`。
- Job Queue / Worker
  - 文件：`backend/app/jobs.py`
  - API：`/api/jobs/agent-chat`、`/api/jobs/{job_id}/events`、`/api/jobs/{job_id}/result`
  - 作用：长任务先进入 SQLite-backed queue，再由后台 worker 执行真实 4-agent 链路；前端优先订阅 job event stream，旧同步/流式接口保留为兼容回退。
- Memory Profile
  - API：`/api/conversations/{conversation_id}/memory-profile`
  - 作用：明确展示 short-term / long-term memory 的存储、容量、压缩和检索状态。
- Benchmark
  - 文件：`backend/benchmarks/run_benchmarks.py`
  - 命令：`cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run-all`
  - 作用：固化路由准确率、RAG 召回率、相图成功率、LAMMPS artifact 完整率、平均耗时等指标。
- Frontend Smoke
  - 文件：`backend/examples/frontend_health_check_smoke.mjs`
  - 作用：通过 Chrome CDP 打开前端，进入系统偏好设置，触发系统健康检查，确认诊断卡片真实渲染。

## 当前 4-agent 架构

- `SupervisorAgent`
  - 读取用户输入和上下文
  - 用 LLM 做真实任务路由
- `RecognitionAgent`
  - 处理截图识别
  - 输出结构化识别结果
  - 当用户只做截图识别时，额外生成一个“交互式识别模拟器”HTML 结果
- `ComputeAgent`
  - 分发到：
    - `PhaseDiagramRuntime`
    - `LammpsRuntime`
- `ChatAgent`
  - 负责普通问答、结果解释、follow-up

### Memory 设计

当前不是 `Memory Agent`，而是**双层 memory module**：

- `short-term memory`
  - 最近消息
  - 上传资产
  - recognition 结果
  - `last_run_context`
- `long-term memory`
  - 会话长期摘要
  - 重点研究主题
  - 持久化事实 / 已完成运行摘要
  - 开放问题

当前实现是在不破坏现有 4-agent 主链路的前提下，把 memory 升级为：

- `state`
- `persistence`
- `summary`
- `short-term + long-term compaction`
- `long-term retrieval`

memory 相关文件：

- `backend/app/state.py`
- `backend/app/memory.py`
- `backend/app/graph.py`

memory 持久化：

- SQLite 优先：`backend/outputs/memory/memory.sqlite3`
- JSON 兼容备份：`backend/outputs/memory/short_term/` 与 `backend/outputs/memory/long_term/`

长期记忆的压缩策略：

- 默认走 heuristic compaction
- 如果 LLM 可用，会尝试 `llm_compaction`
- 但不会因为 LLM 不可用而阻塞主流程
- 会额外提取：
  - `user_preferences`
  - `retrieval_hints`
- 当前长期记忆检索已增强：
  - 中文材料名、英文体系名、元素符号互相对齐
  - 会把与当前查询最相关的长期要点注入 `ChatAgent`
  - summary 会做长度控制，避免 prompt suggestion 溢出

当前 memory 已明确分层：

- `ShortTermMemoryStore`
  - 保留最近对话、上传资产、上一轮运行上下文、识别结果。
  - 默认保留最近 20 条消息和 6 个上传资产。
- `LongTermMemoryStore`
  - 保留压缩后的研究事实、偏好、已完成运行摘要、开放问题和检索提示。
  - 默认 heuristic compaction，LLM 可用时尝试 LLM compaction。

## 当前相图链路

相图生成保留真实计算路径：

1. `SupervisorAgent` 路由到相图生成
2. `ComputeAgent` 进入 `PhaseDiagramRuntime`
3. thermo registry 查库
4. LLM 生成薄 wrapper
5. 本地 Python 执行 wrapper
6. `pycalphad + TDB` 真计算
7. review / repair
8. accuracy gate

相图 runtime 现在额外记录：

- `runtime_profile`
  - runtime 名称
  - 执行耗时
  - step 数 / failed step 数
  - artifact 数
  - tool chain
  - trust level
  - termination reason

当用户在相图生成后继续要求“生成交互式 HTML / 交互式页面”时，`ChatAgent` 会先读取上一轮真实 phase `result.html` 中的相图图像，仅将其作为内部识别 / tracing 输入，再生成一个新的交互模拟器 HTML，并直接作为当前聊天轮 artifact 渲染。最终页面不会直接回显上一轮原始图像；展示的是新生成的 `HTML + canvas` 结构化重建结果。该模拟器包含温度/压强滑条和共晶点/关键点几何投影，但它不是每次拖动都重新运行 pycalphad 的真实热力学求解。

当前已接入的 TDB 体系见：

- `backend/configs/thermo_registry.json`

## 识别交互模拟器（新增）

当前识别链路已经不是“只返回结构化 JSON”了。

当用户上传相图截图并触发：

- `recognition.analyze`

时，`RecognitionAgent` 现在会完成两件事：

1. 默认先识别图片中的体系、坐标轴、相区和关键点
2. 如果用户显式要求 `交互式 HTML / 重构 / result.html`，再基于识别结果生成一个独立 HTML artifact

也就是说，普通“识别 / 讲解 / 解释图片”请求默认停留在：

- recognition result
- ChatAgent explanation

只有显式 HTML 请求才会进入重建页。

这个 HTML 结果是一个**模拟重建面板**，不是新的热力学求解结果。它提供：

- 自生成的 HTML 相图重建视图
- 基于上传图片提取的结构化 canvas primitive 重建
- 温度滑条
- 压强因子滑条
- 随滑条变化而更新的相界/共晶点模拟投影
- 识别来源、警告和使用说明

实现原则：

- 新功能归属于 `RecognitionAgent`
- 不修改真实 `pycalphad + TDB` 链路
- 只在 `recognition.analyze` 纯识别场景启用
- `mixed.request` 仍然保留“识别后进入真实相图计算”的主路径
- 后端当前采用确定性重建流水线：
  - `schema -> validator -> curve fitting -> HTML renderer`
- 当上传原图可用时：
  - 原图只参与识别、plot window 推断和结构化 primitive 提取
  - 最终展示页不会直接嵌入原始 `<img>`，也不会做像素重绘底图
  - 而是直接输出自生成的 `HTML + canvas` 重建结果
  - 当前高保真重建主线采用：
    - `quantized colors`
    - `merged rect runs`
    - `deterministic canvas renderer`
  - 具体实现在：`backend/app/recognition_reconstruction/`

当前 recognition 回归基准当前覆盖两类检查：

- 几何 contour accuracy：
  - `Al-Ni`
  - `Al-Cu`
  - `Pb-Sn`
- generated canvas render regression：
  - `Al-Ni`
  - `Al-Cu`
  - `Pb-Sn`

当前已做外部公开相图回归基准：

- `Al-Ni`
- `Al-Cu`
- `Pb-Sn`

## 当前 thermo RAG 定位

项目当前有 `thermo RAG`，但它不是执行主链路，而是**查库增强层**：

- exact/alias registry 命中优先
- exact miss 才进入 thermo RAG
- RAG 只负责召回候选和补充数据库知识
- 真执行仍然是：
  - `registry card -> .tdb path -> pycalphad`
- 当前 RAG 已升级为：
  - `BM25 sparse retrieval`
  - `structured lexical scoring`
  - `API-capable dense vector retrieval`
  - 向量层只负责扩召回 / 重排，不单独决定执行
  - BM25 作为 sparse 证据参与融合，但 auto-select 仍保留 lexical gate
  - 默认优先尝试：
    - `llm_api + qwen/qwen3-embedding-8b`
  - 当 embedding API 未配置、不可用或瞬时失败时，会自动回退：
    - `local_hash`
  - 文档索引与查询向量按 embedding 配置签名保持一致，避免不同向量空间混用

RAG 相关文件：

- `backend/app/thermo/rag_models.py`
- `backend/app/thermo/rag_index.py`
- `backend/app/thermo/rag_retriever.py`
- `backend/app/thermo/rag_service.py`
- `backend/app/thermo/rag_vector.py`
- `backend/app/rag/data_manager.py`

## 当前 Materials RAG 定位

项目现在还有一个面向材料知识和 LAMMPS 使用场景的 `materials RAG`。它不是新 Agent，而是 service-layer knowledge enhancer，用来增强现有 `ChatAgent` 和 `LammpsRuntime`：

- LAMMPS 命令解释
- MD 工作流建议
- 势函数选择
- LAMMPS 报错诊断
- 材料基础概念
- 相图 / 热力学概念

当前检索方式：

- BM25 sparse retrieval
- structured lexical scoring
- API-capable dense vector retrieval
- 默认优先尝试：
  - `llm_api + qwen/qwen3-embedding-8b`
- 当 embedding API 不可用时自动回退：
  - `local_hash`

调试接口：

- `GET /api/materials-rag/search?q=fix%20nvt&top_k=5`

召回评测脚本：

- `cd backend && conda run -n lammps_agent python benchmarks/run_rag_recall.py --require-remote`
- 输出：`backend/outputs/rag_recall/latest.json`

相关文件：

- `backend/app/materials_rag/`
- `backend/configs/materials_rag_documents.jsonl`

## 当前 LAMMPS 链路

LAMMPS 生成保留真实本地执行路径：

1. `SupervisorAgent` 路由到 LAMMPS 计算
2. `ComputeAgent` 进入 `LammpsRuntime`
3. request parse / registry / validator
4. 生成 `in.lammps`
5. 本地运行 LAMMPS
6. OVITO 后处理
7. 返回 plot / report / gif / mp4 / 轨迹文件

当前主线代码位置：

- `backend/app/runtimes/lammps.py`
- `backend/app/lammps/`

顶层历史 `lammps/` 目录已移除，避免和当前主线实现混淆。

## 当前 MCP Server

项目当前已经提供一个旁路 MCP server，用来把现有后端能力包装成标准化工具接口，而不改写真实计算内核。

- 入口：`backend/app/mcp_server.py`
- 运行方式：
  - `cd backend && conda run -n lammps_agent python -m app.mcp_server`

当前已暴露的 MCP tools：

- `phase_diagram.run`
- `phase_diagram.run_structured`
- `phase_diagram.registry_search`
- `phase_diagram.rag_search`
- `lammps.run`
- `lammps.run_structured`
- `lammps.registry_get`
- `system.diagnostics`

当前 MCP 设计原则：

- MCP 是 sidecar server
- 不接管前端主 API
- 只封装现有 runtime / registry / diagnostics
- 真实执行内核仍然是：
  - `pycalphad + TDB`
  - `LAMMPS + OVITO`
- `run_structured` 已接入：
  - 用户入口仍然可以是自然语言
  - 但系统内部和 MCP client 已可以直接传结构化请求，绕过 request-parse 这一步

## 当前 Benchmark 资产

项目当前已经开始收口后端 benchmark 资产，目录在：

- `backend/benchmarks/`

当前定位：

- 用统一 taxonomy 评估：
  - routing
  - parsing gold set
  - execution
  - memory follow-up
  - MCP contract
- 数据集会以 `jsonl` 形式保存在：
  - `backend/benchmarks/datasets/`
- 当前提供：
  - dataset builder
  - benchmark runner
  - 资产校验测试

说明：

- benchmark 这条线已经不只是设计文档，当前 builder / runner / datasets / tests 都已落地
- 当前已生成：
  - `22` 份 benchmark dataset
  - `390` 条 benchmark case
- 当前已通过：
  - `cd backend && conda run -n lammps_agent python benchmarks/build_datasets.py`
  - `cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py validate`
  - `cd backend && conda run -n lammps_agent python benchmarks/build_materials_agent_bench.py --output-dir /tmp/materials_agent_bench_quick_ci --summary-only`
  - `make test-materials-bench-freeze`
  - `make test-quick`
- 当前 `MaterialsAgentBench` adapter 会把旧 suite 映射成统一 schema，并用 `backend/benchmarks/datasets/materials_agent_bench.freeze.json` 锁定 390 条 case / 250 条 frozen case 的 frozen split hash 与数据防泄漏结果
- 当前 Judge 层先落地为离线 contract / calibration benchmark：验证 blinded input、JSON fallback、hard-gate 不可覆盖、drift 检查与后端能力矩阵，不会调用线上 LLM 或写入 API key
- 更重的完整 benchmark 可继续用 `make test-full` 或单独跑 `backend/benchmarks/run_benchmarks.py run-all`
- deterministic benchmark 默认会让 `lammps_contract` / `lammps_e2e` 使用 mock LAMMPS runtime，避免本机真实 LAMMPS 执行时间污染快速 CI；需要真实 LAMMPS 时使用 `--real-lammps` 或 `make test-lammps-real`

## 测试入口

根目录已提供 Makefile，默认使用 Python 3.12 的 `lammps_agent` Conda 环境，不会安装依赖：

```bash
# 快速：每次提交/本地改动后
make test-quick

# 单独扫描可提交文件中的 API key/token/私人路径
make test-secret-scan

# 单独验证 MaterialsAgentBench frozen split 未被同版本误改
make test-materials-bench-freeze

# 完整：本地或 CI
make test-full

# 固化 benchmark gate：threshold / critical / baseline regression 失败会非零退出
make test-benchmark-gate

# 汇总审计高级 agent 改造证据：roadmap、能力面、deterministic report、MaterialsAgentBench freeze
make audit-advanced-agent

# 记录当前 deterministic benchmark baseline；默认 BENCHMARK_LIMIT=1 是 smoke baseline
make record-benchmark-baseline

# LAMMPS 重点回归：nightly / 本地长测，显式开启 --real-lammps
make test-lammps-real

# 单独验证 DAG / semaphore / replan / global timeout
make test-orchestration

# 记录 3 个 LAMMPS contract case 的耗时、run_mode 和 artifact baseline
make record-lammps-baseline

# 真实 embedding/reranker/Judge backend：不依赖前端 API，适合手动 live gate
make test-live-backends

# 只验证 live backend gate wiring，不触网
make test-live-backends LIVE_BACKENDS=0

# 真实 API / live benchmark：手动触发，需要先启动后端
make test-live API_BASE=http://127.0.0.1:8000
```

`make test-quick` 当前覆盖：

- 后端 deterministic unit/schema/evaluator/statistics 快速单测
- secret scan：检查可提交文件中的 API key/token/private path
- `benchmarks/run_benchmarks.py validate`
- MaterialsAgentBench schema/freeze manifest 临时构建到 `/tmp/materials_agent_bench_quick_ci`
- MaterialsAgentBench 仓库级 freeze lock 校验
- `frontend` 的 `npm run build`

`make test-benchmark-gate` 会先运行 deterministic `run-all` smoke，再调用 `scripts/benchmark_gate.py` 检查当前 report 的 threshold、critical failure，以及可选 baseline regression。该默认入口使用 `BENCHMARK_LIMIT=1`，不会调用真实 LAMMPS，也不会使用真实 embedding/reranker/LLM Judge；真实 LAMMPS 长测走 `make test-lammps-real`，真实 embedding/reranker/Judge 后端走 `make test-live-backends`，完整 API live benchmark 走 `make test-live`。真实 Judge 还需要显式设置 `MATERIALS_JUDGE_PROVIDER=openrouter|dashscope`、`MATERIALS_JUDGE_LIVE_ENABLED=true` 和对应 API key。若要和旧报告比较：

```bash
make test-benchmark-gate BENCHMARK_BASELINE=backend/outputs/benchmarks/baseline.json
```

若要跑全量 deterministic benchmark gate：

```bash
make test-benchmark-gate BENCHMARK_LIMIT=
```

gate 产物默认写入 `/tmp/phase_diagram_agent_benchmark_gate`，包含 `gate.json` 和 `report.md`；失败时命令返回非 0，可直接作为 CI 合并阻断。

`make audit-advanced-agent` 不会重新跑长 benchmark，只读取当前仓库内的 roadmap、核心能力代码面、`backend/outputs/benchmarks/latest.json`、benchmark dataset manifest 与 MaterialsAgentBench freeze lock，输出一份 JSON 审计结果。它会检查 DAG/replan/lifecycle、LAMMPS 质量门、Red-Blue、共享记忆、benchmark suite、测试文件、Makefile gate 和 CI workflow 是否仍然存在并接线，用来快速确认高级 agent 迁移证据是否仍然闭合。

仓库已提供三条 GitHub Actions workflow：

- `.github/workflows/quick-ci.yml`：PR/push/manual，运行 `make test-quick`；
- `.github/workflows/nightly-benchmark.yml`：定时/manual，运行 `make test-full`，可选真实 LAMMPS gate，并上传 benchmark artifact；
- `.github/workflows/live-backends.yml`：manual，运行 `make test-live-backends`，可选 API live gate；API key 通过 GitHub Secrets 注入，报告只记录 key 是否存在，不输出 key 值。

`make record-benchmark-baseline` 会把当前 deterministic `run-all` 结果写入：

- `backend/outputs/benchmarks/baseline.json`

默认沿用 `BENCHMARK_LIMIT=1`，因此它是快速 smoke baseline；要记录全量 deterministic baseline，用：

```bash
make record-benchmark-baseline BENCHMARK_LIMIT=
```

`make record-lammps-baseline` 会运行 `lammps_contract` 三个 case，并把结构化基线写到：

- `backend/outputs/baselines/lammps_contract/baseline.json`
- `backend/outputs/baselines/lammps_contract/report.md`

报告会显式记录每个 case 的 `run_mode`。默认 baseline 使用 deterministic mock runtime，只能作为基础设施 baseline；如需记录真实科学执行 baseline，可直接运行 `cd backend && conda run -n lammps_agent python benchmarks/lammps_contract_baseline.py --real-lammps`。

## 运行入口

常用端口：

- 前端：`http://127.0.0.1:5174`
- 后端：`http://127.0.0.1:8000`

后端主入口：

- `backend/app/api.py`

## 文档说明

如果要快速恢复项目上下文，请优先读：

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `docs/ADVANCED_AGENT_MIGRATION_ROADMAP.md`
4. `docs/RAG_PRODUCTION.md`

其中：

- `README.md` 保留运行方式、项目结构和主线说明
- `docs/` 保留仍与当前实现对应的专项设计文档
- `docs/ADVANCED_AGENT_MIGRATION_ROADMAP.md` 是后续高级编排、Red-Blue、物理质量门、分层记忆和评测升级的执行路线图

## 当前规模

- thermo registry：当前已扩到 `29` 个真实可计算二元系
- thermo RAG 示例文档：当前为 `145` 条结构化文档
- thermo RAG 文档生成脚本：
  - `backend/examples/build_thermo_rag_documents.py`
  - `backend/app/thermo/rag_documents.py`
- 双层 memory：
  - `backend/outputs/memory/short_term/`
  - `backend/outputs/memory/long_term/`

## 当前开发约定

- 优先保留已验证通过的真实计算链路
- 新能力先旁路增强，不直接替换稳定主链路
- 前端与后端边界保持清晰
- `backend/outputs/` 是运行态目录，run、diagnostic、memory 等产物默认不应作为源码维护
- 相图识别转 HTML 当前采用 canvas-only renderer，不再保留旧 SVG fallback
- MCP 以“旁路 server 封装现有 runtime/tool”为优先，不重写核心计算内核
- thermo RAG 允许接外部 embedding API，但必须保留：
  - deterministic registry execution
  - lexical gate
  - BM25 weight 不压过结构化规则
  - `local_hash` fallback
- 代码状态以 Git 提交、测试和 benchmark 结果为准，不在仓库维护重复的开发流水账
- 在上下文压缩后，优先重新读取 `README.md` 和 `docs/ARCHITECTURE.md`
