from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.materials_agent_bench import MaterialsAgentBenchCase, MaterialsAgentBenchResult, MetricMeasurement, metric_measurement


SUPPORTED_CLAIM_STATUSES = {"supported", "contradicted", "unsupported", "not_verifiable", "not_applicable"}
HALLUCINATION_STATUSES = {"contradicted", "unsupported"}
NON_REAL_PROVENANCE = {"mock", "synthetic", "contract", "fixture", "dry_run", "simulated"}
SUCCESS_PHRASES = (
    "completed successfully",
    "successfully completed",
    "run succeeded",
    "simulation succeeded",
    "execution succeeded",
    "已成功",
    "成功完成",
    "运行成功",
    "执行成功",
    "模拟成功",
)
REAL_EXECUTION_PHRASES = (
    "real lammps",
    "real execution",
    "actually ran",
    "真实 lammps",
    "真实执行",
    "实际执行",
    "真正运行",
)
NEGATION_PHRASES = ("not successful", "did not succeed", "failed", "失败", "未成功", "没有成功")


@dataclass(frozen=True)
class RuleEvaluationObservation:
    route_name: str | None = None
    compute_domain: str | None = None
    locked_constraints: dict[str, Any] = field(default_factory=dict)
    completed_tools: list[str] = field(default_factory=list)
    artifacts: list[str | dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    final_response: str = ""
    claims: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    required_hops: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_name": self.route_name,
            "compute_domain": self.compute_domain,
            "locked_constraints": dict(self.locked_constraints),
            "completed_tools": list(self.completed_tools),
            "artifacts": list(self.artifacts),
            "provenance": dict(self.provenance),
            "final_response": self.final_response,
            "claims": list(self.claims),
            "citations": list(self.citations),
            "required_hops": list(self.required_hops),
            "metadata": dict(self.metadata),
        }


def evaluate_rule_layer(
    case: MaterialsAgentBenchCase,
    observation: RuleEvaluationObservation | dict[str, Any],
) -> MaterialsAgentBenchResult:
    obs = observation.to_dict() if isinstance(observation, RuleEvaluationObservation) else dict(observation)
    critical_failures: list[str] = []
    metrics: dict[str, MetricMeasurement] = {}

    _add_route_metrics(case, obs, metrics)
    _add_locked_constraint_metrics(case, obs, metrics, critical_failures)
    _add_tool_chain_metrics(case, obs, metrics)
    _add_artifact_metrics(case, obs, metrics)
    _add_provenance_metrics(obs, metrics, critical_failures)
    _add_execution_success_metrics(obs, critical_failures)
    claims = _normalized_claims(case, obs)
    citations = _normalized_citations(obs)
    _add_claim_metrics(claims, metrics, critical_failures)
    _add_citation_metrics(case, citations, metrics)
    _add_multihop_metric(obs, metrics)

    critical_count = len(critical_failures)
    metrics["critical_hallucination_rate"] = metric_measurement(
        "critical_hallucination_rate",
        numerator=1 if critical_count else 0,
        denominator=1,
        threshold=0.0,
        greater_is_better=False,
        notes=[f"{critical_count} critical failure(s)"],
    )
    hard_gate_passed = critical_count == 0
    passed = hard_gate_passed and all(metric.passed is not False for metric in metrics.values())
    return MaterialsAgentBenchResult(
        case_id=case.case_id,
        passed=passed,
        hard_gate_passed=hard_gate_passed,
        metrics=metrics,
        critical_failures=critical_failures,
        claims=claims,
        citations=citations,
        required_hops=list(obs.get("required_hops") or []),
        evidence_refs=list(obs.get("evidence_refs") or []),
        metadata={"rule_evaluator_version": "materials-rule-evaluator/v1"},
    )


def _add_route_metrics(case: MaterialsAgentBenchCase, obs: dict[str, Any], metrics: dict[str, MetricMeasurement]) -> None:
    metrics["route_accuracy"] = _binary_metric(
        "route_accuracy",
        _optional_match(case.expected_route, obs.get("route_name")),
        applicable=case.expected_route is not None,
    )
    metrics["compute_domain_accuracy"] = _binary_metric(
        "compute_domain_accuracy",
        _optional_match(case.expected_compute_domain, obs.get("compute_domain")),
        applicable=case.expected_compute_domain is not None,
    )


def _add_locked_constraint_metrics(
    case: MaterialsAgentBenchCase,
    obs: dict[str, Any],
    metrics: dict[str, MetricMeasurement],
    critical_failures: list[str],
) -> None:
    expected = case.locked_constraints
    observed = dict(obs.get("locked_constraints") or obs.get("request") or {})
    hits = 0
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        observed_value = observed.get(key)
        if _values_match(expected_value, observed_value):
            hits += 1
        else:
            mismatches.append(f"{key}: expected {expected_value!r}, observed {observed_value!r}")
    metrics["locked_constraint_accuracy"] = metric_measurement(
        "locked_constraint_accuracy",
        numerator=hits,
        denominator=len(expected),
        threshold=1.0,
    )
    for mismatch in mismatches:
        critical_failures.append(f"locked constraint changed silently ({mismatch})")


def _add_tool_chain_metrics(case: MaterialsAgentBenchCase, obs: dict[str, Any], metrics: dict[str, MetricMeasurement]) -> None:
    required = set(case.required_tool_chain)
    completed = {str(item) for item in obs.get("completed_tools") or obs.get("tool_chain") or []}
    hits = len(required & completed)
    metrics["tool_chain_completion"] = metric_measurement(
        "tool_chain_completion",
        numerator=hits,
        denominator=len(required),
        threshold=1.0,
    )


def _add_artifact_metrics(case: MaterialsAgentBenchCase, obs: dict[str, Any], metrics: dict[str, MetricMeasurement]) -> None:
    required = set(case.required_artifacts)
    artifacts = _artifact_names(obs.get("artifacts") or [])
    hits = len(required & artifacts)
    metrics["artifact_completeness"] = metric_measurement(
        "artifact_completeness",
        numerator=hits,
        denominator=len(required),
        threshold=1.0,
    )


def _add_provenance_metrics(
    obs: dict[str, Any],
    metrics: dict[str, MetricMeasurement],
    critical_failures: list[str],
) -> None:
    provenance = dict(obs.get("provenance") or {})
    actual = _normalize_provenance(provenance.get("actual") or provenance.get("run_mode") or provenance.get("actual_mode"))
    claimed = _normalize_provenance(provenance.get("claimed") or provenance.get("claimed_run_mode") or provenance.get("claimed_mode"))
    final_response = str(obs.get("final_response") or "")
    if claimed is None and _claims_real_execution(final_response):
        claimed = "real"
    applicable = actual is not None or claimed is not None
    accurate = actual is not None and (claimed is None or actual == claimed)
    if actual in NON_REAL_PROVENANCE and claimed == "real":
        accurate = False
        critical_failures.append(f"{actual} provenance described as real execution")
    metrics["real_mock_provenance_accuracy"] = _binary_metric(
        "real_mock_provenance_accuracy",
        accurate,
        applicable=applicable,
    )


def _add_execution_success_metrics(obs: dict[str, Any], critical_failures: list[str]) -> None:
    execution = dict(obs.get("execution") or {})
    physical_gate = dict(obs.get("physical_gate") or {})
    final_response = str(obs.get("final_response") or "")
    execution_failed = execution.get("success") is False or physical_gate.get("passed") is False
    if execution_failed and _claims_success(final_response):
        critical_failures.append("final response claims success although execution or physical gate failed")


def _add_claim_metrics(
    claims: list[dict[str, Any]],
    metrics: dict[str, MetricMeasurement],
    critical_failures: list[str],
) -> None:
    factual_claims = [claim for claim in claims if claim.get("status") in SUPPORTED_CLAIM_STATUSES - {"not_applicable"}]
    supported = [claim for claim in factual_claims if claim.get("status") == "supported"]
    hallucinated = [claim for claim in factual_claims if claim.get("status") in HALLUCINATION_STATUSES]
    metrics["factual_accuracy"] = metric_measurement(
        "factual_accuracy",
        numerator=len(supported),
        denominator=len(factual_claims),
        threshold=0.95,
    )
    metrics["hallucination_rate"] = metric_measurement(
        "hallucination_rate",
        numerator=len(hallucinated),
        denominator=len(factual_claims),
        threshold=0.0,
        greater_is_better=False,
    )
    for claim in hallucinated:
        if claim.get("critical") is True or claim.get("severity") == "critical":
            text = str(claim.get("text") or claim.get("claim_id") or "critical claim")
            critical_failures.append(f"critical claim is {claim['status']}: {text}")


def _add_citation_metrics(
    case: MaterialsAgentBenchCase,
    citations: list[dict[str, Any]],
    metrics: dict[str, MetricMeasurement],
) -> None:
    required = set(case.required_evidence)
    supporting_ids = {
        str(citation.get("evidence_id") or citation.get("source") or citation.get("source_ref"))
        for citation in citations
        if _citation_supports(citation)
    }
    metrics["citation_coverage"] = metric_measurement(
        "citation_coverage",
        numerator=len(required & supporting_ids),
        denominator=len(required),
        threshold=1.0,
    )
    supporting_count = sum(1 for citation in citations if _citation_supports(citation))
    metrics["citation_precision"] = metric_measurement(
        "citation_precision",
        numerator=supporting_count,
        denominator=len(citations),
        threshold=1.0,
    )


def _add_multihop_metric(obs: dict[str, Any], metrics: dict[str, MetricMeasurement]) -> None:
    hops = [hop for hop in obs.get("required_hops") or [] if isinstance(hop, dict)]
    completed = [hop for hop in hops if hop.get("completed") is True]
    metrics["evidence_chain_completeness"] = metric_measurement(
        "evidence_chain_completeness",
        numerator=len(completed),
        denominator=len(hops),
        threshold=1.0,
    )


def _normalized_claims(case: MaterialsAgentBenchCase, obs: dict[str, Any]) -> list[dict[str, Any]]:
    observed_claims = [dict(claim) for claim in obs.get("claims") or [] if isinstance(claim, dict)]
    if not case.claim_gold:
        return [_normalize_claim(claim) for claim in observed_claims]

    by_id = {str(claim.get("claim_id")): _normalize_claim(claim) for claim in observed_claims if claim.get("claim_id") is not None}
    normalized: list[dict[str, Any]] = []
    for gold in case.claim_gold:
        claim_id = str(gold.get("claim_id") or gold.get("id") or gold.get("text"))
        observed = by_id.get(claim_id, {})
        status = observed.get("status") or gold.get("observed_status") or "unsupported"
        normalized.append(
            _normalize_claim(
                {
                    **gold,
                    **observed,
                    "claim_id": claim_id,
                    "status": status,
                    "critical": observed.get("critical", gold.get("critical", False)),
                }
            )
        )
    return normalized


def _normalize_claim(claim: dict[str, Any]) -> dict[str, Any]:
    status = str(claim.get("status") or "not_verifiable")
    if status not in SUPPORTED_CLAIM_STATUSES:
        status = "not_verifiable"
    normalized = dict(claim)
    normalized["status"] = status
    return normalized


def _normalized_citations(obs: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(citation) for citation in obs.get("citations") or [] if isinstance(citation, dict)]


def _binary_metric(name: str, value: bool, *, applicable: bool = True) -> MetricMeasurement:
    return metric_measurement(name, numerator=1 if value else 0, denominator=1 if applicable else 0, threshold=1.0)


def _optional_match(expected: Any, observed: Any) -> bool:
    if expected is None:
        return False
    return _values_match(expected, observed)


def _values_match(expected: Any, observed: Any) -> bool:
    if expected is None and observed is None:
        return True
    if expected is None or observed is None:
        return False
    if isinstance(expected, int | float) or isinstance(observed, int | float):
        try:
            return abs(float(expected) - float(observed)) <= 1e-9
        except (TypeError, ValueError):
            return False
    return str(expected).strip().lower() == str(observed).strip().lower()


def _artifact_names(artifacts: list[str | dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for artifact in artifacts:
        if isinstance(artifact, str):
            names.add(Path(artifact).name)
        elif isinstance(artifact, dict):
            for key in ("name", "path", "artifact_name"):
                if artifact.get(key):
                    names.add(Path(str(artifact[key])).name)
    return names


def _normalize_provenance(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"real", "real_lammps", "actual", "executed"}:
        return "real"
    if raw in {"mock", "synthetic", "contract", "fixture", "dry_run", "simulated"}:
        return raw
    return raw or None


def _claims_real_execution(text: str) -> bool:
    normalized = text.lower()
    return any(phrase in normalized for phrase in REAL_EXECUTION_PHRASES)


def _claims_success(text: str) -> bool:
    normalized = text.lower()
    if any(phrase in normalized for phrase in NEGATION_PHRASES):
        return False
    return any(phrase in normalized for phrase in SUCCESS_PHRASES)


def _citation_supports(citation: dict[str, Any]) -> bool:
    if citation.get("exists") is False or citation.get("hash_verified") is False:
        return False
    if citation.get("supports") is False:
        return False
    return bool(citation.get("supports") is True or citation.get("evidence_id") or citation.get("source") or citation.get("source_ref"))
