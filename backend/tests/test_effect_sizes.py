from __future__ import annotations

import unittest

from benchmarks.statistics.effect_size import cohens_dz, summarize_distribution
from benchmarks.statistics.paired_tests import mcnemar_exact, paired_risk_difference


class EffectSizeTests(unittest.TestCase):
    def test_cohens_dz_for_continuous_paired_scores(self) -> None:
        result = cohens_dz([3.0, 4.0, 4.0, 5.0], [4.0, 4.5, 5.0, 5.0], data_type="continuous")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["n"], 4)
        self.assertAlmostEqual(result["mean_delta"], 0.625)
        self.assertGreater(result["effect_size"], 0)

    def test_cohens_dz_rejects_binary_metrics(self) -> None:
        with self.assertRaises(ValueError):
            cohens_dz([0, 1, 1, 0], [1, 1, 0, 0], data_type="continuous")
        with self.assertRaises(ValueError):
            cohens_dz([0.2, 0.4], [0.3, 0.5], data_type="binary")

    def test_paired_risk_difference_counts_improvements_and_regressions(self) -> None:
        result = paired_risk_difference([0, 1, 0, 1, 0], [1, 1, 0, 0, 1])

        self.assertEqual(result["n"], 5)
        self.assertEqual(result["improvements"], 2)
        self.assertEqual(result["regressions"], 1)
        self.assertAlmostEqual(result["old_rate"], 0.4)
        self.assertAlmostEqual(result["new_rate"], 0.6)
        self.assertAlmostEqual(result["risk_difference"], 0.2)

    def test_mcnemar_exact_reports_discordant_pairs_and_p_value(self) -> None:
        result = mcnemar_exact([0, 1, 0, 1, 0], [1, 1, 0, 0, 1])

        self.assertEqual(result["improvements"], 2)
        self.assertEqual(result["regressions"], 1)
        self.assertEqual(result["discordant"], 3)
        self.assertGreaterEqual(result["exact_p_value"], 0)
        self.assertLessEqual(result["exact_p_value"], 1)

    def test_distribution_summary_reports_median_and_tail_latency(self) -> None:
        result = summarize_distribution([10, 20, 30, 40, 50])

        self.assertEqual(result["n"], 5)
        self.assertEqual(result["median"], 30)
        self.assertEqual(result["min"], 10)
        self.assertEqual(result["max"], 50)
        self.assertGreater(result["p95"], result["p90"])


if __name__ == "__main__":
    unittest.main()
