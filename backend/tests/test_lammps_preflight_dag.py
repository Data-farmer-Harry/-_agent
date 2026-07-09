from __future__ import annotations

import unittest

from app.lammps.preflight import LAMMPS_PREFLIGHT_NODE_IDS, build_lammps_preflight_plan


class LammpsPreflightDagTests(unittest.TestCase):
    def test_preflight_plan_has_expected_topology(self) -> None:
        plan = build_lammps_preflight_plan()

        self.assertEqual(plan.topological_order(), LAMMPS_PREFLIGHT_NODE_IDS)

        nodes = plan.node_map()
        self.assertEqual(nodes["preflight_merge"].dependencies, LAMMPS_PREFLIGHT_NODE_IDS[:5])
        self.assertEqual(nodes["red_pre_execution_review"].dependencies, ["preflight_merge"])
        self.assertEqual(nodes["materials_rag_search"].resource_class, "network")
        self.assertEqual(nodes["runtime_diagnostics"].resource_class, "cpu")
        self.assertFalse(nodes["materials_rag_search"].critical)
        self.assertTrue(nodes["registry_lookup"].critical)

    def test_preflight_plan_is_stable_for_identical_inputs(self) -> None:
        first = build_lammps_preflight_plan().model_dump(mode="json")
        second = build_lammps_preflight_plan().model_dump(mode="json")

        self.assertEqual(first, second)

    def test_attachment_node_becomes_critical_when_required(self) -> None:
        optional_plan = build_lammps_preflight_plan(requires_attachment=False)
        required_plan = build_lammps_preflight_plan(requires_attachment=True)

        self.assertFalse(optional_plan.node_map()["attachment_inspection"].critical)
        self.assertTrue(required_plan.node_map()["attachment_inspection"].critical)

    def test_timeout_overrides_are_applied_without_changing_topology(self) -> None:
        plan = build_lammps_preflight_plan(timeout_overrides={"materials_rag_search": 3.5})

        self.assertEqual(plan.topological_order(), LAMMPS_PREFLIGHT_NODE_IDS)
        self.assertEqual(plan.node_map()["materials_rag_search"].timeout_seconds, 3.5)


if __name__ == "__main__":
    unittest.main()
