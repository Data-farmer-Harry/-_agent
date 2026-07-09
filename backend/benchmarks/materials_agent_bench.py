from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    from benchmarks.benchmark_config import BENCHMARK_DATASET_DIR
    from benchmarks.versioning import build_freeze_manifest, scan_case_data_leakage
except ModuleNotFoundError:  # pragma: no cover - package import fallback
    from .benchmark_config import BENCHMARK_DATASET_DIR
    from .versioning import build_freeze_manifest, scan_case_data_leakage


MATERIALS_AGENT_BENCH_VERSION = "materials-agent-bench/v1"
NOT_APPLICABLE = "not_applicable"

DEFAULT_MATERIALS_AGENT_BENCH_DIR = BENCHMARK_DATASET_DIR / "materials_agent_bench"

BenchmarkMode = Literal["deterministic", "real", "live"]
CaseDifficulty = Literal["normal", "edge", "adversarial"]
MetricStatus = Literal["ok", "not_applicable"]

SUPPORTED_DOMAINS = {
    "routing_clarification",
    "lammps_request_parsing",
    "registry_potential",
    "materials_rag",
    "lammps_execution",
    "phase_diagram_execution",
    "physical_quality",
    "red_blue_repair",
    "orchestration_recovery",
    "shared_memory",
    "recognition",
    "mcp_tooling",
    "final_response",
    "evaluation_judge",
}

DOMAIN_BY_DATASET = {
    "routing_cases": "routing_clarification",
    "phase_parsing_cases": "phase_diagram_execution",
    "lammps_parsing_cases": "lammps_request_parsing",
    "phase_execution_cases": "phase_diagram_execution",
    "lammps_contract_cases": "lammps_execution",
    "lammps_e2e_cases": "lammps_execution",
    "lammps_quality_cases": "physical_quality",
    "lammps_red_blue_cases": "red_blue_repair",
    "review_json_fallback_cases": "red_blue_repair",
    "orchestration_cases": "orchestration_recovery",
    "judge_calibration_cases": "evaluation_judge",
    "lammps_recovery_cases": "orchestration_recovery",
    "recognition_cases": "recognition",
    "external_recognition_cases": "recognition",
    "memory_followup_cases": "shared_memory",
    "memory_retrieval_cases": "shared_memory",
    "shared_memory_cases": "shared_memory",
    "memory_conflict_cases": "shared_memory",
    "context_compression_cases": "shared_memory",
    "materials_multihop_cases": "final_response",
    "mcp_cases": "mcp_tooling",
    "rag_blind_cases": "materials_rag",
}

SENSITIVE_KEY_MARKERS = ("api_key", "apikey", "secret", "token", "password")


@dataclass(frozen=True)
class MetricMeasurement:
    name: str
    value: float | int | None
    status: MetricStatus = "ok"
    numerator: float | int | None = None
    denominator: float | int | None = None
    threshold: float | int | None = None
    passed: bool | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "threshold": self.threshold,
            "passed": self.passed,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class MaterialsAgentBenchCase:
    case_id: str
    benchmark_version: str
    domain: str
    difficulty: CaseDifficulty
    mode: BenchmarkMode
    prompt: str
    source_dataset: str
    source_suite: str
    source_case_id: str
    split: str = "development"
    uploaded_assets: list[dict[str, Any]] = field(default_factory=list)
    expected_route: str | None = None
    expected_compute_domain: str | None = None
    locked_constraints: dict[str, Any] = field(default_factory=dict)
    required_tool_chain: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    required_findings: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    claim_gold: list[dict[str, Any]] = field(default_factory=list)
    citation_gold: list[dict[str, Any]] = field(default_factory=list)
    judge_rubric: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "benchmark_version": self.benchmark_version,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "mode": self.mode,
            "prompt": self.prompt,
            "source_dataset": self.source_dataset,
            "source_suite": self.source_suite,
            "source_case_id": self.source_case_id,
            "split": self.split,
            "uploaded_assets": list(self.uploaded_assets),
            "expected_route": self.expected_route,
            "expected_compute_domain": self.expected_compute_domain,
            "locked_constraints": dict(self.locked_constraints),
            "required_tool_chain": list(self.required_tool_chain),
            "required_evidence": list(self.required_evidence),
            "required_artifacts": list(self.required_artifacts),
            "required_findings": list(self.required_findings),
            "forbidden_claims": list(self.forbidden_claims),
            "claim_gold": list(self.claim_gold),
            "citation_gold": list(self.citation_gold),
            "judge_rubric": dict(self.judge_rubric),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MaterialsAgentBenchResult:
    case_id: str
    passed: bool
    hard_gate_passed: bool
    metrics: dict[str, MetricMeasurement] = field(default_factory=dict)
    critical_failures: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    required_hops: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "hard_gate_passed": self.hard_gate_passed,
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
            "critical_failures": list(self.critical_failures),
            "claims": list(self.claims),
            "citations": list(self.citations),
            "required_hops": list(self.required_hops),
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }


def metric_measurement(
    name: str,
    *,
    value: float | int | None = None,
    numerator: float | int | None = None,
    denominator: float | int | None = None,
    threshold: float | int | None = None,
    greater_is_better: bool = True,
    notes: list[str] | None = None,
) -> MetricMeasurement:
    if denominator == 0:
        return MetricMeasurement(
            name=name,
            value=None,
            status=NOT_APPLICABLE,
            numerator=numerator,
            denominator=denominator,
            threshold=threshold,
            passed=None,
            notes=[*(notes or []), "denominator is zero"],
        )
    resolved_value = value
    if resolved_value is None and numerator is not None and denominator is not None:
        resolved_value = numerator / denominator
    passed: bool | None = None
    if threshold is not None and resolved_value is not None:
        passed = resolved_value >= threshold if greater_is_better else resolved_value <= threshold
    return MetricMeasurement(
        name=name,
        value=resolved_value,
        status="ok",
        numerator=numerator,
        denominator=denominator,
        threshold=threshold,
        passed=passed,
        notes=list(notes or []),
    )


def load_source_datasets(dataset_dir: Path = BENCHMARK_DATASET_DIR) -> dict[str, list[dict[str, Any]]]:
    datasets: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(dataset_dir.glob("*.jsonl")):
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        datasets[path.stem] = rows
    return datasets


def build_materials_agent_cases(datasets: dict[str, list[dict[str, Any]]]) -> list[MaterialsAgentBenchCase]:
    cases: list[MaterialsAgentBenchCase] = []
    for dataset_name, rows in sorted(datasets.items()):
        for row in rows:
            if dataset_name not in DOMAIN_BY_DATASET:
                continue
            cases.append(_adapt_source_case(dataset_name, row))
    return cases


def build_materials_agent_manifest(cases: list[MaterialsAgentBenchCase]) -> dict[str, Any]:
    by_domain: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    by_split: dict[str, int] = {}
    by_source_dataset: dict[str, int] = {}
    for case in cases:
        by_domain[case.domain] = by_domain.get(case.domain, 0) + 1
        by_mode[case.mode] = by_mode.get(case.mode, 0) + 1
        by_split[case.split] = by_split.get(case.split, 0) + 1
        by_source_dataset[case.source_dataset] = by_source_dataset.get(case.source_dataset, 0) + 1
    return {
        "benchmark_version": MATERIALS_AGENT_BENCH_VERSION,
        "case_count": len(cases),
        "domains": dict(sorted(by_domain.items())),
        "modes": dict(sorted(by_mode.items())),
        "splits": dict(sorted(by_split.items())),
        "source_datasets": dict(sorted(by_source_dataset.items())),
        "metric_null_semantics": {
            "zero_denominator": NOT_APPLICABLE,
            "not_applicable_value": None,
            "not_applicable_passed": None,
        },
        "freeze": build_freeze_manifest(cases),
        "judge_layer": {
            "llm_provider_enabled": False,
            "offline_contract_enabled": any(case.source_dataset == "judge_calibration_cases" for case in cases),
            "calibration_case_count": by_source_dataset.get("judge_calibration_cases", 0),
        },
        "llm_judge_enabled": False,
    }


def build_materials_agent_metric_report(
    suite_metrics: dict[str, dict[str, Any]],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or {}
    suites: dict[str, Any] = {}
    for suite, metrics in sorted(suite_metrics.items()):
        suite_payload: dict[str, Any] = {"metrics": {}}
        for metric_name, value in sorted(metrics.items()):
            full_name = f"{suite}.{metric_name}"
            threshold = thresholds.get(full_name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                measurement = MetricMeasurement(
                    name=full_name,
                    value=None,
                    status=NOT_APPLICABLE,
                    threshold=threshold,
                    passed=None,
                    notes=["metric has no numeric value in source report"],
                )
            else:
                measurement = metric_measurement(name=full_name, value=value, threshold=threshold)
            suite_payload["metrics"][metric_name] = measurement.to_dict()
        suites[suite] = suite_payload
    return {
        "benchmark_version": MATERIALS_AGENT_BENCH_VERSION,
        "judge_layer": {
            "llm_provider_enabled": False,
            "offline_contract_enabled": "judge_calibration" in suite_metrics,
        },
        "llm_judge_enabled": False,
        "suites": suites,
    }


def write_materials_agent_bench(cases: list[MaterialsAgentBenchCase], output_dir: Path = DEFAULT_MATERIALS_AGENT_BENCH_DIR) -> dict[str, Any]:
    manifest = build_materials_agent_manifest(cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_split: dict[str, list[MaterialsAgentBenchCase]] = {}
    for case in cases:
        by_split.setdefault(case.split, []).append(case)
    for split, split_cases in by_split.items():
        split_dir = output_dir / split
        split_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True) for case in split_cases]
        (split_dir / "cases.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def validate_materials_agent_cases(cases: list[MaterialsAgentBenchCase]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            errors.append(f"{case.case_id}: duplicate case_id")
        seen.add(case.case_id)
        if case.benchmark_version != MATERIALS_AGENT_BENCH_VERSION:
            errors.append(f"{case.case_id}: unsupported benchmark_version {case.benchmark_version}")
        if case.domain not in SUPPORTED_DOMAINS:
            errors.append(f"{case.case_id}: unsupported domain {case.domain}")
        if case.mode not in {"deterministic", "real", "live"}:
            errors.append(f"{case.case_id}: unsupported mode {case.mode}")
        if case.difficulty not in {"normal", "edge", "adversarial"}:
            errors.append(f"{case.case_id}: unsupported difficulty {case.difficulty}")
        if not case.prompt.strip():
            errors.append(f"{case.case_id}: prompt is empty")
        if _contains_sensitive_key(case.to_dict()):
            errors.append(f"{case.case_id}: contains sensitive key marker")
    for issue in scan_case_data_leakage(cases, split="frozen_test"):
        errors.append(f"{issue['case_id']}: data leakage at {issue['path']} ({issue['reason']})")
    return errors


def _adapt_source_case(dataset_name: str, row: dict[str, Any]) -> MaterialsAgentBenchCase:
    expected = row.get("expected") if isinstance(row.get("expected"), dict) else {}
    source_case_id = str(row.get("case_id") or f"{dataset_name}.unknown")
    source_suite = str(row.get("suite") or dataset_name.removesuffix("_cases"))
    return MaterialsAgentBenchCase(
        case_id=f"{dataset_name}.{source_case_id}",
        benchmark_version=MATERIALS_AGENT_BENCH_VERSION,
        domain=_infer_domain(dataset_name, row),
        difficulty=_infer_difficulty(row),
        mode=_normalize_mode(row),
        prompt=_extract_prompt(dataset_name, row),
        source_dataset=dataset_name,
        source_suite=source_suite,
        source_case_id=source_case_id,
        split=_infer_split(dataset_name, row),
        uploaded_assets=_extract_uploaded_assets(row),
        expected_route=_as_optional_str(expected.get("route_name")),
        expected_compute_domain=_as_optional_str(expected.get("compute_domain")),
        locked_constraints=_extract_locked_constraints(row, expected),
        required_tool_chain=_string_list(expected.get("required_steps") or expected.get("plan_steps") or row.get("required_tool_chain")),
        required_evidence=_extract_required_evidence(row, expected),
        required_artifacts=_string_list(expected.get("required_artifacts") or row.get("required_artifacts")),
        required_findings=_string_list(expected.get("required_issue_terms") or expected.get("required_findings")),
        forbidden_claims=_extract_forbidden_claims(dataset_name, row, expected),
        claim_gold=_dict_list(row.get("claim_gold")),
        citation_gold=_dict_list(row.get("citation_gold")),
        judge_rubric=dict(row.get("judge_rubric") or {}),
        tags=_string_list(row.get("tags")),
        metadata=_build_metadata(dataset_name, row),
    )


def _infer_domain(dataset_name: str, row: dict[str, Any]) -> str:
    if dataset_name == "rag_blind_cases":
        source_domain = str(row.get("domain") or "")
        if source_domain in {"lammps", "materials", "thermo"}:
            return "materials_rag"
    return DOMAIN_BY_DATASET[dataset_name]


def _infer_difficulty(row: dict[str, Any]) -> CaseDifficulty:
    raw = str(row.get("difficulty") or "").lower()
    tags = {str(tag).lower() for tag in row.get("tags", []) if isinstance(tag, str)}
    if raw in {"adversarial", "hard"} or {"adversarial", "conflict", "synthetic", "fatal"} & tags:
        return "adversarial"
    if raw in {"edge", "medium"} or {"edge", "clarification", "recovery", "timeout", "cancel"} & tags:
        return "edge"
    return "normal"


def _normalize_mode(row: dict[str, Any]) -> BenchmarkMode:
    raw_mode = str(row.get("mode") or "").lower()
    if raw_mode.startswith("live") or "live_http" in raw_mode:
        return "live"
    if raw_mode in {"real", "real_lammps", "real_pycalphad"}:
        return "real"
    return "deterministic"


def _infer_split(dataset_name: str, row: dict[str, Any]) -> str:
    if dataset_name == "rag_blind_cases":
        return "frozen_test"
    generation = row.get("generation") if isinstance(row.get("generation"), dict) else {}
    if generation.get("frozen_before_first_evaluation") is True:
        return "frozen_test"
    return "development"


def _extract_prompt(dataset_name: str, row: dict[str, Any]) -> str:
    if row.get("prompt"):
        return str(row["prompt"])
    if row.get("query"):
        return str(row["query"])
    fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else {}
    request = fixture.get("request") if isinstance(fixture.get("request"), dict) else {}
    if request:
        material = request.get("material", "material")
        task_type = request.get("task_type", "task")
        temperature = request.get("temperature")
        steps = request.get("steps")
        return f"LAMMPS fixture for {material} {task_type}, temperature={temperature}, steps={steps}"
    request_payload = row.get("request") if isinstance(row.get("request"), dict) else {}
    if request_payload:
        method = request_payload.get("method") or "request"
        return f"MCP protocol fixture: {method}"
    if row.get("scenario"):
        return f"{dataset_name} scenario: {row['scenario']}"
    if row.get("payload_type"):
        return f"{dataset_name} payload fixture: {row['payload_type']}"
    return str(row.get("case_id") or dataset_name)


def _extract_uploaded_assets(row: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    if row.get("upload_image") is True:
        assets.append({"kind": "image", "source": "inline_fixture"})
    if row.get("asset_path"):
        assets.append({"kind": "image", "path": str(row["asset_path"]), "source_url": row.get("source_url")})
    return assets


def _extract_locked_constraints(row: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for key in ("request", "locked_constraints"):
        value = expected.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else {}
    fixture_request = fixture.get("request") if isinstance(fixture.get("request"), dict) else {}
    if fixture_request:
        candidates.append(fixture_request)
    request_overrides = row.get("request_overrides") if isinstance(row.get("request_overrides"), dict) else {}
    if request_overrides:
        candidates.append(request_overrides)
    direct_keys = {
        "material",
        "potential_family",
        "task_type",
        "temperature",
        "steps",
        "ensemble",
        "system_name",
        "temperature_min",
        "temperature_max",
        "pressure",
    }
    direct = {key: expected[key] for key in direct_keys if key in expected}
    if direct:
        candidates.append(direct)
    locked: dict[str, Any] = {}
    for candidate in candidates:
        for key, value in candidate.items():
            if key in direct_keys and value is not None:
                locked[key] = value
    return locked


def _extract_required_evidence(row: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    evidence = _string_list(row.get("required_evidence") or expected.get("required_evidence"))
    if not evidence and isinstance(row.get("expected"), list):
        evidence = _string_list(row["expected"])
    if not evidence and expected.get("requires_materials_rag"):
        evidence.append("materials_rag_hit")
    if not evidence and expected.get("requires_primary_evidence"):
        evidence.append("primary_evidence")
    return evidence


def _extract_forbidden_claims(dataset_name: str, row: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    claims = _string_list(row.get("forbidden_claims") or expected.get("forbidden_claims"))
    if dataset_name == "lammps_quality_cases" and expected.get("synthetic_thermo") is True:
        claims.append("Do not describe synthetic thermo as a real LAMMPS execution result.")
    if dataset_name in {"lammps_red_blue_cases", "review_json_fallback_cases"}:
        claims.append("Do not apply an unverified repair patch or modify locked constraints silently.")
    return list(dict.fromkeys(claims))


def _build_metadata(dataset_name: str, row: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "source_mode": row.get("mode"),
        "source_dataset": dataset_name,
    }
    expected = row.get("expected") if isinstance(row.get("expected"), dict) else {}
    for key in ("scenario", "payload_type", "schema", "doc_type", "material", "language", "source_url"):
        if key in row:
            metadata[key] = row[key]
    for key in ("required_hops", "expected_conclusion", "required_bridge_claims", "min_hop_completion"):
        if key in expected:
            metadata[key] = expected[key]
    if isinstance(row.get("generation"), dict):
        metadata["generation"] = row["generation"]
    return metadata


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if any(marker in key_lower for marker in SENSITIVE_KEY_MARKERS):
                return True
            if _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
