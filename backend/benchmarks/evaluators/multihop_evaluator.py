from __future__ import annotations

from typing import Any

from benchmarks.evaluators.rule_evaluator import evaluate_rule_layer
from benchmarks.materials_agent_bench import MaterialsAgentBenchCase, MaterialsAgentBenchResult, MetricMeasurement, metric_measurement


AUTHORITY_RANKS = {
    "user_constraint": 100,
    "registry": 90,
    "quality_gate": 85,
    "input_script": 80,
    "run_log": 80,
    "thermo": 80,
    "red_review": 75,
    "blue_patch": 70,
    "rag": 60,
    "final_answer": 50,
}
UNSUPPORTED_BRIDGE_STATUSES = {"unsupported", "contradicted"}
HONEST_MISSING_HOP_PHRASES = (
    "insufficient evidence",
    "missing evidence",
    "cannot verify",
    "not enough evidence",
    "缺少证据",
    "证据不足",
    "无法验证",
    "不能确认",
)


def evaluate_materials_multihop(
    case: MaterialsAgentBenchCase,
    observation: dict[str, Any],
) -> MaterialsAgentBenchResult:
    base = evaluate_rule_layer(case, observation)
    metrics: dict[str, MetricMeasurement] = dict(base.metrics)
    critical_failures = list(base.critical_failures)
    required_hops = _required_hops(case, observation)
    observed_hops = _observed_hops(observation)
    citations = [dict(item) for item in observation.get("citations") or [] if isinstance(item, dict)]
    claims = list(base.claims)

    completed_hops = _completed_hop_ids(observed_hops)
    required_hop_ids = [str(hop.get("hop_id")) for hop in required_hops if hop.get("hop_id")]
    completed_required = [hop_id for hop_id in required_hop_ids if hop_id in completed_hops]
    missing_hop_ids = [hop_id for hop_id in required_hop_ids if hop_id not in completed_hops]
    metrics["required_hop_completion"] = metric_measurement(
        "required_hop_completion",
        numerator=len(completed_required),
        denominator=len(required_hop_ids),
        threshold=float(case.metadata.get("min_hop_completion") or 1.0),
    )

    supported_citation_ids = _supporting_citation_ids(citations)
    evidence_completed = [
        hop
        for hop in required_hops
        if str(hop.get("hop_id")) in completed_hops and str(hop.get("evidence_id")) in supported_citation_ids
    ]
    metrics["evidence_chain_completeness"] = metric_measurement(
        "evidence_chain_completeness",
        numerator=len(evidence_completed),
        denominator=len(required_hops),
        threshold=1.0,
    )

    bridge_claims = [claim for claim in claims if claim.get("bridge") is True or claim.get("claim_type") == "bridge"]
    unsupported_bridge_claims = [claim for claim in bridge_claims if claim.get("status") in UNSUPPORTED_BRIDGE_STATUSES]
    metrics["unsupported_bridge_claim_rate"] = metric_measurement(
        "unsupported_bridge_claim_rate",
        numerator=len(unsupported_bridge_claims),
        denominator=len(bridge_claims),
        threshold=0.0,
        greater_is_better=False,
    )
    metrics["no_unsupported_bridge_claim_rate"] = metric_measurement(
        "no_unsupported_bridge_claim_rate",
        numerator=len(bridge_claims) - len(unsupported_bridge_claims),
        denominator=len(bridge_claims),
        threshold=1.0,
    )

    expected_conclusion = case.metadata.get("expected_conclusion")
    observed_conclusion = observation.get("final_conclusion")
    conclusion_ok = expected_conclusion is not None and str(expected_conclusion) == str(observed_conclusion)
    metrics["final_conclusion_correctness"] = metric_measurement(
        "final_conclusion_correctness",
        numerator=1 if conclusion_ok else 0,
        denominator=1 if expected_conclusion is not None else 0,
        threshold=1.0,
    )

    citation_order_authority_ok = _citation_order_and_authority_ok(required_hops, citations)
    metrics["citation_order_authority_rate"] = metric_measurement(
        "citation_order_authority_rate",
        numerator=1 if citation_order_authority_ok else 0,
        denominator=1 if required_hops else 0,
        threshold=1.0,
    )

    missing_hop_honesty_ok = _missing_hop_honesty_ok(missing_hop_ids, observation)
    metrics["missing_hop_honesty_rate"] = metric_measurement(
        "missing_hop_honesty_rate",
        numerator=1 if missing_hop_honesty_ok else 0,
        denominator=1,
        threshold=1.0,
    )

    if missing_hop_ids and not missing_hop_honesty_ok:
        critical_failures.append(f"missing required hop(s) without disclosure: {', '.join(missing_hop_ids)}")
    if unsupported_bridge_claims:
        claim_ids = [str(claim.get("claim_id") or claim.get("text") or "bridge_claim") for claim in unsupported_bridge_claims]
        critical_failures.append(f"unsupported bridge claim(s): {', '.join(claim_ids)}")
    if expected_conclusion is not None and not conclusion_ok:
        critical_failures.append(f"final conclusion mismatch: expected {expected_conclusion!r}, observed {observed_conclusion!r}")
    if required_hops and not citation_order_authority_ok:
        critical_failures.append("citation order or authority does not satisfy required hop chain")
    if required_hops and len(evidence_completed) != len(required_hops):
        critical_failures.append("evidence chain is incomplete or unsupported by citations")

    hard_gate_passed = not critical_failures
    metrics["critical_hallucination_rate"] = metric_measurement(
        "critical_hallucination_rate",
        numerator=1 if critical_failures else 0,
        denominator=1,
        threshold=0.0,
        greater_is_better=False,
        notes=[f"{len(critical_failures)} critical failure(s)"],
    )
    passed = hard_gate_passed and all(metric.passed is not False for metric in metrics.values())
    return MaterialsAgentBenchResult(
        case_id=case.case_id,
        passed=passed,
        hard_gate_passed=hard_gate_passed,
        metrics=metrics,
        critical_failures=critical_failures,
        claims=claims,
        citations=citations,
        required_hops=_merge_hop_completion(required_hops, completed_hops),
        evidence_refs=list(observation.get("evidence_refs") or []),
        metadata={
            **dict(base.metadata),
            "multihop_evaluator_version": "materials-multihop-evaluator/v1",
        },
    )


def _required_hops(case: MaterialsAgentBenchCase, observation: dict[str, Any]) -> list[dict[str, Any]]:
    raw_hops = case.metadata.get("required_hops") or observation.get("required_hops") or []
    return [dict(hop) for hop in raw_hops if isinstance(hop, dict)]


def _observed_hops(observation: dict[str, Any]) -> list[dict[str, Any]]:
    raw_hops = observation.get("required_hops") or observation.get("hops") or []
    return [dict(hop) for hop in raw_hops if isinstance(hop, dict)]


def _completed_hop_ids(hops: list[dict[str, Any]]) -> set[str]:
    return {str(hop.get("hop_id")) for hop in hops if hop.get("hop_id") and hop.get("completed") is True}


def _supporting_citation_ids(citations: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for citation in citations:
        if citation.get("supports") is False or citation.get("exists") is False or citation.get("hash_verified") is False:
            continue
        evidence_id = citation.get("evidence_id") or citation.get("source") or citation.get("source_ref")
        if evidence_id:
            ids.add(str(evidence_id))
    return ids


def _citation_order_and_authority_ok(required_hops: list[dict[str, Any]], citations: list[dict[str, Any]]) -> bool:
    if not required_hops:
        return True
    citation_by_id = {
        str(citation.get("evidence_id") or citation.get("source") or citation.get("source_ref")): (index, citation)
        for index, citation in enumerate(citations)
    }
    last_index = -1
    for hop in required_hops:
        evidence_id = str(hop.get("evidence_id") or "")
        if evidence_id not in citation_by_id:
            return False
        index, citation = citation_by_id[evidence_id]
        if index <= last_index:
            return False
        last_index = index
        min_rank = int(hop.get("min_authority_rank") or AUTHORITY_RANKS.get(str(hop.get("authority") or ""), 0))
        if _authority_rank(citation, evidence_id) < min_rank:
            return False
    return True


def _authority_rank(citation: dict[str, Any], evidence_id: str) -> int:
    authority = str(citation.get("authority") or evidence_id.split(":", 1)[0])
    return AUTHORITY_RANKS.get(authority, 0)


def _missing_hop_honesty_ok(missing_hop_ids: list[str], observation: dict[str, Any]) -> bool:
    if not missing_hop_ids:
        return True
    if observation.get("missing_hop_disclosure") is True:
        return True
    final_response = str(observation.get("final_response") or "").lower()
    return any(phrase in final_response for phrase in HONEST_MISSING_HOP_PHRASES)


def _merge_hop_completion(required_hops: list[dict[str, Any]], completed_hops: set[str]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for hop in required_hops:
        payload = dict(hop)
        payload["completed"] = str(payload.get("hop_id")) in completed_hops
        merged.append(payload)
    return merged
