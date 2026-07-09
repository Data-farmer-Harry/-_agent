from __future__ import annotations

import unittest

from app.orchestration import (
    DAGExecutionResult,
    DAGNode,
    DAGNodeResult,
    DAGPlan,
    ReplanBudgetState,
    apply_level_1_fallbacks,
    decide_degradation,
    failure_signature,
    merge_failure_batch,
)


def _reuse_metadata(node_id: str, dependencies: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "input_hash": f"input-{node_id}",
        "config_signature_hash": "config-dev",
        "dependency_result_hashes": dependencies or {},
        "node_fingerprint": f"fingerprint-{node_id}",
        "result_hash": f"result-{node_id}",
        "content_hash": f"result-{node_id}",
        "reuse_safe": True,
    }


def _plan() -> DAGPlan:
    return DAGPlan(
        plan_id="test-plan/v1",
        global_timeout_seconds=5,
        nodes=[
            DAGNode(node_id="extract", node_type="extract", critical=True),
            DAGNode(
                node_id="optional_rag",
                node_type="rag",
                dependencies=["extract"],
                critical=False,
                retryable=True,
                metadata={"fallback": "continue_without_rag"},
            ),
            DAGNode(node_id="critical_review", node_type="review", dependencies=["extract"], critical=True, retryable=True),
            DAGNode(node_id="merge", node_type="merge", dependencies=["optional_rag", "critical_review"], critical=True),
        ],
    )


class ReplanPolicyTests(unittest.TestCase):
    def test_level_1_fallback_marks_optional_failure_as_completed_with_fallback(self) -> None:
        plan = _plan()
        result = DAGExecutionResult(
            plan_id=plan.plan_id,
            status="completed",
            topological_order=plan.topological_order(),
            results={
                "extract": DAGNodeResult(node_id="extract", status="completed"),
                "optional_rag": DAGNodeResult(
                    node_id="optional_rag",
                    status="timed_out",
                    error="DAG node timed out after 1 seconds",
                    failure_category="node_timeout",
                ),
                "critical_review": DAGNodeResult(node_id="critical_review", status="completed"),
                "merge": DAGNodeResult(node_id="merge", status="completed"),
            },
        )

        decision = decide_degradation(plan, result, budget_state=ReplanBudgetState(repair_budget=0, replan_budget=1))
        degraded = apply_level_1_fallbacks(result, decision)

        self.assertEqual(decision.degradation_level, "level_1_fallback")
        self.assertEqual(decision.fallback_nodes, ["optional_rag"])
        self.assertEqual(degraded.status, "completed")
        self.assertEqual(degraded.results["optional_rag"].status, "completed_with_fallback")
        self.assertTrue(degraded.results["optional_rag"].metadata["fallback_applied"])

    def test_level_2_replan_invalidates_failed_node_and_downstream_only(self) -> None:
        plan = _plan()
        result = DAGExecutionResult(
            plan_id=plan.plan_id,
            status="failed",
            topological_order=plan.topological_order(),
            results={
                "extract": DAGNodeResult(node_id="extract", status="completed", metadata=_reuse_metadata("extract")),
                "optional_rag": DAGNodeResult(
                    node_id="optional_rag",
                    status="completed",
                    metadata=_reuse_metadata("optional_rag", {"extract": "result-extract"}),
                ),
                "critical_review": DAGNodeResult(
                    node_id="critical_review",
                    status="failed",
                    error="red review consistency failure",
                    failure_category="RuntimeError",
                ),
                "merge": DAGNodeResult(
                    node_id="merge",
                    status="skipped",
                    error="Skipped because critical dependency critical_review ended with failed",
                    failure_category="blocked_dependency",
                ),
            },
        )

        decision = decide_degradation(plan, result, budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1))

        self.assertEqual(decision.degradation_level, "level_2_replan")
        self.assertTrue(decision.can_continue)
        self.assertEqual(decision.lifecycle_target, "repairing")
        self.assertEqual(decision.invalidated_nodes, ["critical_review", "merge"])
        self.assertEqual(decision.reused_nodes, ["extract", "optional_rag"])
        self.assertIsNotNone(decision.new_plan)
        assert decision.new_plan is not None
        self.assertEqual(decision.new_plan.plan_version, 2)
        self.assertEqual(decision.new_plan.node_map()["optional_rag"].metadata["replan_status"], "reuse_checkpoint")
        self.assertEqual(decision.new_plan.node_map()["critical_review"].metadata["replan_status"], "rerun")
        self.assertEqual(decision.metadata["non_reusable_nodes"], [])

    def test_replan_reruns_non_invalidated_node_without_reuse_hashes(self) -> None:
        plan = _plan()
        result = DAGExecutionResult(
            plan_id=plan.plan_id,
            status="failed",
            topological_order=plan.topological_order(),
            results={
                "extract": DAGNodeResult(node_id="extract", status="completed"),
                "optional_rag": DAGNodeResult(
                    node_id="optional_rag",
                    status="completed",
                    metadata=_reuse_metadata("optional_rag", {"extract": "result-extract"}),
                ),
                "critical_review": DAGNodeResult(
                    node_id="critical_review",
                    status="failed",
                    error="red review consistency failure",
                    failure_category="RuntimeError",
                ),
                "merge": DAGNodeResult(node_id="merge", status="skipped", failure_category="blocked_dependency"),
            },
        )

        decision = decide_degradation(plan, result, budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1))

        self.assertEqual(decision.reused_nodes, ["optional_rag"])
        self.assertEqual(decision.metadata["non_reusable_nodes"], ["extract"])
        assert decision.new_plan is not None
        self.assertEqual(decision.new_plan.node_map()["extract"].metadata["replan_status"], "rerun")
        self.assertEqual(decision.new_plan.node_map()["extract"].metadata["replan_reason"], "missing_or_unsafe_reuse_fingerprint")

    def test_level_3_global_timeout_builds_honest_partial_report(self) -> None:
        plan = _plan()
        result = DAGExecutionResult(
            plan_id=plan.plan_id,
            status="timed_out",
            topological_order=plan.topological_order(),
            results={
                "extract": DAGNodeResult(node_id="extract", status="completed", evidence_refs=["extract.json"]),
                "optional_rag": DAGNodeResult(
                    node_id="optional_rag",
                    status="timed_out",
                    error="Global DAG timeout reached",
                    failure_category="global_timeout",
                ),
                "critical_review": DAGNodeResult(
                    node_id="critical_review",
                    status="skipped",
                    error="Skipped because global DAG timeout was reached",
                    failure_category="global_timeout",
                ),
            },
        )

        decision = decide_degradation(
            plan,
            result,
            available_artifacts=[{"name": "extract.json"}],
            last_checkpoint_id="after_node_extract-1234",
        )

        self.assertEqual(decision.degradation_level, "level_3_partial_report")
        self.assertFalse(decision.can_continue)
        self.assertEqual(decision.termination_reason, "global_timeout")
        self.assertIsNotNone(decision.partial_report)
        assert decision.partial_report is not None
        self.assertFalse(decision.partial_report.success)
        self.assertFalse(decision.partial_report.scientific_result_available)
        self.assertEqual(decision.partial_report.completed_nodes, ["extract"])
        self.assertEqual(decision.partial_report.failed_nodes, ["optional_rag"])
        self.assertEqual(decision.partial_report.unfinished_nodes, ["critical_review", "merge"])
        self.assertEqual(decision.partial_report.last_checkpoint_id, "after_node_extract-1234")
        self.assertTrue(decision.partial_report.resume_supported)

    def test_repeated_failure_signature_stops_oscillation(self) -> None:
        plan = _plan()
        result = DAGExecutionResult(
            plan_id=plan.plan_id,
            status="failed",
            topological_order=plan.topological_order(),
            results={
                "extract": DAGNodeResult(node_id="extract", status="completed"),
                "critical_review": DAGNodeResult(
                    node_id="critical_review",
                    status="failed",
                    error="same failure",
                    failure_category="RuntimeError",
                ),
                "merge": DAGNodeResult(node_id="merge", status="skipped", failure_category="blocked_dependency"),
            },
        )
        batch = merge_failure_batch(plan, result)
        assert batch is not None
        signature = failure_signature(batch)

        decision = decide_degradation(
            plan,
            result,
            budget_state=ReplanBudgetState(
                repair_budget=2,
                replan_budget=2,
                previous_failure_signatures=[signature, signature],
                oscillation_threshold=2,
            ),
        )

        self.assertEqual(decision.degradation_level, "level_2_replan")
        self.assertFalse(decision.can_continue)
        self.assertTrue(decision.oscillation_detected)
        self.assertEqual(decision.termination_reason, "repair_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
