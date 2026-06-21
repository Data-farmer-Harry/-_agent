from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.build_datasets import build_all_datasets, build_manifest, write_jsonl
from benchmarks.run_benchmarks import _threshold_results, validate_datasets


class BenchmarkAssetsTests(unittest.TestCase):
    def test_build_all_datasets_has_expected_suites_and_counts(self) -> None:
        datasets = build_all_datasets()
        self.assertIn("routing_cases", datasets)
        self.assertIn("phase_execution_cases", datasets)
        self.assertIn("external_recognition_cases", datasets)
        self.assertIn("memory_followup_cases", datasets)
        self.assertIn("memory_retrieval_cases", datasets)
        self.assertIn("mcp_cases", datasets)
        self.assertGreaterEqual(len(datasets["routing_cases"]), 8)
        self.assertGreaterEqual(len(datasets["phase_execution_cases"]), 20)
        self.assertGreaterEqual(len(datasets["external_recognition_cases"]), 4)
        self.assertGreaterEqual(len(datasets["mcp_cases"]), 5)

    def test_generated_datasets_validate_cleanly(self) -> None:
        datasets = build_all_datasets()
        self.assertEqual(validate_datasets(datasets), [])

    def test_manifest_matches_written_dataset_counts(self) -> None:
        datasets = build_all_datasets()
        manifest = build_manifest(datasets)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for name, rows in datasets.items():
                write_jsonl(root / f"{name}.jsonl", rows)
            reloaded = {
                path.stem: [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                for path in root.glob("*.jsonl")
            }

        self.assertEqual(manifest["datasets"]["routing_cases"], len(reloaded["routing_cases"]))
        self.assertEqual(manifest["datasets"]["phase_execution_cases"], len(reloaded["phase_execution_cases"]))
        self.assertEqual(manifest["datasets"]["external_recognition_cases"], len(reloaded["external_recognition_cases"]))

    def test_benchmark_threshold_results_report_pass_and_fail(self) -> None:
        checks = _threshold_results(
            {
                "routing": {"route_accuracy": 1.0, "compute_domain_accuracy": 1.0},
                "rag_recall": {"materials_hit@5": 1.0, "thermo_hit@5": 0.1},
            }
        )
        by_name = {item["metric"]: item for item in checks}

        self.assertTrue(by_name["routing.route_accuracy"]["passed"])
        self.assertFalse(by_name["rag_recall.thermo_hit@5"]["passed"])
