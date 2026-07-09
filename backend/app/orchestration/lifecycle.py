from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.orchestration.dag import DAGNodeResult, DAGPlan
from app.orchestration.executor import DAGExecutionEvent, DAGExecutionResult
from app.state import AgentStreamEvent
from app.utils.path_utils import ensure_directory, read_json_file_if_exists, write_json_file


LifecycleState = Literal[
    "queued",
    "planning",
    "preflight",
    "ready",
    "running",
    "reviewing",
    "repairing",
    "completed",
    "terminated",
]


ALLOWED_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    "queued": {"planning", "terminated"},
    "planning": {"preflight", "ready", "terminated"},
    "preflight": {"ready", "repairing", "terminated"},
    "ready": {"running", "terminated"},
    "running": {"reviewing", "repairing", "terminated"},
    "reviewing": {"completed", "repairing", "terminated"},
    "repairing": {"planning", "preflight", "ready", "terminated"},
    "completed": set(),
    "terminated": set(),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LifecycleTransitionError(ValueError):
    """Raised when an internal runtime lifecycle transition is invalid."""


class LifecycleEvent(BaseModel):
    event_type: str = "lifecycle.transition"
    run_id: str
    attempt: int = 1
    plan_version: int = 1
    from_state: LifecycleState
    to_state: LifecycleState
    reason: str
    emitted_at: str = Field(default_factory=_now_iso)
    termination_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeEventRecord(BaseModel):
    event_type: str
    run_id: str
    emitted_at: str = Field(default_factory=_now_iso)
    plan_version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckpointRecord(BaseModel):
    schema_version: str = "agent-checkpoint/v1"
    checkpoint_id: str
    run_id: str
    created_at: str = Field(default_factory=_now_iso)
    attempt: int = 1
    plan_id: str = ""
    plan_version: int = 1
    stage: str
    lifecycle_state: LifecycleState
    node_id: str = ""
    completed_nodes: list[str] = Field(default_factory=list)
    failed_nodes: list[str] = Field(default_factory=list)
    timed_out_nodes: list[str] = Field(default_factory=list)
    skipped_nodes: list[str] = Field(default_factory=list)
    pending_nodes: list[str] = Field(default_factory=list)
    result_status: str = ""
    path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunLifecycleState(BaseModel):
    schema_version: str = "agent-lifecycle/v1"
    run_id: str
    attempt: int = 1
    current_state: LifecycleState = "queued"
    current_plan_version: int = 1
    events: list[LifecycleEvent | RuntimeEventRecord] = Field(default_factory=list)
    checkpoints: list[CheckpointRecord] = Field(default_factory=list)
    last_checkpoint_id: str = ""
    termination_reason: str = ""


class TaskLifecycleController:
    """Validate runtime lifecycle transitions and persist restart-readable checkpoints."""

    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        event_sink=None,
        attempt: int = 1,
        initial_state: LifecycleState = "queued",
    ) -> None:
        self.run_id = run_id
        self.run_dir = ensure_directory(run_dir)
        self.checkpoint_dir = ensure_directory(self.run_dir / "checkpoints")
        self.event_sink = event_sink
        self.state = RunLifecycleState(run_id=run_id, attempt=attempt, current_state=initial_state)
        self._persist_state()

    @property
    def lifecycle_path(self) -> Path:
        return self.run_dir / "lifecycle.json"

    def snapshot(self) -> dict[str, Any]:
        return self.state.model_dump(mode="json")

    def transition(
        self,
        *,
        to_state: LifecycleState,
        reason: str,
        plan_version: int | None = None,
        metadata: dict[str, Any] | None = None,
        termination_reason: str = "",
    ) -> LifecycleEvent:
        from_state = self.state.current_state
        if to_state not in ALLOWED_TRANSITIONS[from_state]:
            raise LifecycleTransitionError(f"Invalid lifecycle transition: {from_state} -> {to_state}")
        if to_state == "terminated" and not termination_reason:
            raise LifecycleTransitionError("terminated lifecycle state requires termination_reason")

        effective_plan_version = plan_version or self.state.current_plan_version
        event = LifecycleEvent(
            run_id=self.run_id,
            attempt=self.state.attempt,
            plan_version=effective_plan_version,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            termination_reason=termination_reason,
            metadata=metadata or {},
        )
        self.state.current_state = to_state
        self.state.current_plan_version = effective_plan_version
        if termination_reason:
            self.state.termination_reason = termination_reason
        self.state.events.append(event)
        self._persist_state()
        self._emit("lifecycle_event", event.model_dump(mode="json"))
        return event

    def record_plan_created(self, plan: DAGPlan, *, metadata: dict[str, Any] | None = None) -> RuntimeEventRecord:
        self.state.current_plan_version = plan.plan_version
        payload = {
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "node_ids": [node.node_id for node in plan.nodes],
            **(metadata or {}),
        }
        return self.record_event("plan.created", payload, plan_version=plan.plan_version, stream_type="lifecycle_event")

    def record_dag_event(self, event: DAGExecutionEvent) -> RuntimeEventRecord:
        return self.record_event(
            "dag.event",
            event.model_dump(mode="json"),
            plan_version=self.state.current_plan_version,
            stream_type="dag_event",
        )

    def record_event(
        self,
        event_type: str,
        metadata: dict[str, Any],
        *,
        plan_version: int | None = None,
        stream_type: str = "lifecycle_event",
    ) -> RuntimeEventRecord:
        record = RuntimeEventRecord(
            event_type=event_type,
            run_id=self.run_id,
            plan_version=plan_version or self.state.current_plan_version,
            metadata=metadata,
        )
        self.state.events.append(record)
        self._persist_state()
        self._emit(stream_type, record.model_dump(mode="json"))
        return record

    def save_checkpoint(
        self,
        *,
        stage: str,
        plan: DAGPlan | None = None,
        results: dict[str, DAGNodeResult] | None = None,
        dag_result: DAGExecutionResult | None = None,
        node_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointRecord:
        plan_id = plan.plan_id if plan is not None else ""
        plan_version = plan.plan_version if plan is not None else self.state.current_plan_version
        node_order = [node.node_id for node in plan.nodes] if plan is not None else []
        result_map = dict(results or {})
        if dag_result is not None:
            result_map.update(dag_result.results)
            if not node_order:
                node_order = list(dag_result.topological_order)
                plan_id = dag_result.plan_id

        completed = [node for node in node_order if result_map.get(node) and result_map[node].status == "completed"]
        failed = [node for node in node_order if result_map.get(node) and result_map[node].status == "failed"]
        timed_out = [node for node in node_order if result_map.get(node) and result_map[node].status == "timed_out"]
        skipped = [node for node in node_order if result_map.get(node) and result_map[node].status == "skipped"]
        pending = [node for node in node_order if node not in result_map]
        checkpoint_metadata = dict(metadata or {})
        checkpoint_metadata.setdefault(
            "node_result_hashes",
            {node_id: str(node_result.metadata.get("result_hash") or "") for node_id, node_result in result_map.items()},
        )
        checkpoint_metadata.setdefault(
            "node_content_hashes",
            {node_id: str(node_result.metadata.get("content_hash") or "") for node_id, node_result in result_map.items()},
        )
        checkpoint_metadata.setdefault(
            "node_input_hashes",
            {node_id: str(node_result.metadata.get("input_hash") or "") for node_id, node_result in result_map.items()},
        )
        checkpoint_metadata.setdefault(
            "node_fingerprints",
            {node_id: str(node_result.metadata.get("node_fingerprint") or "") for node_id, node_result in result_map.items()},
        )
        checkpoint_metadata.setdefault(
            "node_reuse_status",
            {
                node_id: "reused" if node_result.metadata.get("reused_from_checkpoint") else "fresh"
                for node_id, node_result in result_map.items()
            },
        )
        checkpoint_id = f"{stage}-{uuid4().hex[:8]}"
        checkpoint_path = self.checkpoint_dir / f"{checkpoint_id}.json"
        checkpoint = CheckpointRecord(
            checkpoint_id=checkpoint_id,
            run_id=self.run_id,
            attempt=self.state.attempt,
            plan_id=plan_id,
            plan_version=plan_version,
            stage=stage,
            lifecycle_state=self.state.current_state,
            node_id=node_id,
            completed_nodes=completed,
            failed_nodes=failed,
            timed_out_nodes=timed_out,
            skipped_nodes=skipped,
            pending_nodes=pending,
            result_status=dag_result.status if dag_result is not None else "",
            path=str(checkpoint_path),
            metadata=checkpoint_metadata,
        )
        write_json_file(
            checkpoint_path,
            {
                **checkpoint.model_dump(mode="json"),
                "results": {key: value.model_dump(mode="json") for key, value in result_map.items()},
                "dag_result": dag_result.model_dump(mode="json") if dag_result is not None else {},
                "plan": plan.model_dump(mode="json") if plan is not None else {},
            },
        )
        self.state.checkpoints.append(checkpoint)
        self.state.last_checkpoint_id = checkpoint.checkpoint_id
        self.state.current_plan_version = plan_version
        self._persist_state()
        self._emit("checkpoint_saved", checkpoint.model_dump(mode="json"))
        return checkpoint

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.event_sink:
            return
        self.event_sink(AgentStreamEvent(type=event_type, run_id=self.run_id, payload=payload))

    def _persist_state(self) -> None:
        write_json_file(self.lifecycle_path, self.state.model_dump(mode="json"))


def load_lifecycle_state(run_dir: Path) -> RunLifecycleState | None:
    payload = read_json_file_if_exists(run_dir / "lifecycle.json")
    if payload is None:
        return None
    return RunLifecycleState.model_validate(payload)
