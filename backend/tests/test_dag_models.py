from __future__ import annotations

import unittest

from app.orchestration import DAGNode, DAGPlan, DAGValidationError, get_downstream_nodes, topological_sort, validate_dag_plan


class DagModelTests(unittest.TestCase):
    def test_topological_sort_is_stable_for_diamond_graph(self) -> None:
        plan = DAGPlan(
            plan_id="unit-diamond",
            nodes=[
                DAGNode(node_id="root", node_type="unit"),
                DAGNode(node_id="left", node_type="unit", dependencies=["root"]),
                DAGNode(node_id="right", node_type="unit", dependencies=["root"]),
                DAGNode(node_id="merge", node_type="unit", dependencies=["left", "right"]),
            ],
        )

        validate_dag_plan(plan)

        self.assertEqual(topological_sort(plan), ["root", "left", "right", "merge"])

    def test_duplicate_node_ids_are_rejected(self) -> None:
        plan = DAGPlan(
            plan_id="unit-duplicate",
            nodes=[DAGNode(node_id="same", node_type="unit"), DAGNode(node_id="same", node_type="unit")],
        )

        with self.assertRaisesRegex(DAGValidationError, "Duplicate"):
            validate_dag_plan(plan)

    def test_missing_dependency_is_rejected(self) -> None:
        plan = DAGPlan(
            plan_id="unit-missing",
            nodes=[DAGNode(node_id="child", node_type="unit", dependencies=["missing-parent"])],
        )

        with self.assertRaisesRegex(DAGValidationError, "missing-parent"):
            validate_dag_plan(plan)

    def test_cycle_is_rejected(self) -> None:
        plan = DAGPlan(
            plan_id="unit-cycle",
            nodes=[
                DAGNode(node_id="a", node_type="unit", dependencies=["c"]),
                DAGNode(node_id="b", node_type="unit", dependencies=["a"]),
                DAGNode(node_id="c", node_type="unit", dependencies=["b"]),
            ],
        )

        with self.assertRaisesRegex(DAGValidationError, "cycle"):
            validate_dag_plan(plan)

    def test_downstream_invalidations_follow_topology_order(self) -> None:
        plan = DAGPlan(
            plan_id="unit-downstream",
            nodes=[
                DAGNode(node_id="constraint_extract", node_type="unit"),
                DAGNode(node_id="materials_rag_search", node_type="unit"),
                DAGNode(node_id="preflight_merge", node_type="unit", dependencies=["constraint_extract", "materials_rag_search"]),
                DAGNode(node_id="red_pre_execution_review", node_type="unit", dependencies=["preflight_merge"]),
            ],
        )

        self.assertEqual(get_downstream_nodes(plan, ["materials_rag_search"]), ["preflight_merge", "red_pre_execution_review"])


if __name__ == "__main__":
    unittest.main()
