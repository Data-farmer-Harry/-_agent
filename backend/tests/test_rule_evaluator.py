from __future__ import annotations

import unittest

from benchmarks.evaluators.rule_evaluator import RuleEvaluationObservation, evaluate_rule_layer
from benchmarks.materials_agent_bench import MATERIALS_AGENT_BENCH_VERSION, MaterialsAgentBenchCase, NOT_APPLICABLE


def _case(**overrides):
    payload = {
        "case_id": "case.rule.demo",
        "benchmark_version": MATERIALS_AGENT_BENCH_VERSION,
        "domain": "lammps_execution",
        "difficulty": "normal",
        "mode": "deterministic",
        "prompt": "Run Cu at 800 K for 4000 steps.",
        "source_dataset": "unit",
        "source_suite": "unit",
        "source_case_id": "unit.demo",
        "expected_route": "lammps.generate",
        "expected_compute_domain": "lammps",
        "locked_constraints": {"material": "Cu", "temperature": 800, "steps": 4000},
        "required_tool_chain": ["lammps_request_interpreter", "lammps_execute", "lammps_result_review"],
        "required_artifacts": ["in.lammps", "thermo.csv", "report.md"],
        "required_evidence": ["registry:cu-eam", "rag:fix-nvt"],
        "tags": ["lammps"],
    }
    payload.update(overrides)
    return MaterialsAgentBenchCase(**payload)


class RuleEvaluatorTests(unittest.TestCase):
    def test_rule_layer_passes_when_structural_gates_are_satisfied(self) -> None:
        result = evaluate_rule_layer(
            _case(),
            RuleEvaluationObservation(
                route_name="lammps.generate",
                compute_domain="lammps",
                locked_constraints={"material": "Cu", "temperature": 800.0, "steps": 4000},
                completed_tools=["lammps_request_interpreter", "lammps_execute", "lammps_result_review"],
                artifacts=[{"name": "in.lammps"}, {"path": "/tmp/run/thermo.csv"}, "report.md"],
                provenance={"actual": "real", "claimed": "real"},
                claims=[{"claim_id": "run", "text": "LAMMPS run completed", "status": "supported"}],
                citations=[
                    {"evidence_id": "registry:cu-eam", "supports": True, "exists": True},
                    {"evidence_id": "rag:fix-nvt", "supports": True, "exists": True},
                ],
                required_hops=[{"name": "registry", "completed": True}, {"name": "rag", "completed": True}],
            ),
        )

        payload = result.to_dict()
        self.assertTrue(payload["passed"])
        self.assertTrue(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["locked_constraint_accuracy"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["tool_chain_completion"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["artifact_completeness"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["real_mock_provenance_accuracy"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["citation_coverage"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["evidence_chain_completeness"]["value"], 1.0)

    def test_locked_constraint_mismatch_is_a_hard_failure(self) -> None:
        result = evaluate_rule_layer(
            _case(),
            {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "locked_constraints": {"material": "Cu", "temperature": 900, "steps": 4000},
                "completed_tools": ["lammps_request_interpreter", "lammps_execute", "lammps_result_review"],
                "artifacts": ["in.lammps", "thermo.csv", "report.md"],
                "provenance": {"actual": "real", "claimed": "real"},
            },
        )

        payload = result.to_dict()
        self.assertFalse(payload["passed"])
        self.assertFalse(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["locked_constraint_accuracy"]["value"], 2 / 3)
        self.assertEqual(payload["metrics"]["critical_hallucination_rate"]["value"], 1.0)
        self.assertTrue(any("temperature" in failure for failure in payload["critical_failures"]))

    def test_synthetic_or_mock_run_cannot_be_described_as_real(self) -> None:
        result = evaluate_rule_layer(
            _case(required_evidence=[]),
            RuleEvaluationObservation(
                route_name="lammps.generate",
                compute_domain="lammps",
                locked_constraints={"material": "Cu", "temperature": 800, "steps": 4000},
                completed_tools=["lammps_request_interpreter", "lammps_execute", "lammps_result_review"],
                artifacts=["in.lammps", "thermo.csv", "report.md"],
                provenance={"actual": "synthetic"},
                final_response="真实执行的 LAMMPS 模拟已经成功完成。",
            ),
        )

        payload = result.to_dict()
        self.assertFalse(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["real_mock_provenance_accuracy"]["value"], 0.0)
        self.assertTrue(any("synthetic provenance described as real" in failure for failure in payload["critical_failures"]))

    def test_failed_execution_cannot_be_reported_as_success(self) -> None:
        result = evaluate_rule_layer(
            _case(required_evidence=[]),
            {
                "locked_constraints": {"material": "Cu", "temperature": 800, "steps": 4000},
                "completed_tools": ["lammps_request_interpreter", "lammps_execute", "lammps_result_review"],
                "artifacts": ["in.lammps", "thermo.csv", "report.md"],
                "provenance": {"actual": "real", "claimed": "real"},
                "execution": {"success": False},
                "final_response": "The LAMMPS run completed successfully and the simulation succeeded.",
            },
        )

        payload = result.to_dict()
        self.assertFalse(payload["hard_gate_passed"])
        self.assertTrue(any("claims success" in failure for failure in payload["critical_failures"]))

    def test_structured_claims_drive_factual_accuracy_and_hallucination_rate(self) -> None:
        result = evaluate_rule_layer(
            _case(required_evidence=[]),
            {
                "claims": [
                    {"claim_id": "supported", "status": "supported", "text": "Cu EAM is supported"},
                    {"claim_id": "unsupported", "status": "unsupported", "text": "A missing plot exists"},
                    {"claim_id": "critical", "status": "contradicted", "text": "Mock was real", "severity": "critical"},
                ],
            },
        )

        payload = result.to_dict()
        self.assertEqual(payload["metrics"]["factual_accuracy"]["value"], 1 / 3)
        self.assertEqual(payload["metrics"]["hallucination_rate"]["value"], 2 / 3)
        self.assertFalse(payload["hard_gate_passed"])
        self.assertTrue(any("critical claim" in failure for failure in payload["critical_failures"]))

    def test_citation_coverage_and_precision_are_deterministic(self) -> None:
        result = evaluate_rule_layer(
            _case(locked_constraints={}, required_tool_chain=[], required_artifacts=[]),
            {
                "citations": [
                    {"evidence_id": "registry:cu-eam", "supports": True, "exists": True},
                    {"evidence_id": "rag:fix-nvt", "supports": False, "exists": True},
                    {"evidence_id": "rag:unrelated", "supports": True, "exists": True},
                ],
            },
        )

        payload = result.to_dict()
        self.assertEqual(payload["metrics"]["citation_coverage"]["value"], 0.5)
        self.assertEqual(payload["metrics"]["citation_precision"]["value"], 2 / 3)
        self.assertFalse(payload["metrics"]["citation_coverage"]["passed"])
        self.assertFalse(payload["metrics"]["citation_precision"]["passed"])

    def test_zero_denominator_metrics_are_not_applicable(self) -> None:
        result = evaluate_rule_layer(
            _case(
                expected_route=None,
                expected_compute_domain=None,
                locked_constraints={},
                required_tool_chain=[],
                required_artifacts=[],
                required_evidence=[],
            ),
            {},
        )

        payload = result.to_dict()
        self.assertEqual(payload["metrics"]["route_accuracy"]["status"], NOT_APPLICABLE)
        self.assertEqual(payload["metrics"]["locked_constraint_accuracy"]["status"], NOT_APPLICABLE)
        self.assertEqual(payload["metrics"]["citation_coverage"]["status"], NOT_APPLICABLE)
        self.assertEqual(payload["metrics"]["factual_accuracy"]["status"], NOT_APPLICABLE)
        self.assertTrue(payload["hard_gate_passed"])

    def test_judge_like_metadata_cannot_override_hard_failure(self) -> None:
        result = evaluate_rule_layer(
            _case(),
            {
                "locked_constraints": {"material": "Al", "temperature": 800, "steps": 4000},
                "metadata": {"judge_score": 5, "judge_summary": "Looks excellent"},
            },
        )

        payload = result.to_dict()
        self.assertFalse(payload["passed"])
        self.assertFalse(payload["hard_gate_passed"])
        self.assertTrue(payload["critical_failures"])


if __name__ == "__main__":
    unittest.main()
