from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.orchestration.dag import DAGExecutionContext, DAGNode, DAGNodeResult, DAGPlan, NodeStatus, ResourceClass, topological_sort, validate_dag_plan
from app.orchestration.fingerprint import build_result_fingerprint_metadata, can_reuse_cached_result, load_reuse_cache


DAGHandler = Callable[[DAGNode, DAGExecutionContext], dict[str, Any] | Awaitable[dict[str, Any]]]
ExecutionStatus = Literal["completed", "failed", "timed_out", "cancelled"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DAGResourceLimits(BaseModel):
    network: int = 3
    cpu: int = 2
    simulation: int = 1

    def as_dict(self) -> dict[ResourceClass, int]:
        return {"network": self.network, "cpu": self.cpu, "simulation": self.simulation}


class DAGExecutionEvent(BaseModel):
    event: str
    node_id: str = ""
    status: str = ""
    message: str = ""
    timestamp: str = Field(default_factory=_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DAGExecutionResult(BaseModel):
    plan_id: str
    status: ExecutionStatus
    results: dict[str, DAGNodeResult] = Field(default_factory=dict)
    events: list[DAGExecutionEvent] = Field(default_factory=list)
    topological_order: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def successful_node_ids(self) -> list[str]:
        return [
            node_id
            for node_id in self.topological_order
            if self.results.get(node_id) and self.results[node_id].status in {"completed", "completed_with_fallback"}
        ]


DAGEventSink = Callable[[DAGExecutionEvent], None]
DAGNodeResultSink = Callable[[DAGNodeResult], None]


class DAGExecutor:
    def __init__(self, resource_limits: DAGResourceLimits | None = None) -> None:
        self.resource_limits = resource_limits or DAGResourceLimits()

    async def run(
        self,
        plan: DAGPlan,
        context: DAGExecutionContext | None = None,
        handlers: Mapping[str, DAGHandler] | None = None,
        event_sink: DAGEventSink | None = None,
        node_result_sink: DAGNodeResultSink | None = None,
    ) -> DAGExecutionResult:
        validate_dag_plan(plan)
        context = context or DAGExecutionContext()
        handlers = handlers or {}
        order = topological_sort(plan)
        node_by_id = plan.node_map()
        semaphores = {resource: asyncio.Semaphore(limit) for resource, limit in self.resource_limits.as_dict().items()}
        timeout_seconds = context.global_timeout_seconds or plan.global_timeout_seconds
        deadline = time.monotonic() + timeout_seconds
        started = time.monotonic()
        pending = set(order)
        running: dict[asyncio.Task[DAGNodeResult], str] = {}
        results: dict[str, DAGNodeResult] = {}
        events: list[DAGExecutionEvent] = []
        reuse_cache = load_reuse_cache(context.metadata)
        self._record_event(
            events,
            DAGExecutionEvent(event="dag_started", message=f"Starting DAG plan {plan.plan_id}", metadata={"node_count": len(order)}),
            event_sink,
        )

        try:
            while len(results) < len(order):
                self._skip_blocked_nodes(
                    pending=pending,
                    results=results,
                    node_by_id=node_by_id,
                    context=context,
                    events=events,
                    event_sink=event_sink,
                    node_result_sink=node_result_sink,
                )
                for node_id in list(order):
                    if node_id not in pending:
                        continue
                    node = node_by_id[node_id]
                    if not all(dependency in results for dependency in node.dependencies):
                        continue
                    dependency_results = {dependency: results[dependency] for dependency in node.dependencies if dependency in results}
                    reused = self._try_reuse_node(
                        node=node,
                        context=context,
                        dependency_results=dependency_results,
                        reuse_cache=reuse_cache,
                        events=events,
                        event_sink=event_sink,
                    )
                    if reused is not None:
                        results[node_id] = reused
                        context.metadata.setdefault("node_outputs", {})[node_id] = reused.output
                        context.metadata.setdefault("node_results", {})[node_id] = reused.model_dump(mode="json")
                        if node_result_sink:
                            node_result_sink(reused)
                        pending.remove(node_id)
                        continue
                    task = asyncio.create_task(
                        self._execute_node(node, context, handlers, semaphores, events, event_sink, dependency_results)
                    )
                    running[task] = node_id
                    pending.remove(node_id)

                if not running:
                    break

                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    self._mark_global_timeout(
                        running=running,
                        pending=pending,
                        results=results,
                        node_by_id=node_by_id,
                        context=context,
                        events=events,
                        event_sink=event_sink,
                        node_result_sink=node_result_sink,
                    )
                    return self._build_result(plan, "timed_out", results, events, order, started, event_sink)

                done, _ = await asyncio.wait(running.keys(), timeout=remaining_time, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    self._mark_global_timeout(
                        running=running,
                        pending=pending,
                        results=results,
                        node_by_id=node_by_id,
                        context=context,
                        events=events,
                        event_sink=event_sink,
                        node_result_sink=node_result_sink,
                    )
                    return self._build_result(plan, "timed_out", results, events, order, started, event_sink)

                for task in done:
                    node_id = running.pop(task)
                    result = task.result()
                    results[node_id] = result
                    context.metadata.setdefault("node_outputs", {})[node_id] = result.output
                    context.metadata.setdefault("node_results", {})[node_id] = result.model_dump(mode="json")
                    if node_result_sink:
                        node_result_sink(result)

            status: ExecutionStatus = "completed"
            if any(result.status in {"failed", "timed_out"} and node_by_id[result.node_id].critical for result in results.values()):
                status = "failed"
            return self._build_result(plan, status, results, events, order, started, event_sink)
        except asyncio.CancelledError:
            for task in running:
                task.cancel()
            await asyncio.gather(*running.keys(), return_exceptions=True)
            self._record_event(
                events,
                DAGExecutionEvent(event="dag_cancelled", status="cancelled", message="DAG execution was cancelled"),
                event_sink,
            )
            raise

    async def _execute_node(
        self,
        node: DAGNode,
        context: DAGExecutionContext,
        handlers: Mapping[str, DAGHandler],
        semaphores: dict[ResourceClass, asyncio.Semaphore],
        events: list[DAGExecutionEvent],
        event_sink: DAGEventSink | None = None,
        dependency_results: Mapping[str, DAGNodeResult] | None = None,
    ) -> DAGNodeResult:
        handler = handlers.get(node.node_id) or handlers.get(node.node_type)
        started_at = _now_iso()
        self._record_event(
            events,
            DAGExecutionEvent(event="node_started", node_id=node.node_id, status="running", metadata={"resource_class": node.resource_class}),
            event_sink,
        )
        if handler is None:
            return self._finish_node(
                node=node,
                context=context,
                dependency_results=dependency_results,
                status="failed",
                started_at=started_at,
                error=f"No DAG handler registered for {node.node_id} / {node.node_type}",
                failure_category="missing_handler",
                events=events,
                event_sink=event_sink,
            )

        try:
            async with semaphores[node.resource_class]:
                output = await asyncio.wait_for(self._call_handler(handler, node, context), timeout=node.timeout_seconds)
            return self._finish_node(
                node=node,
                context=context,
                dependency_results=dependency_results,
                status="completed",
                started_at=started_at,
                output=output or {},
                events=events,
                event_sink=event_sink,
            )
        except TimeoutError:
            return self._finish_node(
                node=node,
                context=context,
                dependency_results=dependency_results,
                status="timed_out",
                started_at=started_at,
                error=f"DAG node timed out after {node.timeout_seconds:g} seconds",
                failure_category="node_timeout",
                events=events,
                event_sink=event_sink,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - boundary converts arbitrary handler failures to structured results.
            return self._finish_node(
                node=node,
                context=context,
                dependency_results=dependency_results,
                status="failed",
                started_at=started_at,
                error=str(exc),
                failure_category=exc.__class__.__name__,
                events=events,
                event_sink=event_sink,
            )

    async def _call_handler(self, handler: DAGHandler, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        value = handler(node, context)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, dict):
            return {"value": value}
        return value

    def _finish_node(
        self,
        *,
        node: DAGNode,
        context: DAGExecutionContext,
        dependency_results: Mapping[str, DAGNodeResult] | None = None,
        status: NodeStatus,
        started_at: str,
        output: dict[str, Any] | None = None,
        error: str = "",
        failure_category: str = "",
        events: list[DAGExecutionEvent],
        event_sink: DAGEventSink | None = None,
    ) -> DAGNodeResult:
        result = DAGNodeResult(
            node_id=node.node_id,
            status=status,
            started_at=started_at,
            finished_at=_now_iso(),
            output=output or {},
            error=error,
            failure_category=failure_category,
            metadata=build_result_fingerprint_metadata(
                node=node,
                context=context,
                dependency_results=dependency_results,
                status=status,
                output=output or {},
                error=error,
                failure_category=failure_category,
            ),
        )
        self._record_event(
            events,
            DAGExecutionEvent(
                event=f"node_{status}",
                node_id=node.node_id,
                status=status,
                message=error,
                metadata={"failure_category": failure_category} if failure_category else {},
            ),
            event_sink,
        )
        return result

    def _skip_blocked_nodes(
        self,
        *,
        pending: set[str],
        results: dict[str, DAGNodeResult],
        node_by_id: dict[str, DAGNode],
        context: DAGExecutionContext,
        events: list[DAGExecutionEvent],
        event_sink: DAGEventSink | None = None,
        node_result_sink: DAGNodeResultSink | None = None,
    ) -> None:
        changed = True
        while changed:
            changed = False
            for node_id in list(pending):
                node = node_by_id[node_id]
                dependency_results = [results.get(dependency) for dependency in node.dependencies]
                if not dependency_results or any(result is None for result in dependency_results):
                    continue
                blocking = [result for result in dependency_results if result and node_by_id[result.node_id].critical and result.status != "completed"]
                if not blocking:
                    continue
                first = blocking[0]
                result = self._finish_node(
                    node=node,
                    context=context,
                    dependency_results={dependency: results[dependency] for dependency in node.dependencies if dependency in results},
                    status="skipped",
                    started_at=_now_iso(),
                    error=f"Skipped because critical dependency {first.node_id} ended with {first.status}",
                    failure_category="blocked_dependency",
                    events=events,
                    event_sink=event_sink,
                )
                results[node_id] = result
                if node_result_sink:
                    node_result_sink(result)
                pending.remove(node_id)
                changed = True

    def _mark_global_timeout(
        self,
        *,
        running: dict[asyncio.Task[DAGNodeResult], str],
        pending: set[str],
        results: dict[str, DAGNodeResult],
        node_by_id: dict[str, DAGNode],
        context: DAGExecutionContext,
        events: list[DAGExecutionEvent],
        event_sink: DAGEventSink | None = None,
        node_result_sink: DAGNodeResultSink | None = None,
    ) -> None:
        self._record_event(
            events,
            DAGExecutionEvent(event="dag_timed_out", status="timed_out", message="DAG global timeout reached"),
            event_sink,
        )
        for task, node_id in list(running.items()):
            task.cancel()
            node = node_by_id[node_id]
            result = self._finish_node(
                node=node,
                context=context,
                dependency_results={dependency: results[dependency] for dependency in node.dependencies if dependency in results},
                status="timed_out",
                started_at=_now_iso(),
                error="Global DAG timeout reached",
                failure_category="global_timeout",
                events=events,
                event_sink=event_sink,
            )
            results[node_id] = result
            if node_result_sink:
                node_result_sink(result)
        running.clear()
        for node_id in list(pending):
            node = node_by_id[node_id]
            result = self._finish_node(
                node=node,
                context=context,
                dependency_results={dependency: results[dependency] for dependency in node.dependencies if dependency in results},
                status="skipped",
                started_at=_now_iso(),
                error="Skipped because global DAG timeout was reached",
                failure_category="global_timeout",
                events=events,
                event_sink=event_sink,
            )
            results[node_id] = result
            if node_result_sink:
                node_result_sink(result)
            pending.remove(node_id)

    def _build_result(
        self,
        plan: DAGPlan,
        status: ExecutionStatus,
        results: dict[str, DAGNodeResult],
        events: list[DAGExecutionEvent],
        order: list[str],
        started: float,
        event_sink: DAGEventSink | None = None,
    ) -> DAGExecutionResult:
        self._record_event(
            events,
            DAGExecutionEvent(event="dag_finished", status=status, metadata={"completed_nodes": len(results)}),
            event_sink,
        )
        return DAGExecutionResult(
            plan_id=plan.plan_id,
            status=status,
            results={node_id: results[node_id] for node_id in order if node_id in results},
            events=events,
            topological_order=order,
            duration_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _record_event(
        events: list[DAGExecutionEvent],
        event: DAGExecutionEvent,
        event_sink: DAGEventSink | None = None,
    ) -> None:
        events.append(event)
        if event_sink:
            event_sink(event)

    def _try_reuse_node(
        self,
        *,
        node: DAGNode,
        context: DAGExecutionContext,
        dependency_results: Mapping[str, DAGNodeResult],
        reuse_cache: Mapping[str, DAGNodeResult],
        events: list[DAGExecutionEvent],
        event_sink: DAGEventSink | None = None,
    ) -> DAGNodeResult | None:
        cached = reuse_cache.get(node.node_id)
        if cached is None:
            return None
        reusable, reason = can_reuse_cached_result(
            node=node,
            context=context,
            dependency_results=dependency_results,
            cached_result=cached,
        )
        if not reusable:
            self._record_event(
                events,
                DAGExecutionEvent(
                    event="node_reuse_rejected",
                    node_id=node.node_id,
                    status="running",
                    message=reason,
                    metadata={"reuse_reason": reason},
                ),
                event_sink,
            )
            return None
        metadata = {
            **cached.metadata,
            "reused_from_checkpoint": True,
            "reuse_reason": reason,
            "reuse_validated_at": _now_iso(),
        }
        result = cached.model_copy(update={"attempt": cached.attempt + 1, "metadata": metadata})
        self._record_event(
            events,
            DAGExecutionEvent(
                event="node_reused",
                node_id=node.node_id,
                status=result.status,
                message="Reused checkpointed DAG node result after hash validation.",
                metadata={
                    "result_hash": metadata.get("result_hash", ""),
                    "node_fingerprint": metadata.get("node_fingerprint", ""),
                },
            ),
            event_sink,
        )
        return result
