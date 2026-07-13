from __future__ import annotations

import unittest

from app.core.llm_route_learning import LearnedPolicyConfig, LearnedRouteRecommender, extract_route_features
from app.lammps.ir import LammpsIRValidationError, compile_ir, request_to_ir, validate_ir
from app.lammps.multifidelity import evaluate_pilot, plan_multifidelity_run
from app.materials_rag.models import MaterialsRagDocument, MaterialsRagHit
from app.materials_rag.graph import build_graph_evidence, community_summaries
from app.orchestration import DAGNode, DAGNodeResult, DAGPlan
from app.orchestration.executor import DAGExecutionResult
from app.orchestration.reward import ProcessRewardModel, build_plan_variants, search_plans
from app.rag.uncertainty import ConformalRiskCalibrator, estimate_retrieval_uncertainty


class DeployedMLPRouteTests(unittest.TestCase):
    def test_deployed_model_distinguishes_fast_and_vision_calls(self) -> None:
        recommender = LearnedRouteRecommender(LearnedPolicyConfig(enabled=True))
        self.assertTrue(recommender.available, recommender.load_error)

        fast = recommender.model.recommend(  # type: ignore[union-attr]
            extract_route_features(
                system_prompt="Answer briefly.",
                user_prompt="你好，请用一句话介绍系统。",
                max_tokens=250,
                temperature=0.1,
                capability="chat",
                multimodal=False,
            )
        )
        vision = recommender.model.recommend(  # type: ignore[union-attr]
            extract_route_features(
                system_prompt="Analyze the uploaded plot.",
                user_prompt="识别相图中的坐标轴和相区。",
                max_tokens=1400,
                temperature=0.1,
                capability="vision.recognition",
                multimodal=True,
            )
        )

        self.assertEqual(fast.tier, "fast")
        self.assertEqual(vision.tier, "vision")


class ProcessRewardSearchTests(unittest.TestCase):
    def _plan(self) -> DAGPlan:
        return DAGPlan(
            plan_id="scientific-plan",
            nodes=[
                DAGNode(node_id="retrieve", node_type="rag", critical=False, retryable=True, max_attempts=2, output_keys=["evidence_refs"]),
                DAGNode(node_id="validate", node_type="validate", dependencies=["retrieve"], critical=True, output_keys=["validation_report"]),
            ],
        )

    def test_best_of_n_returns_scored_plan_and_process_trace_reward(self) -> None:
        plan = self._plan()
        search = search_plans(build_plan_variants(plan), latency_budget_seconds=300)
        self.assertEqual(len(search.candidate_scores), 3)
        self.assertIn("candidate_policy", search.selected_plan.metadata)

        execution = DAGExecutionResult(
            plan_id=search.selected_plan.plan_id,
            status="completed",
            topological_order=search.selected_plan.topological_order(),
            results={
                "retrieve": DAGNodeResult(node_id="retrieve", status="completed", evidence_refs=["doc:1"]),
                "validate": DAGNodeResult(node_id="validate", status="completed", evidence_refs=["report:1"]),
            },
        )
        reward = ProcessRewardModel().score_execution(search.selected_plan, execution)
        self.assertEqual(reward["progress_rate"], 1.0)
        self.assertGreater(reward["total_reward"], 0.0)


class NeuroSymbolicIRTests(unittest.TestCase):
    def test_valid_request_compiles_through_typed_ir(self) -> None:
        ir = request_to_ir(
            {"material": "Cu", "potential_family": "eam", "task_type": "heating", "temperature": 800, "initial_temp": 300, "steps": 5000, "ensemble": "NVT", "time_step": 0.001}
        )
        report = validate_ir(ir)
        compiled = compile_ir(ir)
        self.assertTrue(report.passed)
        self.assertEqual(compiled["material"], "Cu")
        self.assertEqual(compiled["ir_validation"]["passed"], True)

    def test_ir_rejects_physically_inconsistent_heating(self) -> None:
        ir = request_to_ir(
            {"material": "Cu", "potential_family": "eam", "task_type": "heating", "temperature": 300, "initial_temp": 600, "steps": 5000, "ensemble": "NVT", "time_step": 0.001}
        )
        with self.assertRaises(LammpsIRValidationError):
            compile_ir(ir)


class GraphRagAndUncertaintyTests(unittest.TestCase):
    def test_graph_paths_link_material_potential_and_tool_entities(self) -> None:
        docs = [
            MaterialsRagDocument(id="a", domain="lammps", doc_type="guide", title="Cu EAM", content="Cu EAM potential", materials=["Cu"], methods=["EAM"], tools=["LAMMPS"]),
            MaterialsRagDocument(id="b", domain="lammps", doc_type="error", title="Cu lost atoms", content="diagnose lost atoms", materials=["Cu"], methods=["EAM"], tools=["LAMMPS"]),
        ]
        seed = MaterialsRagHit(document=docs[0], score=3.0, lexical_score=2.0, vector_score=0.8)
        evidence = build_graph_evidence("Cu EAM LAMMPS", docs, [seed])
        self.assertGreater(evidence["b"].score, 0.0)
        self.assertTrue(evidence["b"].paths)
        self.assertIn("lammps:cu", community_summaries(docs))

    def test_uncertainty_policy_can_answer_or_abstain(self) -> None:
        document = MaterialsRagDocument(id="a", domain="lammps", doc_type="guide", title="Cu", content="evidence")
        strong_hits = [
            MaterialsRagHit(document=document, score=8.0, lexical_score=2.0, bm25_score=1.0, vector_score=0.9, rerank_score=0.9, graph_score=0.8),
            MaterialsRagHit(document=document.model_copy(update={"id": "b"}), score=2.0, lexical_score=1.0, bm25_score=0.5, vector_score=0.6, rerank_score=0.7, graph_score=0.5),
        ]
        self.assertEqual(estimate_retrieval_uncertainty([]).action, "abstain")
        self.assertEqual(estimate_retrieval_uncertainty(strong_hits).action, "answer")
        calibrator = ConformalRiskCalibrator.fit([0.9, 0.8, 0.6, 0.4], [True, True, False, False])
        self.assertGreaterEqual(calibrator.answer_threshold, 0.5)


class MultiFidelityTests(unittest.TestCase):
    def test_high_cost_heating_task_uses_pilot_and_continues_when_stable(self) -> None:
        plan = plan_multifidelity_run(
            {"task_type": "heating", "temperature": 1500, "steps": 20_000, "time_step": 0.001},
            enabled=True,
        )
        self.assertTrue(plan.requires_pilot)
        decision = evaluate_pilot(
            plan,
            execution_success=True,
            quality_passed=True,
            scientific_result_passed=True,
        )
        self.assertEqual(decision.action, "continue_full")
        self.assertGreaterEqual(decision.stability_score, 0.9)

    def test_failed_pilot_stops_full_run(self) -> None:
        plan = plan_multifidelity_run(
            {"task_type": "heating", "temperature": 1500, "steps": 20_000, "time_step": 0.006},
            enabled=True,
        )
        decision = evaluate_pilot(
            plan,
            execution_success=False,
            quality_passed=False,
            scientific_result_passed=False,
            fatal_anomalies=1,
        )
        self.assertEqual(decision.action, "stop")
        self.assertGreater(decision.failure_probability, 0.5)


if __name__ == "__main__":
    unittest.main()
