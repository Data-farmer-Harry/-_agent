from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.orchestration.dag import DAGNode, DAGNodeResult, DAGPlan, get_downstream_nodes
from app.orchestration.executor import DAGExecutionResult
from app.orchestration.fingerprint import reusable_node_ids


FailureCategory = Literal[
    "none",
    "optional_timeout_or_failure",
    "node_timeout",
    "global_timeout",
    "missing_handler",
    "blocked_dependency",
    "critical_failure",
    "infrastructure_missing",
    "needs_user",
    "unknown",
]
DegradationLevel = Literal["none", "level_1_fallback", "level_2_replan", "level_3_partial_report"]


class FailureFinding(BaseModel):
    node_id: str
    status: str
    critical: bool
    category: FailureCategory
    message: str = ""
    repairable: bool = False
    needs_user: bool = False
    fallback_available: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FailureBatch(BaseModel):
    batch_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    failed_nodes: list[str] = Field(default_factory=list)
    findings: list[FailureFinding] = Field(default_factory=list)
    repairable: bool = False
    needs_user: bool = False
    restart_from: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanBudgetState(BaseModel):
    repair_budget: int = 2
    replan_budget: int = 2
    previous_failure_signatures: list[str] = Field(default_factory=list)
    oscillation_threshold: int = 2


class PartialReport(BaseModel):
    success: bool = False
    termination_reason: str = "global_timeout"
    completed_nodes: list[str] = Field(default_factory=list)
    unfinished_nodes: list[str] = Field(default_factory=list)
    failed_nodes: list[str] = Field(default_factory=list)
    available_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    last_checkpoint_id: str = ""
    resume_supported: bool = True
    scientific_result_available: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanDecision(BaseModel):
    degradation_level: DegradationLevel = "none"
    can_continue: bool = True
    lifecycle_target: str = "ready"
    termination_reason: str = ""
    fallback_nodes: list[str] = Field(default_factory=list)
    invalidated_nodes: list[str] = Field(default_factory=list)
    reused_nodes: list[str] = Field(default_factory=list)
    failure_batch: FailureBatch | None = None
    new_plan: DAGPlan | None = None
    partial_report: PartialReport | None = None
    repair_budget_remaining: int = 0
    replan_budget_remaining: int = 0
    oscillation_detected: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def classify_node_failure(node: DAGNode, result: DAGNodeResult) -> FailureFinding | None:
    if result.status in {"completed", "completed_with_fallback"}:
        return None

    error = result.error or result.failure_category
    category: FailureCategory = "unknown"
    if result.failure_category == "global_timeout":
        category = "global_timeout"
    elif result.failure_category == "node_timeout" or result.status == "timed_out":
        category = "node_timeout"
    elif result.failure_category == "missing_handler":
        category = "missing_handler"
    elif result.failure_category == "blocked_dependency" or result.status == "skipped":
        category = "blocked_dependency"
    elif _looks_like_user_clarification(error):
        category = "needs_user"
    elif _looks_like_infrastructure(error):
        category = "infrastructure_missing"
    elif not node.critical:
        category = "optional_timeout_or_failure"
    else:
        category = "critical_failure"

    fallback_available = (not node.critical) and bool(node.metadata.get("fallback") or node.metadata.get("deterministic_fallback"))
    repairable = category in {"critical_failure", "node_timeout", "blocked_dependency"} and node.retryable
    needs_user = category == "needs_user"
    return FailureFinding(
        node_id=node.node_id,
        status=result.status,
        critical=node.critical,
        category=category,
        message=error,
        repairable=repairable,
        needs_user=needs_user,
        fallback_available=fallback_available,
        evidence_refs=list(result.evidence_refs),
        metadata={
            "node_type": node.node_type,
            "resource_class": node.resource_class,
            "retryable": node.retryable,
            "max_attempts": node.max_attempts,
        },
    )


def merge_failure_batch(plan: DAGPlan, result: DAGExecutionResult) -> FailureBatch | None:
    node_by_id = plan.node_map()
    findings: list[FailureFinding] = []
    seen: set[tuple[str, FailureCategory, str]] = set()
    for node_id in plan.topological_order():
        node_result = result.results.get(node_id)
        if node_result is None:
            synthetic = DAGNodeResult(
                node_id=node_id,
                status="skipped",
                error="Node did not finish before DAG ended.",
                failure_category="unfinished",
            )
            finding = classify_node_failure(node_by_id[node_id], synthetic)
        else:
            finding = classify_node_failure(node_by_id[node_id], node_result)
        if finding is None:
            continue
        key = (finding.node_id, finding.category, finding.message)
        if key in seen:
            continue
        seen.add(key)
        findings.append(finding)

    if not findings:
        return None

    failed_nodes = [finding.node_id for finding in findings]
    restart_from = next((node_id for node_id in plan.topological_order() if node_id in failed_nodes), failed_nodes[0])
    return FailureBatch(
        failed_nodes=failed_nodes,
        findings=findings,
        repairable=any(finding.repairable for finding in findings),
        needs_user=any(finding.needs_user for finding in findings),
        restart_from=restart_from,
        metadata={
            "critical_failures": [finding.node_id for finding in findings if finding.critical],
            "optional_failures": [finding.node_id for finding in findings if not finding.critical],
        },
    )


def decide_degradation(
    plan: DAGPlan,
    result: DAGExecutionResult,
    *,
    budget_state: ReplanBudgetState | None = None,
    available_artifacts: list[dict[str, Any]] | None = None,
    last_checkpoint_id: str = "",
) -> ReplanDecision:
    budget_state = budget_state or ReplanBudgetState()
    batch = merge_failure_batch(plan, result)
    if batch is None and result.status == "completed":
        return ReplanDecision(
            degradation_level="none",
            can_continue=True,
            lifecycle_target="ready",
            repair_budget_remaining=budget_state.repair_budget,
            replan_budget_remaining=budget_state.replan_budget,
        )

    if result.status == "timed_out" or (batch and any(finding.category == "global_timeout" for finding in batch.findings)):
        partial = build_partial_report(
            plan,
            result,
            available_artifacts=available_artifacts or [],
            last_checkpoint_id=last_checkpoint_id,
            failure_batch=batch,
        )
        return ReplanDecision(
            degradation_level="level_3_partial_report",
            can_continue=False,
            lifecycle_target="terminated",
            termination_reason="global_timeout",
            failure_batch=batch,
            partial_report=partial,
            repair_budget_remaining=budget_state.repair_budget,
            replan_budget_remaining=budget_state.replan_budget,
        )

    assert batch is not None
    if _is_level_1_fallback(batch):
        fallback_nodes = [finding.node_id for finding in batch.findings if finding.fallback_available]
        return ReplanDecision(
            degradation_level="level_1_fallback",
            can_continue=True,
            lifecycle_target="ready",
            fallback_nodes=fallback_nodes,
            failure_batch=batch,
            repair_budget_remaining=budget_state.repair_budget,
            replan_budget_remaining=budget_state.replan_budget,
            metadata={"trust_penalty": 0.08 * len(fallback_nodes)},
        )

    signature = failure_signature(batch)
    oscillation_detected = budget_state.previous_failure_signatures.count(signature) >= budget_state.oscillation_threshold
    if batch.needs_user:
        return ReplanDecision(
            degradation_level="level_2_replan",
            can_continue=False,
            lifecycle_target="terminated",
            termination_reason="needs_user_clarification",
            failure_batch=batch,
            repair_budget_remaining=budget_state.repair_budget,
            replan_budget_remaining=budget_state.replan_budget,
            oscillation_detected=oscillation_detected,
        )
    if budget_state.repair_budget <= 0 or budget_state.replan_budget <= 0 or oscillation_detected:
        return ReplanDecision(
            degradation_level="level_2_replan",
            can_continue=False,
            lifecycle_target="terminated",
            termination_reason="repair_budget_exhausted",
            failure_batch=batch,
            invalidated_nodes=_invalidated_nodes(plan, batch.failed_nodes),
            repair_budget_remaining=max(0, budget_state.repair_budget),
            replan_budget_remaining=max(0, budget_state.replan_budget),
            oscillation_detected=oscillation_detected,
        )

    invalidated = _invalidated_nodes(plan, batch.failed_nodes)
    reused, non_reusable = reusable_node_ids(plan, result, invalidated_nodes=set(invalidated))
    new_plan = build_replan_plan(
        plan,
        invalidated_nodes=invalidated,
        reusable_nodes=reused,
        non_reusable_nodes=non_reusable,
        reason=batch.restart_from,
    )
    return ReplanDecision(
        degradation_level="level_2_replan",
        can_continue=True,
        lifecycle_target="repairing",
        failure_batch=batch,
        invalidated_nodes=invalidated,
        reused_nodes=reused,
        new_plan=new_plan,
        repair_budget_remaining=budget_state.repair_budget - 1,
        replan_budget_remaining=budget_state.replan_budget - 1,
        oscillation_detected=oscillation_detected,
        metadata={"failure_signature": signature, "non_reusable_nodes": non_reusable},
    )


def build_replan_plan(
    plan: DAGPlan,
    *,
    invalidated_nodes: list[str],
    reason: str,
    created_from: str = "repair",
    reusable_nodes: list[str] | None = None,
    non_reusable_nodes: list[str] | None = None,
) -> DAGPlan:
    invalidated_set = set(invalidated_nodes)
    reusable_set = set(reusable_nodes or [])
    non_reusable_set = set(non_reusable_nodes or [])
    nodes = []
    for node in plan.nodes:
        metadata = dict(node.metadata)
        if node.node_id in invalidated_set:
            metadata["replan_status"] = "rerun"
            metadata["replan_reason"] = reason
        elif node.node_id in reusable_set:
            metadata["replan_status"] = "reuse_checkpoint"
            metadata["replan_reason"] = "content_hash_reuse"
        elif node.node_id in non_reusable_set:
            metadata["replan_status"] = "rerun"
            metadata["replan_reason"] = "missing_or_unsafe_reuse_fingerprint"
        else:
            metadata["replan_status"] = "rerun"
            metadata["replan_reason"] = "no_reuse_decision"
        nodes.append(node.model_copy(update={"metadata": metadata}))
    return plan.model_copy(
        update={
            "plan_id": f"{plan.plan_id}/replan-{plan.plan_version + 1}",
            "plan_version": plan.plan_version + 1,
            "nodes": nodes,
            "metadata": {
                **plan.metadata,
                "created_from": created_from,
                "old_plan_id": plan.plan_id,
                "invalidated_nodes": invalidated_nodes,
                "reusable_nodes": reusable_nodes or [],
                "non_reusable_nodes": non_reusable_nodes or [],
                "replan_reason": reason,
            },
        }
    )


def build_partial_report(
    plan: DAGPlan,
    result: DAGExecutionResult,
    *,
    available_artifacts: list[dict[str, Any]] | None = None,
    last_checkpoint_id: str = "",
    failure_batch: FailureBatch | None = None,
) -> PartialReport:
    completed_nodes = [
        node_id
        for node_id in plan.topological_order()
        if result.results.get(node_id) and result.results[node_id].status in {"completed", "completed_with_fallback"}
    ]
    failed_nodes = [
        node_id
        for node_id in plan.topological_order()
        if result.results.get(node_id) and result.results[node_id].status in {"failed", "timed_out"}
    ]
    unfinished_nodes = [
        node_id
        for node_id in plan.topological_order()
        if node_id not in result.results or result.results[node_id].status == "skipped"
    ]
    evidence_refs: list[str] = []
    for node_result in result.results.values():
        for ref in node_result.evidence_refs:
            if ref not in evidence_refs:
                evidence_refs.append(ref)
    return PartialReport(
        completed_nodes=completed_nodes,
        unfinished_nodes=unfinished_nodes,
        failed_nodes=failed_nodes,
        available_artifacts=available_artifacts or [],
        evidence_refs=evidence_refs,
        last_checkpoint_id=last_checkpoint_id,
        resume_supported=bool(last_checkpoint_id),
        scientific_result_available=False,
        metadata={
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "dag_status": result.status,
            "failure_batch_id": failure_batch.batch_id if failure_batch else "",
        },
    )


def apply_level_1_fallbacks(result: DAGExecutionResult, decision: ReplanDecision) -> DAGExecutionResult:
    if decision.degradation_level != "level_1_fallback" or not decision.fallback_nodes:
        return result
    updated_results = dict(result.results)
    for node_id in decision.fallback_nodes:
        node_result = updated_results.get(node_id)
        if node_result is None:
            continue
        updated_results[node_id] = node_result.model_copy(
            update={
                "status": "completed_with_fallback",
                "metadata": {
                    **node_result.metadata,
                    "degradation_level": "level_1_fallback",
                    "original_status": node_result.status,
                    "fallback_applied": True,
                },
            }
        )
    return result.model_copy(update={"status": "completed", "results": updated_results})


def failure_signature(batch: FailureBatch) -> str:
    raw = "|".join(f"{finding.node_id}:{finding.category}:{finding.message}" for finding in batch.findings)
    return sha256(raw.encode("utf-8")).hexdigest()


def _invalidated_nodes(plan: DAGPlan, failed_nodes: list[str]) -> list[str]:
    failed = set(failed_nodes)
    downstream = set(get_downstream_nodes(plan, failed))
    return [node_id for node_id in plan.topological_order() if node_id in failed or node_id in downstream]


def _is_level_1_fallback(batch: FailureBatch) -> bool:
    if not batch.findings:
        return False
    return all((not finding.critical) and finding.fallback_available for finding in batch.findings)


def _looks_like_infrastructure(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "executable not found",
            "potential file not found",
            "structure file not found",
            "not configured",
            "missing_handler",
        )
    )


def _looks_like_user_clarification(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ("needs_user", "clarification", "missing user", "需要用户", "需要补充"))
