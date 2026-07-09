from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.orchestration import DAGNode, DAGNodeResult, DAGPlan
from app.orchestration.lifecycle import LifecycleTransitionError, TaskLifecycleController, load_lifecycle_state


class LifecycleStateMachineTests(unittest.TestCase):
    def test_lifecycle_rejects_invalid_transition_and_requires_termination_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = TaskLifecycleController(run_id="life-invalid", run_dir=Path(tmp))

            with self.assertRaises(LifecycleTransitionError):
                controller.transition(to_state="running", reason="skip_required_states")

            controller.transition(to_state="planning", reason="start")
            with self.assertRaises(LifecycleTransitionError):
                controller.transition(to_state="terminated", reason="missing_reason")

            controller.transition(to_state="terminated", reason="failed", termination_reason="preflight_failed")

            with self.assertRaises(LifecycleTransitionError):
                controller.transition(to_state="planning", reason="resume_same_attempt")

    def test_checkpoint_is_restart_readable_and_tracks_node_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            controller = TaskLifecycleController(run_id="life-checkpoint", run_dir=run_dir)
            plan = DAGPlan(
                plan_id="unit-plan",
                nodes=[
                    DAGNode(node_id="a", node_type="unit.a"),
                    DAGNode(node_id="b", node_type="unit.b", dependencies=["a"]),
                ],
            )
            controller.transition(to_state="planning", reason="start")
            controller.record_plan_created(plan)
            after_plan = controller.save_checkpoint(stage="after_plan", plan=plan)
            controller.transition(to_state="preflight", reason="run_preflight")
            node_result = DAGNodeResult(node_id="a", status="completed", output={"ok": True})
            after_node = controller.save_checkpoint(stage="after_node_a", plan=plan, results={"a": node_result}, node_id="a")

            restored = load_lifecycle_state(run_dir)
            checkpoint_exists = Path(restored.checkpoints[-1].path).exists() if restored is not None else False

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.current_state, "preflight")
        self.assertEqual(restored.last_checkpoint_id, after_node.checkpoint_id)
        self.assertEqual(restored.checkpoints[0].checkpoint_id, after_plan.checkpoint_id)
        self.assertEqual(restored.checkpoints[-1].completed_nodes, ["a"])
        self.assertEqual(restored.checkpoints[-1].pending_nodes, ["b"])
        self.assertTrue(checkpoint_exists)


if __name__ == "__main__":
    unittest.main()
