from __future__ import annotations

import unittest

from benchmarks.build_datasets import build_materials_multihop_cases
from benchmarks.evaluators.multihop_evaluator import evaluate_materials_multihop
from benchmarks.materials_agent_bench import build_materials_agent_cases


def _first_case():
    source = build_materials_multihop_cases()[0]
    bench_case = build_materials_agent_cases({"materials_multihop_cases": [source]})[0]
    return source, bench_case


class MultihopEvaluatorTests(unittest.TestCase):
    def test_complete_multihop_chain_passes_all_hard_gates(self) -> None:
        source, bench_case = _first_case()
        result = evaluate_materials_multihop(bench_case, dict(source["observation"]))
        payload = result.to_dict()

        self.assertTrue(payload["passed"])
        self.assertTrue(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["required_hop_completion"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["evidence_chain_completeness"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["final_conclusion_correctness"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["citation_order_authority_rate"]["value"], 1.0)

    def test_missing_required_hop_requires_honest_disclosure(self) -> None:
        source, bench_case = _first_case()
        observation = dict(source["observation"])
        observation["required_hops"] = [
            {**hop, "completed": hop["hop_id"] != "run_log"}
            for hop in observation["required_hops"]
        ]
        observation["final_response"] = "The run failed and I can explain the likely cause."
        result = evaluate_materials_multihop(bench_case, observation)
        payload = result.to_dict()

        self.assertFalse(payload["hard_gate_passed"])
        self.assertLess(payload["metrics"]["required_hop_completion"]["value"], 1.0)
        self.assertEqual(payload["metrics"]["missing_hop_honesty_rate"]["value"], 0.0)
        self.assertTrue(any("missing required hop" in failure for failure in payload["critical_failures"]))

    def test_missing_required_hop_can_pass_honesty_gate_but_not_completion_gate(self) -> None:
        source, bench_case = _first_case()
        observation = dict(source["observation"])
        observation["required_hops"] = [
            {**hop, "completed": hop["hop_id"] != "run_log"}
            for hop in observation["required_hops"]
        ]
        observation["missing_hop_disclosure"] = True
        observation["final_response"] = "证据不足：缺少 run.log，因此不能确认 lost atoms 是否真的发生。"
        result = evaluate_materials_multihop(bench_case, observation)
        payload = result.to_dict()

        self.assertEqual(payload["metrics"]["missing_hop_honesty_rate"]["value"], 1.0)
        self.assertFalse(payload["passed"])
        self.assertTrue(any("evidence chain is incomplete" in failure for failure in payload["critical_failures"]))

    def test_unsupported_bridge_claim_is_critical(self) -> None:
        source, bench_case = _first_case()
        observation = dict(source["observation"])
        observation["claims"] = [
            *observation["claims"],
            {"claim_id": "bad_bridge", "text": "A fabricated dump proves melting", "status": "unsupported", "bridge": True},
        ]
        result = evaluate_materials_multihop(bench_case, observation)
        payload = result.to_dict()

        self.assertFalse(payload["hard_gate_passed"])
        self.assertLess(payload["metrics"]["no_unsupported_bridge_claim_rate"]["value"], 1.0)
        self.assertTrue(any("unsupported bridge claim" in failure for failure in payload["critical_failures"]))

    def test_wrong_citation_order_or_low_authority_fails(self) -> None:
        source, bench_case = _first_case()
        observation = dict(source["observation"])
        citations = list(observation["citations"])
        citations[1], citations[2] = citations[2], citations[1]
        observation["citations"] = citations
        result = evaluate_materials_multihop(bench_case, observation)
        payload = result.to_dict()

        self.assertFalse(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["citation_order_authority_rate"]["value"], 0.0)
        self.assertTrue(any("citation order or authority" in failure for failure in payload["critical_failures"]))

    def test_wrong_final_conclusion_fails(self) -> None:
        source, bench_case = _first_case()
        observation = dict(source["observation"])
        observation["final_conclusion"] = "run_succeeded"
        result = evaluate_materials_multihop(bench_case, observation)
        payload = result.to_dict()

        self.assertFalse(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["final_conclusion_correctness"]["value"], 0.0)
        self.assertTrue(any("final conclusion mismatch" in failure for failure in payload["critical_failures"]))


if __name__ == "__main__":
    unittest.main()
