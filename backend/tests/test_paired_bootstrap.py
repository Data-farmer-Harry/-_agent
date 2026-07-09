from __future__ import annotations

import json
import unittest

from benchmarks.statistics.bootstrap import paired_bootstrap_ci, paired_statistics_report
from benchmarks.statistics.environment import build_statistics_environment_manifest


class PairedBootstrapTests(unittest.TestCase):
    def test_paired_bootstrap_is_reproducible_with_fixed_seed(self) -> None:
        old_values = [0.1, 0.2, 0.3, 0.4, 0.5]
        new_values = [0.2, 0.25, 0.35, 0.55, 0.6]
        first = paired_bootstrap_ci(old_values, new_values, n_resamples=250, seed=7).to_dict()
        second = paired_bootstrap_ci(old_values, new_values, n_resamples=250, seed=7).to_dict()

        self.assertEqual(first, second)
        self.assertAlmostEqual(first["delta"], 0.09)
        self.assertEqual(first["n_resamples"], 250)
        self.assertEqual(first["seed"], 7)

    def test_statistics_report_aligns_paired_cases_and_reports_missing(self) -> None:
        old = {"a": 0.4, "b": 0.5, "old_only": 0.9}
        new = {"a": 0.5, "b": 0.7, "new_only": 0.1}
        report = paired_statistics_report(old, new, metric_name="judge_overall", data_type="continuous", n_resamples=100, seed=11)

        self.assertEqual(report["paired_case_count"], 2)
        self.assertEqual(report["missing_old_case_ids"], ["new_only"])
        self.assertEqual(report["missing_new_case_ids"], ["old_only"])
        self.assertAlmostEqual(report["bootstrap"]["old_mean"], 0.45)
        self.assertAlmostEqual(report["bootstrap"]["new_mean"], 0.6)
        self.assertEqual(report["effect_size"]["status"], "ok")

    def test_domain_ci_is_not_reported_for_small_domains(self) -> None:
        old = {f"case-{i:02d}": 0.5 for i in range(35)}
        new = {f"case-{i:02d}": 0.6 for i in range(35)}
        domain_by_case = {
            **{f"case-{i:02d}": "small" for i in range(5)},
            **{f"case-{i:02d}": "large" for i in range(5, 35)},
        }
        report = paired_statistics_report(
            old,
            new,
            metric_name="materials_hit@5",
            data_type="rate",
            domain_by_case=domain_by_case,
            n_resamples=100,
            seed=13,
        )

        self.assertEqual(report["domains"]["small"]["case_count"], 5)
        self.assertEqual(report["domains"]["small"]["bootstrap"]["status"], "not_applicable")
        self.assertIn("fewer than 30", report["domains"]["small"]["bootstrap"]["reason"])
        self.assertEqual(report["domains"]["large"]["case_count"], 30)
        self.assertEqual(report["domains"]["large"]["bootstrap"]["status"], "ok")
        self.assertIsNotNone(report["domains"]["large"]["bootstrap"]["ci_low"])

    def test_binary_report_uses_risk_difference_and_mcnemar(self) -> None:
        old = {"a": False, "b": True, "c": False, "d": True}
        new = {"a": True, "b": True, "c": False, "d": False}
        report = paired_statistics_report(old, new, metric_name="case_pass", data_type="binary", n_resamples=100, seed=17)

        self.assertEqual(report["paired_binary"]["risk_difference"]["improvements"], 1)
        self.assertEqual(report["paired_binary"]["risk_difference"]["regressions"], 1)
        self.assertEqual(report["paired_binary"]["mcnemar"]["discordant"], 2)
        self.assertEqual(report["effect_size"]["status"], "not_applicable")
        self.assertIn("Cohen", report["effect_size"]["reason"])

    def test_environment_manifest_records_reproducibility_without_secret_fields(self) -> None:
        manifest = build_statistics_environment_manifest(extra={"evaluation_mode": "unit"})
        payload = json.dumps(manifest, ensure_ascii=False).lower()

        self.assertEqual(manifest["statistics_version"], "materials-statistics/v1")
        self.assertEqual(manifest["default_bootstrap_seed"], 20260706)
        self.assertEqual(manifest["extra"]["evaluation_mode"], "unit")
        self.assertNotIn("api_key", payload)
        self.assertNotIn("token", payload)
        self.assertNotIn("password", payload)


if __name__ == "__main__":
    unittest.main()
