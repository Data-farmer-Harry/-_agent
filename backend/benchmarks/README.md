# Backend Benchmarks

这套 benchmark 面向当前 `phase_diagram_agent` 后端，目标不是只测“答得像不像”，而是测完整 agent 闭环：

- 路由是否正确
- 请求是否能稳定落到真实 runtime
- 相图与 LAMMPS 工具链是否真正跑通
- memory follow-up 是否保留上下文
- MCP 封装是否符合协议并可自动化调用

## Benchmark 分层

### 1. Routing

目标：

- `SupervisorAgent` 是否把请求送到正确链路
- `compute_domain` 是否正确

核心指标：

- `route_accuracy`
- `compute_domain_accuracy`

### 2. Parsing Gold Set

目标：

- 保留自然语言到结构化请求的金标数据集
- 便于后续独立评估 phase/LAMMPS parser

当前说明：

- 数据集已收集
- 当前 runner 先做 `validate / summary`
- 后续可直接接到独立 parser evaluator

### 3. Execution

目标：

- 相图：真实 `pycalphad + TDB`
- LAMMPS contract：真实或 mock/contract 模式下的 artifact 完整性
- LAMMPS E2E：从路由、RAG preflight、请求解释、registry/validation、输入脚本、执行、后处理到 review 的完整闭环；同时验证缺少材料/温度/步数时必须先澄清

核心指标：

- `success_rate`
- `accuracy_gate_pass_rate`
- `artifact_completeness`
- `chain_completion_rate`
- `clarification_accuracy`
- `rag_preflight_rate`

### 4. Recognition

目标：

- 既保留项目内生图片识别回归
- 也补一套外部论文/公开资料相图的 live 识别基准

当前说明：

- `recognition`：使用内生测试图和 `ScriptedLLMClient` 做 contract/regression
- `external_recognition_live`：直接调用当前运行中的 `/api/agent/chat`，对外部论文图做真实多模态识别

### 5. Memory

目标：

- follow-up 是否利用了短期/长期记忆
- 不同会话是否隔离

核心指标：

- `followup_grounding_rate`
- `memory_retrieval_relevance`

### 6. MCP

目标：

- sidecar MCP server 是否能完成：
  - `initialize`
  - `tools/list`
  - registry/rag search
  - `run_structured`

核心指标：

- `protocol_pass_rate`
- `tool_contract_pass_rate`

## 数据集文件

生成后位于：

- `backend/benchmarks/datasets/manifest.json`
- `backend/benchmarks/datasets/routing_cases.jsonl`
- `backend/benchmarks/datasets/phase_parsing_cases.jsonl`
- `backend/benchmarks/datasets/lammps_parsing_cases.jsonl`
- `backend/benchmarks/datasets/phase_execution_cases.jsonl`
- `backend/benchmarks/datasets/lammps_contract_cases.jsonl`
- `backend/benchmarks/datasets/lammps_e2e_cases.jsonl`
- `backend/benchmarks/datasets/recognition_cases.jsonl`
- `backend/benchmarks/datasets/external_recognition_cases.jsonl`
- `backend/benchmarks/datasets/memory_followup_cases.jsonl`
- `backend/benchmarks/datasets/memory_retrieval_cases.jsonl`
- `backend/benchmarks/datasets/mcp_cases.jsonl`

外部论文图资产位于：

- `backend/benchmarks/assets/external_phase_diagrams/`

## 命令

先重建数据集：

```bash
cd backend && ./.venv/bin/python benchmarks/build_datasets.py
```

查看汇总：

```bash
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py summary
```

校验数据集：

```bash
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py validate
```

运行确定性 benchmark：

```bash
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite routing
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite rag_recall
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite phase_execution --limit 5
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite lammps_contract
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite lammps_e2e
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite external_recognition_live
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite memory
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite memory_retrieval
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run --suite mcp
```

运行固化总控 benchmark：

```bash
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run-all
```

总控报告默认写入：

- `backend/outputs/benchmarks/latest.json`

快速 smoke：

```bash
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run-all --suite routing --suite rag_recall --limit 3
```

如需加入真实外部多模态识别 live benchmark：

```bash
cd backend && ./.venv/bin/python benchmarks/run_benchmarks.py run-all --include-live --api-base http://127.0.0.1:8000
```

## 固化指标与阈值

`run-all` 会统一输出以下指标，并按内置阈值给出 pass/fail：

- `routing.route_accuracy`
- `routing.compute_domain_accuracy`
- `rag_recall.materials_hit@5`
- `rag_recall.thermo_hit@5`
- `phase_execution.success_rate`
- `phase_execution.accuracy_gate_pass_rate`
- `lammps_contract.artifact_completeness`
- `lammps_e2e.chain_completion_rate`
- `lammps_e2e.clarification_accuracy`
- `lammps_e2e.rag_preflight_rate`
- `recognition.success_rate`
- `memory.followup_grounding_rate`
- `memory_retrieval.memory_retrieval_relevance`
- `mcp.tool_contract_pass_rate`

报告还会记录：

- 每个 suite 的总耗时
- `avg_case_duration_seconds`
- 原始 case 结果
- benchmark manifest
- 阈值检查明细

## 当前设计边界

- benchmark 默认优先使用 `ScriptedLLMClient`，保证可重复
- `routing` suite 现在直接测 `SupervisorAgent.decide()`，不再拖着整条 `/api/agent/chat` 链一起跑
- 相图执行集会走真实 `pycalphad + TDB`
- LAMMPS contract benchmark 保留为稳定 artifact 回归；`lammps_e2e` 额外覆盖完整 agent 链路、RAG preflight 和缺参澄清
- `external_recognition_live` 依赖当前后端常驻进程和真实多模态 API，可用于量化外部论文图的泛化能力
- `memory_followup` 只测 API 可观测 follow-up grounding
- `memory_retrieval` 单独测长期记忆命中质量
- parsing 数据集已经收集，但解析层还没有单独拆成独立 runner
