from __future__ import annotations

import unittest

from benchmarks.build_datasets import build_all_datasets
from benchmarks.evaluators.judge_evaluator import (
    build_blind_judge_input,
    build_judge_backend_matrix,
    build_judge_drift_report,
    deterministic_judge_report,
    evaluate_judge_calibration_case,
    evaluate_judge_with_provider,
    parse_judge_payload,
)
from benchmarks.evaluators.judge_provider import JudgeProviderConfig, judge_provider_config_from_env, sanitized_provider_metadata
from benchmarks.evaluators.rule_evaluator import evaluate_rule_layer
from benchmarks.materials_agent_bench import build_materials_agent_cases


class _FakeJudgeClient:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_system_prompt = ""
        self.last_user_prompt = ""

    def chat_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.0) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return self.payload


class LlmJudgeContractTests(unittest.TestCase):
    def _case_and_row(self, suffix: str):
        rows = build_all_datasets()["judge_calibration_cases"]
        row = next(item for item in rows if item["case_id"].endswith(suffix))
        case = next(
            item
            for item in build_materials_agent_cases({"judge_calibration_cases": rows})
            if item.source_case_id == row["case_id"]
        )
        return case, row

    def test_blind_input_excludes_source_and_human_labels(self) -> None:
        case, row = self._case_and_row("valid_lammps_report")
        rule_result = evaluate_rule_layer(case, row["observation"])
        blind = build_blind_judge_input(case, row["observation"], rule_result)
        serialized = str(blind).lower()

        self.assertNotIn("source_dataset", serialized)
        self.assertNotIn("source_case_id", serialized)
        self.assertNotIn("human_scores", serialized)
        self.assertNotIn("raw_judge_payload", serialized)
        self.assertIn("hard_gate_summary", blind)

    def test_judge_cannot_override_hard_gate_failure(self) -> None:
        case, row = self._case_and_row("synthetic_claimed_real_hard_gate")
        result = evaluate_judge_calibration_case(case, row)

        self.assertFalse(result.report.hard_gate_passed)
        self.assertFalse(result.report.passed)
        self.assertFalse(result.hard_gate_override)
        self.assertIn("synthetic provenance described as real execution", " ".join(result.report.issues))

    def test_provider_hard_gate_override_is_forced_back_to_failure(self) -> None:
        case, row = self._case_and_row("synthetic_claimed_real_hard_gate")
        fallback = deterministic_judge_report(case, row["observation"])
        parsed = parse_judge_payload(
            '{"scores":{"factuality":5,"logical_consistency":5,"citation_quality":5,"physical_validity":5,"actionable_clarity":5},"overall_score":5,"passed":true,"hard_gate_passed":true}',
            fallback_report=fallback,
        )

        self.assertFalse(parsed.hard_gate_passed)
        self.assertFalse(parsed.passed)
        self.assertIn("provider_attempted_hard_gate_override", parsed.issues)
        self.assertTrue(parsed.metadata["provider_hard_gate_passed"])
        self.assertFalse(parsed.metadata["deterministic_hard_gate_passed"])

    def test_parse_fallback_recovers_invalid_payload_safely(self) -> None:
        case, row = self._case_and_row("invalid_json_deterministic_fallback")
        fallback = deterministic_judge_report(case, row["observation"])
        parsed = parse_judge_payload("not valid json", fallback_report=fallback)

        self.assertEqual(parsed.parse_mode, "deterministic_fallback")
        self.assertEqual(parsed.cache_key, fallback.cache_key)
        self.assertTrue(parsed.hard_gate_passed)

    def test_normalized_json_fence_is_parsed(self) -> None:
        case, row = self._case_and_row("normalized_json_fence")
        result = evaluate_judge_calibration_case(case, row)

        self.assertEqual(result.report.parse_mode, "normalized")
        self.assertEqual(result.report.scores.actionable_clarity, 4)
        self.assertEqual(result.within_one_agreement, 1.0)

    def test_judge_drift_report_accepts_current_calibration(self) -> None:
        rows = build_all_datasets()["judge_calibration_cases"]
        cases = {
            item.source_case_id: item
            for item in build_materials_agent_cases({"judge_calibration_cases": rows})
        }
        calibrations = [evaluate_judge_calibration_case(cases[row["case_id"]], row) for row in rows]

        report = build_judge_drift_report(calibrations)

        self.assertFalse(report.drift_detected)
        self.assertGreaterEqual(report.within_one_agreement_rate, 0.8)
        self.assertLessEqual(report.mean_absolute_error, 0.8)
        self.assertEqual(report.parse_recovery_rate, 1.0)
        self.assertEqual(report.hard_gate_override_rate, 0.0)
        self.assertTrue(report.calibration_signature)

    def test_judge_backend_matrix_reports_capabilities_without_secret_values(self) -> None:
        matrix = build_judge_backend_matrix(
            {
                "OPENROUTER_API_KEY": "test-key",
                "DASHSCOPE_API_KEY": "",
            }
        )
        payload = matrix.model_dump(mode="json")
        serialized = str(payload)
        by_backend = {item["backend"]: item for item in payload["backends"]}

        self.assertTrue(by_backend["offline_contract"]["configured"])
        self.assertTrue(by_backend["offline_contract"]["allowed_in_quick_ci"])
        self.assertTrue(by_backend["openrouter"]["configured"])
        self.assertFalse(by_backend["dashscope"]["configured"])
        self.assertIn("DASHSCOPE_API_KEY", payload["missing_required_env"])
        self.assertNotIn("test-key", serialized)

    def test_live_judge_provider_uses_blinded_prompt_and_sanitized_metadata(self) -> None:
        case, row = self._case_and_row("valid_lammps_report")
        fake_client = _FakeJudgeClient(
            '{"scores":{"factuality":5,"logical_consistency":5,"citation_quality":5,"physical_validity":5,"actionable_clarity":5},"overall_score":5,"passed":true,"hard_gate_passed":true,"issues":[]}'
        )
        provider_config = JudgeProviderConfig(
            provider="openrouter",
            model="test-judge-model",
            api_base_url="https://openrouter.example/api/v1",
            api_key="or-secret-test-key",
            enabled=True,
        )

        report = evaluate_judge_with_provider(
            case,
            row["observation"],
            provider_config=provider_config,
            client=fake_client,
            require_live=True,
        )
        serialized = str(report.model_dump(mode="json"))

        self.assertTrue(report.passed)
        self.assertEqual(report.parse_mode, "strict")
        self.assertEqual(report.judge_version, "openrouter:test-judge-model")
        self.assertIn("MaterialsAgentBench", fake_client.last_user_prompt)
        self.assertNotIn("source_case_id", fake_client.last_user_prompt)
        self.assertNotIn("human_scores", fake_client.last_user_prompt)
        self.assertNotIn("or-secret-test-key", serialized)
        self.assertTrue(report.metadata["provider"]["api_key_present"])

    def test_judge_provider_env_config_requires_explicit_live_enable(self) -> None:
        config = judge_provider_config_from_env(
            {
                "MATERIALS_JUDGE_PROVIDER": "openrouter",
                "OPENROUTER_API_KEY": "or-secret-test-key",
                "OPENROUTER_JUDGE_MODEL": "test-model",
            }
        )
        metadata = sanitized_provider_metadata(config)
        serialized = str(metadata)

        self.assertFalse(config.configured)
        self.assertFalse(metadata["configured"])
        self.assertTrue(metadata["api_key_present"])
        self.assertNotIn("or-secret-test-key", serialized)


if __name__ == "__main__":
    unittest.main()
