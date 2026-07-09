from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.compare_versions import compare_benchmark_reports, write_comparison_outputs


def _report(*, suite: str = "materials_multihop", case_rows: list[dict] | None = None, metric_value: float = 1.0, threshold_passed: bool = True) -> dict:
    rows = case_rows or [
        {"case_id": "case-1", "passed": True, "details": {}},
        {"case_id": "case-2", "passed": True, "details": {}},
    ]
    return {
        "schema_version": "agent-benchmark-report/v1",
        "generated_at": "2026-07-06T00:00:00+0800",
        "benchmark_manifest": {"benchmark_version": "test", "cases_total": len(rows)},
        "selected_suites": [suite],
        "thresholds": {f"{suite}.required_hop_completion": 1.0},
        "threshold_checks": [
            {
                "metric": f"{suite}.required_hop_completion",
                "value": metric_value,
                "threshold": 1.0,
                "passed": threshold_passed,
            }
        ],
        "metrics": {
            suite: {
                "success_rate": metric_value,
                "required_hop_completion": metric_value,
            }
        },
        "raw_results": {
            suite: {
                "suite": suite,
                "cases": len(rows),
                "passed": sum(1 for row in rows if row["passed"]),
                "results": rows,
            }
        },
    }


class CompareVersionsTests(unittest.TestCase):
    def test_compare_aligns_paired_cases_and_detects_case_regression(self) -> None:
        old = _report(case_rows=[{"case_id": "a", "passed": True, "details": {}}, {"case_id": "b", "passed": True, "details": {}}, {"case_id": "old-only", "passed": True, "details": {}}])
        new = _report(case_rows=[{"case_id": "a", "passed": True, "details": {}}, {"case_id": "b", "passed": False, "details": {}}, {"case_id": "new-only", "passed": True, "details": {}}])
        comparison = compare_benchmark_reports(old, new, n_resamples=100, seed=3)

        self.assertFalse(comparison["passed"])
        self.assertEqual(comparison["statistics"]["case_pass"]["paired_case_count"], 2)
        self.assertEqual(comparison["statistics"]["case_pass"]["missing_old_case_ids"], ["materials_multihop/new-only"])
        self.assertEqual(comparison["statistics"]["case_pass"]["missing_new_case_ids"], ["materials_multihop/old-only"])
        self.assertEqual(comparison["regressions"]["case_regressions"], [{"case_id": "materials_multihop/b", "old_passed": True, "new_passed": False}])

    def test_threshold_regression_blocks_comparison(self) -> None:
        old = _report(metric_value=1.0, threshold_passed=True)
        new = _report(metric_value=0.5, threshold_passed=False)
        comparison = compare_benchmark_reports(old, new, n_resamples=100, seed=5)

        self.assertFalse(comparison["passed"])
        self.assertEqual(len(comparison["regressions"]["threshold_regressions"]), 1)
        self.assertEqual(comparison["regressions"]["threshold_regressions"][0]["metric"], "materials_multihop.required_hop_completion")
        self.assertLess(comparison["statistics"]["metric_deltas"][0]["delta"], 0)

    def test_new_critical_failure_is_reported(self) -> None:
        old = _report()
        new = _report(
            case_rows=[
                {
                    "case_id": "case-1",
                    "passed": False,
                    "details": {
                        "evaluation": {
                            "critical_failures": ["synthetic provenance described as real execution"],
                        }
                    },
                }
            ],
            metric_value=1.0,
            threshold_passed=True,
        )
        comparison = compare_benchmark_reports(old, new, n_resamples=100, seed=7)

        self.assertFalse(comparison["passed"])
        self.assertEqual(comparison["regressions"]["new_critical_failures"][0]["case_id"], "materials_multihop/case-1")
        self.assertIn("synthetic provenance", comparison["regressions"]["new_critical_failures"][0]["critical_failures"][0])

    def test_write_comparison_outputs_creates_required_artifacts(self) -> None:
        comparison = compare_benchmark_reports(_report(), _report(), n_resamples=100, seed=11)
        with tempfile.TemporaryDirectory() as tmp_dir:
            outputs = write_comparison_outputs(comparison, Path(tmp_dir))
            payloads = {name: Path(path) for name, path in outputs.items()}
            report = payloads["report"].read_text(encoding="utf-8")
            statistics = json.loads(payloads["statistics"].read_text(encoding="utf-8"))
            regressions = json.loads(payloads["regressions"].read_text(encoding="utf-8"))

        self.assertIn("# MaterialsAgentBench Version Comparison", report)
        self.assertIn("case_pass", statistics)
        self.assertEqual(regressions["case_regressions"], [])
        self.assertEqual(set(outputs), {"manifest", "environment", "statistics", "threshold_checks", "regressions", "report"})


if __name__ == "__main__":
    unittest.main()
