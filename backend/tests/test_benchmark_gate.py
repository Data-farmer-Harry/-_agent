from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_gate import evaluate_benchmark_gate, write_gate_outputs


def _report(*, passed: bool = True, threshold_passed: bool = True, case_passed: bool = True, critical: bool = False) -> dict:
    details = {}
    if critical:
        details = {"evaluation": {"critical_failures": ["synthetic provenance described as real execution"]}}
    return {
        "schema_version": "agent-benchmark-report/v1",
        "generated_at": "2026-07-07T00:00:00+0800",
        "benchmark_manifest": {"benchmark_version": "test", "cases_total": 1},
        "selected_suites": ["judge_calibration"],
        "passed": passed,
        "thresholds": {"judge_calibration.drift_free_rate": 1.0},
        "threshold_checks": [
            {
                "metric": "judge_calibration.drift_free_rate",
                "value": 1.0 if threshold_passed else 0.0,
                "threshold": 1.0,
                "passed": threshold_passed,
            }
        ],
        "metrics": {"judge_calibration": {"drift_free_rate": 1.0 if threshold_passed else 0.0}},
        "raw_results": {
            "judge_calibration": {
                "suite": "judge_calibration",
                "cases": 1,
                "passed": 1 if case_passed else 0,
                "results": [{"case_id": "case-1", "passed": case_passed, "details": details}],
            }
        },
    }


class BenchmarkGateTests(unittest.TestCase):
    def test_current_report_gate_passes_clean_report(self) -> None:
        gate = evaluate_benchmark_gate(_report())

        self.assertTrue(gate["passed"])
        self.assertTrue(gate["current"]["passed"])
        self.assertEqual(gate["current"]["threshold_failure_count"], 0)
        self.assertIsNone(gate["comparison"])

    def test_current_report_gate_blocks_threshold_failure(self) -> None:
        gate = evaluate_benchmark_gate(_report(passed=False, threshold_passed=False))

        self.assertFalse(gate["passed"])
        self.assertEqual(gate["current"]["threshold_failure_count"], 1)
        self.assertEqual(gate["current"]["threshold_failures"][0]["metric"], "judge_calibration.drift_free_rate")

    def test_current_report_gate_blocks_critical_failure(self) -> None:
        gate = evaluate_benchmark_gate(_report(passed=False, critical=True))

        self.assertFalse(gate["passed"])
        self.assertEqual(gate["current"]["critical_failure_count"], 1)
        self.assertIn("synthetic provenance", gate["current"]["critical_failures"][0]["critical_failures"][0])

    def test_baseline_comparison_blocks_case_regression(self) -> None:
        gate = evaluate_benchmark_gate(_report(), baseline_report=_report(case_passed=True), label="new", baseline_label="old")

        self.assertTrue(gate["passed"])

        regressed_gate = evaluate_benchmark_gate(_report(passed=True, case_passed=False), baseline_report=_report(case_passed=True), label="new", baseline_label="old")

        self.assertFalse(regressed_gate["passed"])
        self.assertFalse(regressed_gate["comparison"]["passed"])
        self.assertEqual(regressed_gate["comparison"]["regressions"]["case_regressions"][0]["case_id"], "judge_calibration/case-1")

    def test_write_gate_outputs_creates_machine_and_markdown_reports(self) -> None:
        gate = evaluate_benchmark_gate(_report(), baseline_report=_report(), n_resamples=50)
        with tempfile.TemporaryDirectory() as tmp_dir:
            outputs = write_gate_outputs(gate, Path(tmp_dir))
            gate_payload = json.loads(Path(outputs["gate"]).read_text(encoding="utf-8"))
            report = Path(outputs["report"]).read_text(encoding="utf-8")

        self.assertTrue(gate_payload["passed"])
        self.assertIn("# MaterialsAgentBench Benchmark Gate", report)
        self.assertIn("comparison_report", outputs)


if __name__ == "__main__":
    unittest.main()
