from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.orchestration import DAGNode, DAGPlan, validate_dag_plan


LAMMPS_PREFLIGHT_NODE_IDS = [
    "constraint_extract",
    "materials_rag_search",
    "registry_lookup",
    "attachment_inspection",
    "runtime_diagnostics",
    "preflight_merge",
    "red_pre_execution_review",
]

DEFAULT_PREFLIGHT_TIMEOUTS: dict[str, float] = {
    "constraint_extract": 45.0,
    "materials_rag_search": 45.0,
    "registry_lookup": 45.0,
    "attachment_inspection": 45.0,
    "runtime_diagnostics": 45.0,
    "preflight_merge": 30.0,
    "red_pre_execution_review": 120.0,
}


def build_lammps_preflight_plan(
    *,
    plan_version: int = 1,
    requires_attachment: bool = False,
    timeout_overrides: Mapping[str, float] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> DAGPlan:
    """Build the first-stage LAMMPS preflight DAG without touching runtime state."""

    timeouts = {**DEFAULT_PREFLIGHT_TIMEOUTS, **dict(timeout_overrides or {})}
    first_stage = [
        DAGNode(
            node_id="constraint_extract",
            node_type="lammps.constraint_extract",
            resource_class="cpu",
            timeout_seconds=timeouts["constraint_extract"],
            critical=True,
            input_keys=["message", "conversation_context"],
            output_keys=["request_constraints", "locked_constraints"],
        ),
        DAGNode(
            node_id="materials_rag_search",
            node_type="lammps.materials_rag_search",
            resource_class="network",
            timeout_seconds=timeouts["materials_rag_search"],
            critical=False,
            retryable=True,
            max_attempts=2,
            input_keys=["message", "request_constraints"],
            output_keys=["rag_context", "evidence_refs"],
            metadata={"fallback": "continue_with_registry_and_user_input"},
        ),
        DAGNode(
            node_id="registry_lookup",
            node_type="lammps.registry_lookup",
            resource_class="cpu",
            timeout_seconds=timeouts["registry_lookup"],
            critical=True,
            input_keys=["request_constraints"],
            output_keys=["registry_match", "registry_evidence_refs"],
        ),
        DAGNode(
            node_id="attachment_inspection",
            node_type="lammps.attachment_inspection",
            resource_class="cpu",
            timeout_seconds=timeouts["attachment_inspection"],
            critical=requires_attachment,
            input_keys=["uploaded_assets"],
            output_keys=["attachment_summary", "attachment_overrides"],
            metadata={"skip_when_empty": True},
        ),
        DAGNode(
            node_id="runtime_diagnostics",
            node_type="lammps.runtime_diagnostics",
            resource_class="cpu",
            timeout_seconds=timeouts["runtime_diagnostics"],
            critical=True,
            input_keys=["runtime_config"],
            output_keys=["runtime_diagnostics", "environment_evidence_refs"],
        ),
    ]
    merge = DAGNode(
        node_id="preflight_merge",
        node_type="lammps.preflight_merge",
        dependencies=[node.node_id for node in first_stage],
        resource_class="cpu",
        timeout_seconds=timeouts["preflight_merge"],
        critical=True,
        input_keys=["request_constraints", "rag_context", "registry_match", "attachment_summary", "runtime_diagnostics"],
        output_keys=["preflight_report", "merged_evidence_refs"],
    )
    red_review = DAGNode(
        node_id="red_pre_execution_review",
        node_type="lammps.red_pre_execution_review",
        dependencies=["preflight_merge"],
        resource_class="network",
        timeout_seconds=timeouts["red_pre_execution_review"],
        critical=True,
        retryable=True,
        max_attempts=2,
        input_keys=["preflight_report", "merged_evidence_refs"],
        output_keys=["red_review_report", "repair_intent"],
        metadata={"deterministic_fallback": True},
    )
    plan = DAGPlan(
        plan_id="lammps-preflight/v1",
        plan_version=plan_version,
        nodes=[*first_stage, merge, red_review],
        metadata={
            "domain": "lammps",
            "stage": "preflight",
            "feature_flag": "lammps_preflight_dag_enabled",
            **dict(metadata or {}),
        },
    )
    validate_dag_plan(plan)
    return plan
