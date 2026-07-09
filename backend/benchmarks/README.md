# Backend Benchmarks

这套 benchmark 面向当前 `phase_diagram_agent` 后端，目标不是只测“答得像不像”，而是测完整 agent 闭环：

- 路由是否正确
- 请求是否能稳定落到真实 runtime
- 相图与 LAMMPS 工具链是否真正跑通
- LAMMPS 结果是否通过物理质量门，且 real/mock/synthetic 是否被正确区分
- memory follow-up 是否保留上下文
- shared memory 是否保持 scope 隔离、冲突可见、证据可追溯和上下文压缩安全
- MCP 封装是否符合协议并可自动化调用
- MaterialsAgentBench v1 adapter 是否能把旧 suite 映射成统一 case/result schema
- Layer 1 rule evaluator 是否能在无 LLM 环境下拦截 hard gate failure
- Judge contract 是否保持 blinded input、安全 JSON fallback 和 hard gate 不可覆盖

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
- LAMMPS quality：用 deterministic fixture 验证 `quality_report.json` 语义、fatal anomaly recall、真实 synthetic 禁止和 valid run false-block
- LAMMPS Red-Blue：直接评测 deterministic Red review、Blue patch policy、locked constraint、证据可追溯、patch verification 与 bounded repair loop
- Review JSON fallback：评测 strict/normalized/deterministic fallback 解析恢复率，以及非法 Blue patch safe-reject

核心指标：

- `success_rate`
- `accuracy_gate_pass_rate`
- `artifact_completeness`
- `chain_completion_rate`
- `clarification_accuracy`
- `rag_preflight_rate`
- `fatal_anomaly_recall`
- `valid_run_pass_rate`
- `real_synthetic_guard_rate`
- `fatal_finding_recall`
- `valid_run_non_block_rate`
- `locked_field_protection_rate`
- `patch_verification_rate`
- `evidence_traceability_rate`
- `rag_evidence_traceability_rate`
- `request_script_consistency_block_rate`
- `bounded_loop_rate`
- `protocol_recovery_rate`
- `invalid_patch_rejection_rate`

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
- shared memory 是否正确去重、隔离、保留 locked fact
- conflict detection 是否避免错误自动覆盖
- context compression 是否保持 L2→L3 可追溯并保护不可压缩内容

核心指标：

- `followup_grounding_rate`
- `memory_retrieval_relevance`
- `duplicate_recall`
- `scope_isolation_rate`
- `locked_retention_rate`
- `conflict_recall`
- `no_incorrect_auto_resolution_rate`
- `l2_traceability_rate`
- `noncompressible_protection_rate`

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

### 7. MaterialsAgentBench / Layer 1 Rule Evaluator

目标：

- 将旧 suite case 映射到统一 `MaterialsAgentBenchCase`
- 用 deterministic rule evaluator 输出 `MaterialsAgentBenchResult`
- 在无 LLM 环境下验证 locked constraints、tool chain、artifact、provenance、claim、citation 和 critical hallucination
- 验证 MaterialsMultiHop 中 Registry/RAG/script/log/quality/repair/final answer 的证据链是否完整
- 保证 Judge metadata 不能覆盖 Layer 1 hard gate failure
- 在 manifest 中生成 `freeze` 区块，对 `frozen_test` case 做内容 hash、benchmark version 与数据防泄漏记录

核心指标：

- `locked_constraint_accuracy`
- `tool_chain_completion`
- `artifact_completeness`
- `real_mock_provenance_accuracy`
- `factual_accuracy`
- `hallucination_rate`
- `critical_hallucination_rate`
- `citation_coverage`
- `citation_precision`
- `evidence_chain_completeness`
- `required_hop_completion`
- `no_unsupported_bridge_claim_rate`
- `final_conclusion_correctness`
- `citation_order_authority_rate`
- `missing_hop_honesty_rate`
- `within_one_agreement_rate`
- `parse_recovery_rate`
- `hard_gate_non_override_rate`
- `blind_input_safety_rate`
- `drift_free_rate`
- `quick_ci_backend_available_rate`
- `backend_matrix_secret_safety_rate`

Judge 相关 benchmark 默认是离线 contract / calibration：它验证五维评分 schema、盲评输入、strict/normalized/deterministic 三层解析 fallback、Layer 1 hard gate 不可被 Judge 高分覆盖、Judge calibration drift，以及 OpenRouter/DashScope/local/mock 的后端能力矩阵。默认 quick gate 不调用线上 LLM provider，也不替代确定性科学 gate。

如需手动开启真实 Judge provider，必须显式设置 live 环境变量，并使用 `--live-backends`：

```bash
export MATERIALS_JUDGE_PROVIDER=openrouter
export MATERIALS_JUDGE_LIVE_ENABLED=true
export OPENROUTER_API_KEY=...
export OPENROUTER_JUDGE_MODEL=openai/gpt-4o-mini
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite judge_calibration --live-backends
```

DashScope compatible-mode 同理使用 `MATERIALS_JUDGE_PROVIDER=dashscope`、`DASHSCOPE_API_KEY` 和 `DASHSCOPE_JUDGE_MODEL`。报告只记录 provider/model/key 是否存在，不写入 API key 值。

### 8. Statistics / Version Comparison Primitives

目标：

- 对同一批 case 做 paired bootstrap 95% CI
- 按 domain 分层报告，case 少于 30 时不输出伪精确 CI
- 对二元 pass/fail 使用 paired risk difference 与 exact McNemar
- 对连续 Judge/质量分使用 paired Cohen's dz
- 对 latency/token/cost 输出 median、P90/P95 等尾部统计
- 记录固定 seed、resample 数和环境 manifest，保证统计输出可复现

核心工具：

- `paired_bootstrap_ci`
- `paired_statistics_report`
- `paired_risk_difference`
- `mcnemar_exact`
- `cohens_dz`
- `summarize_distribution`
- `build_statistics_environment_manifest`
- `compare_versions.py`

## 数据集文件

生成后位于：

- `backend/benchmarks/datasets/manifest.json`
- `backend/benchmarks/datasets/routing_cases.jsonl`
- `backend/benchmarks/datasets/phase_parsing_cases.jsonl`
- `backend/benchmarks/datasets/lammps_parsing_cases.jsonl`
- `backend/benchmarks/datasets/phase_execution_cases.jsonl`
- `backend/benchmarks/datasets/lammps_contract_cases.jsonl`
- `backend/benchmarks/datasets/lammps_e2e_cases.jsonl`
- `backend/benchmarks/datasets/lammps_quality_cases.jsonl`
- `backend/benchmarks/datasets/lammps_red_blue_cases.jsonl`
- `backend/benchmarks/datasets/review_json_fallback_cases.jsonl`
- `backend/benchmarks/datasets/recognition_cases.jsonl`
- `backend/benchmarks/datasets/external_recognition_cases.jsonl`
- `backend/benchmarks/datasets/memory_followup_cases.jsonl`
- `backend/benchmarks/datasets/memory_retrieval_cases.jsonl`
- `backend/benchmarks/datasets/shared_memory_cases.jsonl`
- `backend/benchmarks/datasets/memory_conflict_cases.jsonl`
- `backend/benchmarks/datasets/context_compression_cases.jsonl`
- `backend/benchmarks/datasets/materials_multihop_cases.jsonl`
- `backend/benchmarks/datasets/judge_calibration_cases.jsonl`
- `backend/benchmarks/datasets/mcp_cases.jsonl`

外部论文图资产位于：

- `backend/benchmarks/assets/external_phase_diagrams/`

## 命令

根目录提供快速质量门：

```bash
make test-quick
```

该入口会运行后端 deterministic unit/schema/evaluator/statistics 快速单测、dataset validate、MaterialsAgentBench 临时构建和前端 build。

先重建数据集：

```bash
cd backend && conda run -n lammps_agent python benchmarks/build_datasets.py
```

查看汇总：

```bash
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py summary
```

校验数据集：

```bash
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py validate
```

运行确定性 benchmark：

```bash
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite routing
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite rag_recall
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite phase_execution --limit 5
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite lammps_contract
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite lammps_e2e
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite lammps_quality
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite lammps_red_blue
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite review_json_fallback
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite external_recognition_live
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite memory
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite memory_retrieval
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite shared_memory
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite memory_conflict
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite context_compression
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite materials_multihop
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite judge_calibration
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run --suite mcp
```

构建 MaterialsAgentBench v1 统一 schema 视图：

```bash
cd backend && conda run -n lammps_agent python benchmarks/build_materials_agent_bench.py
```

默认输出到：

- `backend/benchmarks/datasets/materials_agent_bench/manifest.json`
- `backend/benchmarks/datasets/materials_agent_bench/development/cases.jsonl`
- `backend/benchmarks/datasets/materials_agent_bench/frozen_test/cases.jsonl`
- `backend/benchmarks/datasets/materials_agent_bench.freeze.json`

当前构建会将 390 条 case 映射为 140 条 `development` + 250 条 `frozen_test`；其中 `frozen_test` 包含 247 条 RAG blind holdout 和 3 条 MaterialsMultiHop 多证据链回归 case。

`manifest.json` 内包含 `freeze` 区块：

- `case_hashes`：每条 `frozen_test` case 的内容 hash，排除 `benchmark_version`
- `split_hash`：整个 frozen split 的聚合 hash
- `data_leakage`：API key/token/password/private path 等泄漏扫描结果
- `hash_excludes`：hash 时排除的字段，当前为 `benchmark_version`

同一 benchmark version 下修改、删除或新增 frozen case 应视为失败；如果确实要修改 frozen case，需要升级 benchmark version 并重新冻结。

仓库级冻结锁文件位于 `backend/benchmarks/datasets/materials_agent_bench.freeze.json`，当前锁定 390 条 MaterialsAgentBench case，其中 250 条为 `frozen_test`。快速验证：

```bash
make test-materials-bench-freeze
```

等价的后端命令：

```bash
cd backend && conda run -n lammps_agent python benchmarks/freeze_materials_agent_bench.py check
```

只有在确认要发布新的 benchmark version 后，才重写冻结锁：

```bash
make freeze-materials-agent-bench
```

验证 MaterialsAgentBench schema、冻结校验与 Layer 1 rule evaluator：

```bash
cd backend && conda run -n lammps_agent python -m pytest tests/test_benchmark_case_schema.py tests/test_benchmark_versioning.py tests/test_rule_evaluator.py tests/test_multihop_evaluator.py tests/test_llm_judge_contract.py -q
```

验证统计层：

```bash
cd backend && conda run -n lammps_agent python -m pytest tests/test_paired_bootstrap.py tests/test_effect_sizes.py tests/test_compare_versions.py -q
```

运行固化总控 benchmark：

```bash
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run-all
```

默认 `run-all` 是 deterministic gate：`lammps_contract` 和 `lammps_e2e` 会强制使用 mock LAMMPS runtime，RAG embedding 使用本地 hash，reranker 关闭，防止本机 `.env` 中的真实 API key 或真实 LAMMPS 环境影响快速 CI。需要真实 LAMMPS 时显式追加 `--real-lammps`，需要真实 embedding/reranker/Judge backend 时显式追加 `--live-backends`，或使用根目录 `make test-lammps-real` / `make test-live-backends` / `make test-live`。

总控报告默认写入：

- `backend/outputs/benchmarks/latest.json`

运行 benchmark gate：

```bash
make test-benchmark-gate
make test-benchmark-gate BENCHMARK_BASELINE=backend/outputs/benchmarks/baseline.json
```

该 gate 默认使用 `BENCHMARK_LIMIT=1` 运行 deterministic `run-all` smoke，检查当前 report 的 `passed`、所有 `threshold_checks`、critical failures；如果传入 baseline，还会调用 paired comparison 检查 case regression、threshold regression 和新增 critical failure。输出默认位于 `/tmp/phase_diagram_agent_benchmark_gate`，失败时返回非 0，可直接接入 CI merge gate。

全量 deterministic gate 可显式清空 limit：

```bash
make test-benchmark-gate BENCHMARK_LIMIT=
```

手动验证 live backend，但不依赖前端 API：

```bash
MATERIALS_JUDGE_PROVIDER=openrouter \
MATERIALS_JUDGE_LIVE_ENABLED=true \
OPENROUTER_API_KEY=... \
make test-live-backends BENCHMARK_LIMIT=
```

该入口只选择 `rag_recall` 和 `judge_calibration`，用于验证真实 embedding/reranker/Judge provider 的 backend path；完整 API live suite 仍使用 `make test-live API_BASE=...`。

如果只想验证 Make target、gate 和 artifact wiring，不触发真实网络 backend：

```bash
make test-live-backends LIVE_BACKENDS=0
```

仓库 CI 分层：

- `.github/workflows/quick-ci.yml`：PR/push/manual，执行 `make test-quick`；
- `.github/workflows/nightly-benchmark.yml`：schedule/manual，执行 `make test-full`，并可追加 `make test-lammps-real`；
- `.github/workflows/live-backends.yml`：manual，执行 `make test-live-backends`，可选 `make test-live`，所有 key 通过 GitHub Secrets 注入且不写入报告。

记录当前 deterministic benchmark baseline：

```bash
make record-benchmark-baseline
```

默认沿用 `BENCHMARK_LIMIT=1`，因此写出的是快速 smoke baseline：

- `backend/outputs/benchmarks/baseline.json`

如果要记录完整 deterministic `run-all` baseline，显式清空 limit：

```bash
make record-benchmark-baseline BENCHMARK_LIMIT=
```

注意这里有两个“全量”概念：

- `MaterialsAgentBench` 数据集清单当前为 390 条 case（140 development + 250 frozen_test），由 `backend/benchmarks/datasets/materials_agent_bench/manifest.json` 和 freeze lock 固化；
- deterministic `run-all` 当前执行 19 个 suite、129 个可本地确定性运行的 case，不包含 4 个 external recognition live case，也不把 390 条 adapter 数据逐条都作为 API/runtime case 执行。

2026-07-09 的完整 deterministic `run-all` 基线：

- 输出：`backend/outputs/benchmarks/latest.json`（ignored local artifact）；
- gate：`backend/outputs/benchmarks/latest_gate/report.md`；
- 结果：`passed=true`，57 个 threshold checks 全部通过；
- 耗时：988.98s，其中 `phase_execution` 29 个真实 pycalphad case 耗时 959.318s，是 full benchmark 的主要长尾。

单独运行 DAG / semaphore / replan / global timeout benchmark：

```bash
make test-orchestration
# 或
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run-all --suite orchestration
```

比较两个 benchmark report：

```bash
cd backend && conda run -n lammps_agent python benchmarks/compare_versions.py \
  --old outputs/benchmarks/baseline.json \
  --new outputs/benchmarks/latest.json \
  --output-dir outputs/benchmarks/comparison
```

比较输出包括：

- `manifest.json`
- `environment.json`
- `statistics.json`
- `threshold_checks.json`
- `regressions.json`
- `report.md`

如果出现 threshold regression 或 critical regression，`compare_versions.py`
会返回非 0 退出码，后续可直接接入 CI gate。

记录 LAMMPS contract baseline：

```bash
make record-lammps-baseline
```

默认写入：

- `backend/outputs/baselines/lammps_contract/baseline.json`
- `backend/outputs/baselines/lammps_contract/report.md`

该报告记录 3 个 `lammps_contract` case 的耗时、`run_mode`、产物全集、缺失产物和阈值检查。`run_mode=mock` 时只能视为基础设施 baseline，不应声称是真实科学执行 baseline。

若要记录真实 LAMMPS contract baseline：

```bash
cd backend && conda run -n lammps_agent python benchmarks/lammps_contract_baseline.py --real-lammps
```

快速 smoke：

```bash
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run-all --suite routing --suite rag_recall --limit 3
```

如需加入真实外部多模态识别 live benchmark：

```bash
cd backend && conda run -n lammps_agent python benchmarks/run_benchmarks.py run-all --include-live --api-base http://127.0.0.1:8000
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
- `lammps_quality.fatal_anomaly_recall`
- `lammps_quality.valid_run_pass_rate`
- `lammps_quality.real_synthetic_guard_rate`
- `lammps_red_blue.fatal_finding_recall`
- `lammps_red_blue.valid_run_non_block_rate`
- `lammps_red_blue.locked_field_protection_rate`
- `lammps_red_blue.patch_verification_rate`
- `lammps_red_blue.evidence_traceability_rate`
- `lammps_red_blue.rag_evidence_traceability_rate`
- `lammps_red_blue.request_script_consistency_block_rate`
- `lammps_red_blue.bounded_loop_rate`
- `review_json_fallback.protocol_recovery_rate`
- `review_json_fallback.invalid_patch_rejection_rate`
- `orchestration.dependency_correctness_rate`
- `orchestration.no_concurrency_violation_rate`
- `orchestration.injected_delay_speedup`
- `orchestration.degradation_decision_accuracy`
- `orchestration.partial_report_safety_rate`
- `lammps_recovery.checkpoint_resume_correctness`
- `recognition.success_rate`
- `memory.followup_grounding_rate`
- `memory_retrieval.memory_retrieval_relevance`
- `shared_memory.duplicate_recall`
- `shared_memory.scope_isolation_rate`
- `shared_memory.locked_retention_rate`
- `shared_memory.evidence_traceability_rate`
- `memory_conflict.conflict_recall`
- `memory_conflict.needs_user_rate`
- `memory_conflict.quarantine_rate`
- `memory_conflict.semantic_candidate_rate`
- `memory_conflict.no_incorrect_auto_resolution_rate`
- `context_compression.l2_traceability_rate`
- `context_compression.noncompressible_protection_rate`
- `materials_multihop.required_hop_completion`
- `materials_multihop.evidence_chain_completeness`
- `materials_multihop.no_unsupported_bridge_claim_rate`
- `materials_multihop.final_conclusion_correctness`
- `materials_multihop.citation_order_authority_rate`
- `materials_multihop.missing_hop_honesty_rate`
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
- `lammps_quality` 不走 HTTP，而是直接跑 `PhysicalQualityReport` fixture，专门防止质量门、真实/模拟 provenance 与 synthetic thermo 语义回归
- `lammps_red_blue` 不走 HTTP，而是直接跑 Red/Blue 协议组件，专门防止 LLM 绕过 deterministic gate、误改 locked field、未验证 patch 执行和 repair loop 震荡
- `review_json_fallback` 不走 HTTP，专门量化 JSON protocol recovery 与 invalid patch safe-reject
- `orchestration` 不走 HTTP/真实 LAMMPS，使用 fake handler 固化 DAG 拓扑、resource semaphore、Level 1 fallback、Level 2 replan/checkpoint reuse 和 Level 3 partial report safety
- `lammps_recovery` 直接跑 orchestration/runtime/job queue fixture，覆盖 global timeout partial report、preflight Plan v+1 checkpoint 复用、worker crash 和 running cancel 幂等
- `external_recognition_live` 依赖当前后端常驻进程和真实多模态 API，可用于量化外部论文图的泛化能力
- `memory_followup` 只测 API 可观测 follow-up grounding
- `memory_retrieval` 单独测长期记忆命中质量
- `shared_memory`、`memory_conflict`、`context_compression` 直接测 SharedMemoryService 的去重、scope、locked retention、raw evidence hash、冲突安全和压缩追溯
- `materials_multihop` 直接测多跳证据链 fixture，覆盖 lost atoms 修复、unsupported potential registry block 和 synthetic thermo provenance guard
- `MaterialsAgentBench` adapter 只做 schema/指标统一、frozen hash manifest 与防泄漏校验；`judge_calibration` 只做离线 Judge contract，不调用线上 LLM，也不替代旧 runner
- parsing 数据集已经收集，但解析层还没有单独拆成独立 runner
