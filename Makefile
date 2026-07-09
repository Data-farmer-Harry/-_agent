CONDA ?= /opt/anaconda3/bin/conda
CONDA_ENV ?= lammps_agent
PYTHON := $(CONDA) run -n $(CONDA_ENV) python

BACKEND_DIR := backend
FRONTEND_DIR := frontend
API_BASE ?= http://127.0.0.1:8000
MATERIALS_BENCH_OUTPUT ?= /tmp/materials_agent_bench_quick_ci
MATERIALS_FREEZE_LOCK ?= backend/benchmarks/datasets/materials_agent_bench.freeze.json
BENCHMARK_OUTPUT ?= /tmp/phase_diagram_agent_benchmark_latest.json
BENCHMARK_BASELINE ?=
BENCHMARK_BASELINE_OUTPUT ?= backend/outputs/benchmarks/baseline.json
BENCHMARK_GATE_OUTPUT ?= /tmp/phase_diagram_agent_benchmark_gate
BENCHMARK_BASELINE_GATE_OUTPUT ?= /tmp/phase_diagram_agent_benchmark_baseline_gate
BENCHMARK_LIMIT ?= 1
LIVE_BACKENDS ?= 1
LIVE_BACKEND_FLAGS := $(if $(filter 1 true yes,$(LIVE_BACKENDS)),--live-backends,)
OFFLINE_TEST_ENV := \
	PYTHONDONTWRITEBYTECODE=1 \
	PHASE_DIAGRAM_LLM_API_KEY= \
	PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_KEY= \
	PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_KEY= \
	PHASE_DIAGRAM_RAG_RERANKER_ENABLED=false \
	PHASE_DIAGRAM_RAG_RERANKER_API_KEY= \
	MATERIALS_JUDGE_LIVE_ENABLED=false \
	MATERIALS_JUDGE_PROVIDER=mock \
	OPENROUTER_API_KEY= \
	DASHSCOPE_API_KEY=

QUICK_PYTEST_TARGETS := \
	tests/test_dag_models.py \
	tests/test_dag_executor.py \
	tests/test_lifecycle_state_machine.py \
	tests/test_replan_policy.py \
	tests/test_checkpoint_resume.py \
	tests/test_lammps_contract_baseline.py \
	tests/test_lammps_preflight_dag.py \
	tests/test_lammps_quality.py \
	tests/test_lammps_review.py \
	tests/test_memory_dedup.py \
	tests/test_memory_retrieval_pipeline.py \
	tests/test_memory_contradiction.py \
	tests/test_memory_scope_isolation.py \
	tests/test_benchmark_assets.py \
	tests/test_benchmark_case_schema.py \
	tests/test_benchmark_versioning.py \
	tests/test_rule_evaluator.py \
	tests/test_multihop_evaluator.py \
	tests/test_llm_judge_contract.py \
	tests/test_paired_bootstrap.py \
	tests/test_effect_sizes.py \
	tests/test_compare_versions.py \
	tests/test_benchmark_gate.py \
	tests/test_secret_scan.py

.PHONY: help test-quick test-backend-quick test-secret-scan test-dataset-validate test-materials-bench-freeze freeze-materials-agent-bench test-frontend-build test-full test-benchmark-gate audit-advanced-agent record-benchmark-baseline test-lammps-real test-orchestration test-live-backends test-live record-lammps-baseline clean-outputs-dry-run clean-local clean-local-with-node-modules

help:
	@echo "Targets:"
	@echo "  make test-quick          # fast local/PR gate: backend units + dataset/schema validate + frontend build"
	@echo "  make test-secret-scan    # scan trackable files for API keys, tokens, and private paths"
	@echo "  make test-materials-bench-freeze # validate MaterialsAgentBench frozen split lock"
	@echo "  make freeze-materials-agent-bench # rewrite MaterialsAgentBench freeze lock after intentional version bump"
	@echo "  make test-full           # full backend pytest + deterministic benchmark run-all + frontend build"
	@echo "  make test-benchmark-gate # run deterministic benchmark smoke gate and fail on threshold/baseline regressions"
	@echo "  make audit-advanced-agent # audit roadmap, capability surface, deterministic report, and freeze locks"
	@echo "  make record-benchmark-baseline # record current deterministic benchmark report as baseline"
	@echo "  make test-lammps-real    # LAMMPS-focused benchmark suites with --real-lammps for nightly/local validation"
	@echo "  make test-orchestration  # DAG/semaphore/replan benchmark only"
	@echo "  make test-live-backends  # live embedding/reranker/Judge backend gate without frontend API dependency"
	@echo "  make record-lammps-baseline # record LAMMPS contract baseline JSON/Markdown under backend/outputs"
	@echo "  make test-live           # live/API-dependent benchmark suites against API_BASE=$(API_BASE)"
	@echo "  make clean-outputs-dry-run # preview local generated files that can be removed"
	@echo "  make clean-local         # remove generated outputs, caches, dist, pyc, and .DS_Store"
	@echo "  make clean-local-with-node-modules # also remove frontend/node_modules"
	@echo ""
	@echo "Default local/CI test targets run with OFFLINE_TEST_ENV so local API keys do not affect deterministic gates."
	@echo "Use make test-live-backends or make test-live for explicit network/provider validation."
	@echo ""
	@echo "Variables:"
	@echo "  CONDA=$(CONDA)"
	@echo "  CONDA_ENV=$(CONDA_ENV)"
	@echo "  API_BASE=$(API_BASE)"
	@echo "  MATERIALS_FREEZE_LOCK=$(MATERIALS_FREEZE_LOCK)"
	@echo "  BENCHMARK_BASELINE=$(BENCHMARK_BASELINE)"
	@echo "  BENCHMARK_BASELINE_OUTPUT=$(BENCHMARK_BASELINE_OUTPUT)"
	@echo "  BENCHMARK_LIMIT=$(BENCHMARK_LIMIT)"
	@echo "  LIVE_BACKENDS=$(LIVE_BACKENDS)"

test-quick: test-backend-quick test-secret-scan test-dataset-validate test-frontend-build

test-backend-quick:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) -m pytest $(QUICK_PYTEST_TARGETS) -q

test-secret-scan:
	$(OFFLINE_TEST_ENV) $(PYTHON) scripts/secret_scan.py

test-dataset-validate:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/run_benchmarks.py validate
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/build_materials_agent_bench.py --output-dir $(MATERIALS_BENCH_OUTPUT) --summary-only
	$(MAKE) test-materials-bench-freeze

test-materials-bench-freeze:
	$(OFFLINE_TEST_ENV) $(PYTHON) $(BACKEND_DIR)/benchmarks/freeze_materials_agent_bench.py check --lock $(MATERIALS_FREEZE_LOCK)

freeze-materials-agent-bench:
	$(OFFLINE_TEST_ENV) $(PYTHON) $(BACKEND_DIR)/benchmarks/freeze_materials_agent_bench.py write --output $(MATERIALS_FREEZE_LOCK) --force

test-frontend-build:
	cd $(FRONTEND_DIR) && npm run build

test-full:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) -m pytest -q
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/run_benchmarks.py validate
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/run_benchmarks.py run-all --output $(BENCHMARK_OUTPUT)
	$(OFFLINE_TEST_ENV) $(PYTHON) scripts/benchmark_gate.py --report $(BENCHMARK_OUTPUT) $(if $(BENCHMARK_BASELINE),--baseline $(BENCHMARK_BASELINE),) --output-dir $(BENCHMARK_GATE_OUTPUT)
	cd $(FRONTEND_DIR) && npm run build

test-benchmark-gate:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/run_benchmarks.py run-all $(if $(BENCHMARK_LIMIT),--limit $(BENCHMARK_LIMIT),) --output $(BENCHMARK_OUTPUT)
	$(OFFLINE_TEST_ENV) $(PYTHON) scripts/benchmark_gate.py --report $(BENCHMARK_OUTPUT) $(if $(BENCHMARK_BASELINE),--baseline $(BENCHMARK_BASELINE),) --output-dir $(BENCHMARK_GATE_OUTPUT)

audit-advanced-agent:
	$(OFFLINE_TEST_ENV) $(PYTHON) scripts/advanced_agent_audit.py

record-benchmark-baseline:
	$(OFFLINE_TEST_ENV) $(PYTHON) $(BACKEND_DIR)/benchmarks/run_benchmarks.py run-all $(if $(BENCHMARK_LIMIT),--limit $(BENCHMARK_LIMIT),) --output $(BENCHMARK_BASELINE_OUTPUT)
	$(OFFLINE_TEST_ENV) $(PYTHON) scripts/benchmark_gate.py --report $(BENCHMARK_BASELINE_OUTPUT) --output-dir $(BENCHMARK_BASELINE_GATE_OUTPUT) --label baseline

test-lammps-real:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/run_benchmarks.py run-all \
		--suite lammps_contract \
		--suite lammps_e2e \
		--suite lammps_quality \
		--suite lammps_red_blue \
		--suite orchestration \
		--suite lammps_recovery \
		--real-lammps \
		--output $(BENCHMARK_OUTPUT)

test-orchestration:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/run_benchmarks.py run-all \
		--suite orchestration \
		--output $(BENCHMARK_OUTPUT)

test-live-backends:
	cd $(BACKEND_DIR) && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) benchmarks/run_benchmarks.py run-all \
		--suite rag_recall \
		--suite judge_calibration \
		$(LIVE_BACKEND_FLAGS) \
		$(if $(BENCHMARK_LIMIT),--limit $(BENCHMARK_LIMIT),) \
		--output $(BENCHMARK_OUTPUT)
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/benchmark_gate.py --report $(BENCHMARK_OUTPUT) $(if $(BENCHMARK_BASELINE),--baseline $(BENCHMARK_BASELINE),) --output-dir $(BENCHMARK_GATE_OUTPUT) --label live-backends

record-lammps-baseline:
	cd $(BACKEND_DIR) && $(OFFLINE_TEST_ENV) $(PYTHON) benchmarks/lammps_contract_baseline.py --output-dir outputs/baselines/lammps_contract

test-live:
	cd $(BACKEND_DIR) && PYTHONDONTWRITEBYTECODE=1 $(PYTHON) benchmarks/run_benchmarks.py run-all \
		--include-live \
		--live-backends \
		--api-base $(API_BASE) \
		--output $(BENCHMARK_OUTPUT)

clean-outputs-dry-run:
	$(BACKEND_DIR)/examples/cleanup_outputs.sh

clean-local:
	$(BACKEND_DIR)/examples/cleanup_outputs.sh --apply

clean-local-with-node-modules:
	$(BACKEND_DIR)/examples/cleanup_outputs.sh --apply --include-node-modules
