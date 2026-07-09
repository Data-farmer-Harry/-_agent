from __future__ import annotations

import asyncio
import unittest

from app.orchestration import DAGExecutionContext, DAGExecutor, DAGNode, DAGPlan, DAGResourceLimits


class DagExecutorTests(unittest.TestCase):
    def test_executor_respects_resource_semaphore_and_preserves_results(self) -> None:
        active_network = 0
        max_active_network = 0

        async def network_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            nonlocal active_network, max_active_network
            active_network += 1
            max_active_network = max(max_active_network, active_network)
            await asyncio.sleep(0.02)
            active_network -= 1
            return {"node_id": node.node_id, "run_id": context.run_id}

        def cpu_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            return {"merged": True, "run_id": context.run_id}

        plan = DAGPlan(
            plan_id="unit-executor",
            nodes=[
                DAGNode(node_id="rag_a", node_type="network", resource_class="network"),
                DAGNode(node_id="rag_b", node_type="network", resource_class="network"),
                DAGNode(node_id="merge", node_type="merge", dependencies=["rag_a", "rag_b"], resource_class="cpu"),
            ],
        )
        result = asyncio.run(
            DAGExecutor(DAGResourceLimits(network=1, cpu=2, simulation=1)).run(
                plan,
                DAGExecutionContext(run_id="run-semaphore"),
                handlers={"network": network_handler, "merge": cpu_handler},
            )
        )

        self.assertEqual(result.status, "completed")
        self.assertLessEqual(max_active_network, 1)
        self.assertEqual(result.successful_node_ids(), ["rag_a", "rag_b", "merge"])
        self.assertEqual(result.results["merge"].output["run_id"], "run-semaphore")

    def test_executor_converts_handler_exception_and_keeps_independent_branch(self) -> None:
        async def failing_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            raise RuntimeError("boom")

        async def ok_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            await asyncio.sleep(0)
            return {"ok": True}

        plan = DAGPlan(
            plan_id="unit-exception",
            nodes=[
                DAGNode(node_id="critical_fail", node_type="fail", critical=True),
                DAGNode(node_id="independent", node_type="ok"),
                DAGNode(node_id="blocked", node_type="ok", dependencies=["critical_fail"]),
            ],
        )
        result = asyncio.run(DAGExecutor().run(plan, handlers={"fail": failing_handler, "ok": ok_handler}))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.results["critical_fail"].status, "failed")
        self.assertEqual(result.results["critical_fail"].failure_category, "RuntimeError")
        self.assertEqual(result.results["independent"].status, "completed")
        self.assertEqual(result.results["blocked"].status, "skipped")
        self.assertEqual(result.results["blocked"].failure_category, "blocked_dependency")

    def test_noncritical_failed_dependency_allows_downstream_fallback(self) -> None:
        async def failing_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            raise ValueError("optional branch failed")

        async def ok_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            return {"ok": True}

        plan = DAGPlan(
            plan_id="unit-noncritical",
            nodes=[
                DAGNode(node_id="optional_rag", node_type="fail", critical=False),
                DAGNode(node_id="registry", node_type="ok", critical=True),
                DAGNode(node_id="merge", node_type="ok", dependencies=["optional_rag", "registry"], critical=True),
            ],
        )
        result = asyncio.run(DAGExecutor().run(plan, handlers={"fail": failing_handler, "ok": ok_handler}))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.results["optional_rag"].status, "failed")
        self.assertEqual(result.results["merge"].status, "completed")

    def test_node_timeout_skips_critical_dependents(self) -> None:
        async def slow_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            await asyncio.sleep(0.05)
            return {"late": True}

        async def ok_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            return {"ok": True}

        plan = DAGPlan(
            plan_id="unit-timeout",
            nodes=[
                DAGNode(node_id="slow", node_type="slow", timeout_seconds=0.01, critical=True),
                DAGNode(node_id="after_slow", node_type="ok", dependencies=["slow"]),
                DAGNode(node_id="independent", node_type="ok"),
            ],
        )
        result = asyncio.run(DAGExecutor().run(plan, handlers={"slow": slow_handler, "ok": ok_handler}))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.results["slow"].status, "timed_out")
        self.assertEqual(result.results["after_slow"].status, "skipped")
        self.assertEqual(result.results["independent"].status, "completed")

    def test_global_timeout_returns_partial_report(self) -> None:
        async def slow_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            await asyncio.sleep(0.05)
            return {"late": True}

        plan = DAGPlan(
            plan_id="unit-global-timeout",
            global_timeout_seconds=0.01,
            nodes=[DAGNode(node_id="slow", node_type="slow", timeout_seconds=1.0)],
        )
        result = asyncio.run(DAGExecutor().run(plan, handlers={"slow": slow_handler}))

        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.results["slow"].status, "timed_out")
        self.assertEqual(result.results["slow"].failure_category, "global_timeout")

    def test_event_and_node_result_callbacks_are_invoked(self) -> None:
        def handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            return {"node": node.node_id, "run_id": context.run_id}

        plan = DAGPlan(
            plan_id="unit-callbacks",
            nodes=[
                DAGNode(node_id="a", node_type="ok"),
                DAGNode(node_id="b", node_type="ok", dependencies=["a"]),
            ],
        )
        event_names: list[str] = []
        node_results: list[str] = []
        result = asyncio.run(
            DAGExecutor().run(
                plan,
                DAGExecutionContext(run_id="run-callbacks"),
                handlers={"ok": handler},
                event_sink=lambda event: event_names.append(event.event),
                node_result_sink=lambda node_result: node_results.append(node_result.node_id),
            )
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(node_results, ["a", "b"])
        self.assertIn("dag_started", event_names)
        self.assertIn("node_started", event_names)
        self.assertIn("node_completed", event_names)
        self.assertEqual(event_names[-1], "dag_finished")

    def test_executor_records_hashes_and_reuses_checkpointed_safe_node(self) -> None:
        calls = 0

        def handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"node": node.node_id, "material": context.input_payload["material"]}

        plan = DAGPlan(
            plan_id="unit-reuse-v1",
            nodes=[DAGNode(node_id="constraint_extract", node_type="extract", input_keys=["material"])],
        )
        first_context = DAGExecutionContext(
            run_id="run-reuse-1",
            input_payload={"material": "Al", "ignored": "does-not-affect-input-hash"},
            config_signature="dev-config",
        )
        first = asyncio.run(DAGExecutor().run(plan, first_context, handlers={"extract": handler}))
        self.assertEqual(first.status, "completed")
        self.assertEqual(calls, 1)
        first_result = first.results["constraint_extract"]
        self.assertTrue(first_result.metadata["input_hash"])
        self.assertTrue(first_result.metadata["result_hash"])
        self.assertTrue(first_result.metadata["node_fingerprint"])

        replan = DAGPlan(
            plan_id="unit-reuse-v2",
            plan_version=2,
            nodes=[
                DAGNode(
                    node_id="constraint_extract",
                    node_type="extract",
                    input_keys=["material"],
                    metadata={"replan_status": "reuse_checkpoint"},
                )
            ],
        )
        second_context = DAGExecutionContext(
            run_id="run-reuse-2",
            input_payload={"material": "Al", "ignored": "changed-but-not-selected"},
            config_signature="dev-config",
            metadata={"reuse_node_results": {"constraint_extract": first_result.model_dump(mode="json")}},
        )
        second = asyncio.run(DAGExecutor().run(replan, second_context, handlers={"extract": handler}))

        self.assertEqual(second.status, "completed")
        self.assertEqual(calls, 1)
        reused = second.results["constraint_extract"]
        self.assertEqual(reused.output, first_result.output)
        self.assertTrue(reused.metadata["reused_from_checkpoint"])
        self.assertIn("node_reused", [event.event for event in second.events])

    def test_cancellation_propagates_to_running_handlers(self) -> None:
        async def scenario() -> bool:
            started = asyncio.Event()
            cancelled = False

            async def slow_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
                nonlocal cancelled
                started.set()
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                return {"late": True}

            plan = DAGPlan(plan_id="unit-cancel", nodes=[DAGNode(node_id="slow", node_type="slow")])
            task = asyncio.create_task(DAGExecutor().run(plan, handlers={"slow": slow_handler}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return cancelled

        self.assertTrue(asyncio.run(scenario()))


if __name__ == "__main__":
    unittest.main()
