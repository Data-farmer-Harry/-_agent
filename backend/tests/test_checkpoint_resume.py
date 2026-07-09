from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest

from app.jobs import AgentJobStore, AgentJobWorker
from app.orchestration import (
    DAGExecutionContext,
    DAGExecutor,
    DAGNode,
    DAGPlan,
    ReplanBudgetState,
    TaskLifecycleController,
    decide_degradation,
)
from app.state import AgentChatRequest, AgentRunResponse, AgentStreamEvent, TaskRoute


def _response(run_id: str, conversation_id: str = "conv-recovery") -> AgentRunResponse:
    return AgentRunResponse(
        success=True,
        run_id=run_id,
        conversation_id=conversation_id,
        route=TaskRoute(name="lammps.generate", compute_domain="lammps", selected_tool="lammps"),
        final_message="ok",
        metadata={"unit_test": True},
    )


class CheckpointResumeTests(unittest.TestCase):
    def test_global_timeout_keeps_checkpoint_and_resumeable_partial_report(self) -> None:
        async def slow_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            await asyncio.sleep(0.05)
            return {"late": True, "node": node.node_id}

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            lifecycle = TaskLifecycleController(run_id="run-global-timeout", run_dir=run_dir)
            lifecycle.transition(to_state="planning", reason="unit_test")
            plan = DAGPlan(
                plan_id="timeout-plan/v1",
                global_timeout_seconds=0.01,
                nodes=[
                    DAGNode(node_id="extract", node_type="slow", timeout_seconds=1.0),
                    DAGNode(node_id="review", node_type="slow", dependencies=["extract"], timeout_seconds=1.0),
                ],
            )
            lifecycle.record_plan_created(plan, metadata={"created_from": "unit_test"})
            lifecycle.save_checkpoint(stage="after_plan", plan=plan)
            lifecycle.transition(to_state="preflight", reason="unit_test", plan_version=plan.plan_version)
            partial_results = {}

            def save_checkpoint(node_result) -> None:
                partial_results[node_result.node_id] = node_result
                lifecycle.save_checkpoint(
                    stage=f"after_node_{node_result.node_id}",
                    plan=plan,
                    results=partial_results,
                    node_id=node_result.node_id,
                )

            result = asyncio.run(
                DAGExecutor().run(
                    plan,
                    DAGExecutionContext(run_id="run-global-timeout"),
                    handlers={"slow": slow_handler},
                    node_result_sink=save_checkpoint,
                )
            )
            decision = decide_degradation(
                plan,
                result,
                budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1),
                last_checkpoint_id=lifecycle.state.last_checkpoint_id,
            )

            self.assertEqual(result.status, "timed_out")
            self.assertIsNotNone(decision.partial_report)
            assert decision.partial_report is not None
            self.assertEqual(decision.termination_reason, "global_timeout")
            self.assertFalse(decision.partial_report.success)
            self.assertFalse(decision.partial_report.scientific_result_available)
            self.assertTrue(decision.partial_report.resume_supported)
            self.assertEqual(decision.partial_report.last_checkpoint_id, lifecycle.state.last_checkpoint_id)
            self.assertGreaterEqual(len(lifecycle.state.checkpoints), 2)

    def test_worker_crash_records_run_error_and_failed_status(self) -> None:
        def crashing_runner(request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
            if event_sink:
                event_sink(AgentStreamEvent(type="run_started", run_id="run-crash", payload={"message": request.message}))
            raise RuntimeError("simulated worker crash")

        with tempfile.TemporaryDirectory() as tmp:
            store = AgentJobStore(root_dir=Path(tmp))
            worker = AgentJobWorker(store=store, runner=crashing_runner, poll_interval_seconds=0.01)
            record = worker.submit_agent_chat(AgentChatRequest(conversation_id="conv-crash", message="crash please"))
            claimed = store.claim_next()
            self.assertIsNotNone(claimed)
            assert claimed is not None

            worker._run_record(claimed)

            latest = store.get(record.job_id)
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.status, "failed")
            self.assertEqual(latest.run_id, "run-crash")
            self.assertIn("simulated worker crash", latest.error)
            events = store.events_after(record.job_id)
            self.assertEqual([item.event.type for item in events], ["run_started", "run_error"])
            self.assertEqual(events[-1].event.run_id, "run-crash")

    def test_running_cancel_is_not_overwritten_by_late_success(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking_runner(request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
            if event_sink:
                event_sink(AgentStreamEvent(type="run_started", run_id="run-cancel-running", payload={"message": request.message}))
            started.set()
            release.wait(timeout=2.0)
            return _response("run-cancel-running", conversation_id=request.conversation_id)

        with tempfile.TemporaryDirectory() as tmp:
            store = AgentJobStore(root_dir=Path(tmp))
            worker = AgentJobWorker(store=store, runner=blocking_runner, poll_interval_seconds=0.01)
            worker.start()
            try:
                record = worker.submit_agent_chat(AgentChatRequest(conversation_id="conv-running-cancel", message="cancel while running"))
                self.assertTrue(started.wait(timeout=2.0))

                cancelled = worker.cancel(record.job_id)
                self.assertEqual(cancelled.status, "cancelled")
                release.set()

                deadline = time.time() + 3
                latest = store.get(record.job_id)
                while latest and latest.status not in {"completed", "failed", "cancelled"} and time.time() < deadline:
                    time.sleep(0.02)
                    latest = store.get(record.job_id)
            finally:
                release.set()
                worker.stop()

            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.status, "cancelled")
            self.assertEqual(latest.run_id, "run-cancel-running")
            self.assertEqual(latest.result_run_id, "")
            self.assertEqual([item.event.type for item in store.events_after(record.job_id)], ["run_started"])


if __name__ == "__main__":
    unittest.main()
