from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ResourceClass = Literal["network", "cpu", "simulation"]
NodeStatus = Literal["completed", "completed_with_fallback", "failed", "timed_out", "skipped"]


class DAGValidationError(ValueError):
    """Raised when a DAG plan cannot be safely executed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DAGNode(BaseModel):
    node_id: str
    node_type: str
    dependencies: list[str] = Field(default_factory=list)
    resource_class: ResourceClass = "cpu"
    timeout_seconds: float = 45.0
    critical: bool = True
    retryable: bool = False
    max_attempts: int = 1
    input_keys: list[str] = Field(default_factory=list)
    output_keys: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("node_id", "node_type")
    @classmethod
    def _require_non_empty_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("DAG identifiers must be non-empty")
        return normalized

    @field_validator("dependencies", "input_keys", "output_keys")
    @classmethod
    def _dedupe_preserving_order(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            item = str(value).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    @field_validator("timeout_seconds")
    @classmethod
    def _require_positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        return value

    @field_validator("max_attempts")
    @classmethod
    def _require_positive_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_attempts must be at least 1")
        return value


class DAGPlan(BaseModel):
    plan_id: str
    plan_version: int = 1
    nodes: list[DAGNode]
    global_timeout_seconds: float = 35 * 60
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("plan_id")
    @classmethod
    def _require_plan_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("plan_id must be non-empty")
        return normalized

    @field_validator("global_timeout_seconds")
    @classmethod
    def _require_positive_global_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("global_timeout_seconds must be positive")
        return value

    def node_map(self) -> dict[str, DAGNode]:
        return {node.node_id: node for node in self.nodes}

    def validate_topology(self) -> None:
        validate_dag_plan(self)

    def topological_order(self) -> list[str]:
        return topological_sort(self)


class DAGExecutionContext(BaseModel):
    run_id: str = ""
    conversation_id: str = "default"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    config_signature: str = ""
    global_timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DAGNodeResult(BaseModel):
    node_id: str
    status: NodeStatus
    attempt: int = 1
    started_at: str = Field(default_factory=_now_iso)
    finished_at: str = Field(default_factory=_now_iso)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    failure_category: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    checkpoint_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_dag_plan(plan: DAGPlan) -> None:
    """Validate duplicate IDs, missing dependencies and cycles."""

    seen: set[str] = set()
    duplicates: list[str] = []
    for node in plan.nodes:
        if node.node_id in seen:
            duplicates.append(node.node_id)
        seen.add(node.node_id)
    if duplicates:
        joined = ", ".join(sorted(set(duplicates)))
        raise DAGValidationError(f"Duplicate DAG node id(s): {joined}")

    node_ids = {node.node_id for node in plan.nodes}
    missing: dict[str, list[str]] = {}
    self_dependencies: list[str] = []
    for node in plan.nodes:
        absent = [dependency for dependency in node.dependencies if dependency not in node_ids]
        if absent:
            missing[node.node_id] = absent
        if node.node_id in node.dependencies:
            self_dependencies.append(node.node_id)
    if missing:
        details = "; ".join(f"{node}: {', '.join(deps)}" for node, deps in sorted(missing.items()))
        raise DAGValidationError(f"Missing DAG dependency reference(s): {details}")
    if self_dependencies:
        joined = ", ".join(sorted(self_dependencies))
        raise DAGValidationError(f"DAG node(s) cannot depend on themselves: {joined}")

    topological_sort(plan)


def topological_sort(plan: DAGPlan) -> list[str]:
    """Return a stable topological order preserving plan order among peers."""

    node_ids = [node.node_id for node in plan.nodes]
    order_index = {node_id: index for index, node_id in enumerate(node_ids)}
    remaining_dependencies = {node.node_id: set(node.dependencies) for node in plan.nodes}
    dependents: dict[str, list[str]] = {node.node_id: [] for node in plan.nodes}
    for node in plan.nodes:
        for dependency in node.dependencies:
            if dependency not in dependents:
                raise DAGValidationError(f"Missing DAG dependency reference(s): {node.node_id}: {dependency}")
            dependents[dependency].append(node.node_id)
    for children in dependents.values():
        children.sort(key=order_index.__getitem__)

    ready = deque(node_id for node_id in node_ids if not remaining_dependencies[node_id])
    result: list[str] = []
    while ready:
        node_id = ready.popleft()
        result.append(node_id)
        for child_id in dependents[node_id]:
            remaining_dependencies[child_id].discard(node_id)
            if not remaining_dependencies[child_id]:
                ready.append(child_id)

    if len(result) != len(plan.nodes):
        cyclic = sorted(node_id for node_id, deps in remaining_dependencies.items() if deps)
        raise DAGValidationError(f"DAG contains a cycle involving: {', '.join(cyclic)}")
    return result


def get_downstream_nodes(plan: DAGPlan, changed_node_ids: set[str] | list[str] | tuple[str, ...]) -> list[str]:
    """Return nodes invalidated by changes to the provided nodes, in topology order."""

    changed = set(changed_node_ids)
    validate_dag_plan(plan)
    dependents: dict[str, list[str]] = {node.node_id: [] for node in plan.nodes}
    for node in plan.nodes:
        for dependency in node.dependencies:
            dependents[dependency].append(node.node_id)

    invalidated: set[str] = set()
    queue = deque(changed)
    while queue:
        node_id = queue.popleft()
        for child_id in dependents.get(node_id, []):
            if child_id in invalidated:
                continue
            invalidated.add(child_id)
            queue.append(child_id)

    return [node_id for node_id in topological_sort(plan) if node_id in invalidated]
