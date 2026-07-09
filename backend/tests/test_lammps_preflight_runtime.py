from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.artifacts import ArtifactService
from app.lammps.config import LammpsConfig
from app.lammps.preflight import LAMMPS_PREFLIGHT_NODE_IDS
from app.orchestration.lifecycle import load_lifecycle_state
from app.runtimes.lammps import LammpsRuntime
from tests.support import ScriptedLLMClient, build_request


class LammpsPreflightRuntimeTests(unittest.TestCase):
    def _run_runtime(
        self,
        *,
        preflight_enabled: bool,
        fail_materials_rag: bool = False,
        fail_red_review_once: bool = False,
        max_retries: int = 0,
    ):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runtime = LammpsRuntime(
            artifact_service=ArtifactService(root_dir=Path(tmp.name)),
            llm_client=ScriptedLLMClient(),
            config_loader=lambda: LammpsConfig(
                allow_mock_fallback=True,
                force_mock=True,
                lammps_command="",
                potentials_dir="",
                max_retries=max_retries,
                lammps_preflight_dag_enabled=preflight_enabled,
            ),
        )
        if fail_materials_rag:
            def _raise_materials_rag(_request):
                raise RuntimeError("materials rag offline")

            runtime._planning_materials_rag = _raise_materials_rag  # type: ignore[method-assign]
        if fail_red_review_once:
            original_red_review = runtime._preflight_red_review_handler
            calls = {"count": 0}

            def _fail_red_review_once(node, context):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise RuntimeError("transient red review failure")
                return original_red_review(node, context)

            runtime._preflight_red_review_handler = _fail_red_review_once  # type: ignore[method-assign]
        return runtime.run(
            run_id=f"lammps-preflight-{preflight_enabled}",
            request=build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。"),
        )

    def test_preflight_dag_feature_flag_adds_dag_trace_without_breaking_runtime(self) -> None:
        result = self._run_runtime(preflight_enabled=True)

        tool_names = [step.tool_name for step in result.plan_steps]

        self.assertTrue(result.success)
        self.assertIn("lammps_preflight_dag", tool_names)
        self.assertIn("materials_rag_search", tool_names)
        self.assertEqual(result.metadata["preflight_dag"]["status"], "completed")
        self.assertEqual(set(result.metadata["preflight_dag"]["results"]), set(LAMMPS_PREFLIGHT_NODE_IDS))
        self.assertEqual(result.metadata["materials_rag"]["planning"]["source"], "preflight_dag")
        self.assertEqual(result.summary["request"]["material"], "Cu")
        self.assertEqual(result.summary["request"]["task_type"], "heating")
        self.assertEqual(result.summary["request"]["temperature"], 800)
        self.assertIn("preflight_dag", result.summary)
        self.assertEqual(result.metadata["lifecycle"]["current_state"], "completed")
        checkpoint_stages = {item["stage"] for item in result.metadata["lifecycle"]["checkpoints"]}
        for node_id in LAMMPS_PREFLIGHT_NODE_IDS:
            self.assertIn(f"after_node_{node_id}", checkpoint_stages)
        lifecycle_artifact = next(item for item in result.artifacts if item.name == "lifecycle.json")
        assert lifecycle_artifact.path is not None
        restored = load_lifecycle_state(Path(lifecycle_artifact.path).parent)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.current_state, "completed")
        self.assertEqual(restored.last_checkpoint_id, result.metadata["lifecycle"]["last_checkpoint_id"])

    def test_preflight_dag_preserves_legacy_normalized_request(self) -> None:
        legacy = self._run_runtime(preflight_enabled=False)
        dag = self._run_runtime(preflight_enabled=True)

        self.assertTrue(legacy.success)
        self.assertTrue(dag.success)
        self.assertEqual(legacy.summary["request"], dag.summary["request"])
        self.assertEqual(legacy.metadata["materials_rag"]["planning"]["material"], dag.metadata["materials_rag"]["planning"]["material"])
        self.assertEqual(legacy.metadata["materials_rag"]["planning"]["hits"], dag.metadata["materials_rag"]["planning"]["hits"])

    def test_preflight_dag_uses_level_1_fallback_for_optional_rag_failure(self) -> None:
        result = self._run_runtime(preflight_enabled=True, fail_materials_rag=True)

        preflight = result.metadata["preflight_dag"]
        degradation = preflight["metadata"]["degradation"]

        self.assertTrue(result.success)
        self.assertEqual(preflight["status"], "completed")
        self.assertEqual(degradation["degradation_level"], "level_1_fallback")
        self.assertEqual(degradation["fallback_nodes"], ["materials_rag_search"])
        self.assertEqual(preflight["results"]["materials_rag_search"]["status"], "completed_with_fallback")
        self.assertEqual(result.metadata["materials_rag"]["planning"]["source"], "preflight_dag")
        self.assertEqual(result.metadata["materials_rag"]["planning"]["hits"], [])

    def test_preflight_dag_executes_level_2_replan_with_checkpoint_reuse(self) -> None:
        result = self._run_runtime(preflight_enabled=True, fail_red_review_once=True, max_retries=1)

        preflight = result.metadata["preflight_dag"]
        degradation = preflight["metadata"]["degradation"]
        lifecycle_events = result.metadata["lifecycle"]["events"]

        self.assertTrue(result.success)
        self.assertEqual(preflight["status"], "completed")
        self.assertEqual(degradation["degradation_level"], "none")
        self.assertTrue(degradation["replan_executed"])
        self.assertEqual(degradation["final_plan_version"], 2)
        self.assertEqual(degradation["replan_history"][0]["degradation_level"], "level_2_replan")
        self.assertEqual(degradation["replan_history"][0]["invalidated_nodes"], ["red_pre_execution_review"])
        self.assertIn("constraint_extract", degradation["replan_history"][0]["reused_nodes"])
        self.assertTrue(preflight["results"]["constraint_extract"]["metadata"]["reused_from_checkpoint"])
        self.assertTrue(preflight["results"]["preflight_merge"]["metadata"]["reused_from_checkpoint"])
        self.assertFalse(preflight["results"]["red_pre_execution_review"]["metadata"].get("reused_from_checkpoint", False))
        self.assertIn("node_reused", [event["event"] for event in preflight["events"]])
        transition_targets = [
            event.get("to_state")
            for event in lifecycle_events
            if event.get("event_type") == "lifecycle.transition"
        ]
        self.assertIn("repairing", transition_targets)
        plan_events = [
            event
            for event in lifecycle_events
            if event.get("event_type") == "plan.created"
            and event.get("metadata", {}).get("created_from") == "runtime_replan"
        ]
        self.assertEqual(len(plan_events), 1)


if __name__ == "__main__":
    unittest.main()
