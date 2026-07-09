from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.materials_agent_bench import (
    DOMAIN_BY_DATASET,
    MATERIALS_AGENT_BENCH_VERSION,
    NOT_APPLICABLE,
    MaterialsAgentBenchResult,
    build_materials_agent_cases,
    build_materials_agent_manifest,
    build_materials_agent_metric_report,
    load_source_datasets,
    metric_measurement,
    validate_materials_agent_cases,
    write_materials_agent_bench,
)
from benchmarks.build_materials_agent_bench import _summarize_manifest
from benchmarks.freeze_materials_agent_bench import DEFAULT_FREEZE_LOCK_PATH, load_freeze_lock, validate_freeze_lock


class MaterialsAgentBenchSchemaTests(unittest.TestCase):
    def test_adapter_maps_all_known_source_jsonl_cases(self) -> None:
        datasets = load_source_datasets()
        cases = build_materials_agent_cases(datasets)
        expected_count = sum(len(rows) for name, rows in datasets.items() if name in DOMAIN_BY_DATASET)
        manifest = build_materials_agent_manifest(cases)

        self.assertEqual(len(cases), expected_count)
        self.assertEqual(validate_materials_agent_cases(cases), [])
        self.assertEqual(manifest["benchmark_version"], MATERIALS_AGENT_BENCH_VERSION)
        self.assertEqual(manifest["case_count"], expected_count)
        self.assertIn("lammps_execution", manifest["domains"])
        self.assertIn("materials_rag", manifest["domains"])
        self.assertIn("shared_memory", manifest["domains"])
        self.assertIn("final_response", manifest["domains"])
        self.assertGreater(manifest["splits"].get("frozen_test", 0), 0)
        self.assertEqual(manifest["freeze"]["schema_version"], "materials-agent-freeze/v1")
        self.assertEqual(manifest["freeze"]["case_count"], manifest["splits"]["frozen_test"])
        self.assertTrue(manifest["freeze"]["data_leakage"]["ok"])
        self.assertEqual(set(manifest["modes"]).issubset({"deterministic", "real", "live"}), True)

    def test_adapter_preserves_lammps_gold_constraints_and_artifacts(self) -> None:
        cases = build_materials_agent_cases(load_source_datasets())
        by_source = {case.source_case_id: case for case in cases}
        case = by_source["lammps_e2e.cu_heating_full_chain"]

        self.assertEqual(case.domain, "lammps_execution")
        self.assertEqual(case.expected_route, "lammps.generate")
        self.assertEqual(case.expected_compute_domain, "lammps")
        self.assertEqual(case.locked_constraints["material"], "Cu")
        self.assertEqual(case.locked_constraints["temperature"], 800)
        self.assertIn("lammps_execute", case.required_tool_chain)
        self.assertIn("in.lammps", case.required_artifacts)
        self.assertIn("materials_rag_hit", case.required_evidence)

    def test_adapter_maps_rag_blind_cases_to_frozen_materials_rag(self) -> None:
        cases = build_materials_agent_cases(load_source_datasets())
        rag_case = next(case for case in cases if case.source_dataset == "rag_blind_cases")

        self.assertEqual(rag_case.domain, "materials_rag")
        self.assertEqual(rag_case.split, "frozen_test")
        self.assertEqual(rag_case.mode, "deterministic")
        self.assertTrue(rag_case.prompt)
        self.assertTrue(rag_case.required_evidence)
        self.assertEqual(rag_case.metadata["generation"]["frozen_before_first_evaluation"], True)

    def test_adapter_preserves_materials_multihop_hops_and_conclusion(self) -> None:
        cases = build_materials_agent_cases(load_source_datasets())
        multihop_case = next(case for case in cases if case.source_case_id == "materials_multihop.lammps_lost_atoms_repair_chain")

        self.assertEqual(multihop_case.domain, "final_response")
        self.assertEqual(multihop_case.split, "frozen_test")
        self.assertEqual(multihop_case.metadata["generation"]["frozen_before_first_evaluation"], True)
        self.assertEqual(multihop_case.expected_route, "lammps.generate")
        self.assertEqual(multihop_case.locked_constraints["material"], "Cu")
        self.assertIn("lammps_execute", multihop_case.required_tool_chain)
        self.assertIn("run.log", multihop_case.required_artifacts)
        self.assertEqual(multihop_case.metadata["expected_conclusion"], "failed_needs_repair")
        self.assertGreaterEqual(len(multihop_case.metadata["required_hops"]), 6)

    def test_materials_multihop_cases_are_frozen_regression_cases(self) -> None:
        cases = [case for case in build_materials_agent_cases(load_source_datasets()) if case.source_dataset == "materials_multihop_cases"]

        self.assertEqual(len(cases), 3)
        self.assertTrue(all(case.split == "frozen_test" for case in cases))
        self.assertTrue(all(case.metadata["generation"]["kind"] == "materials_multihop_frozen" for case in cases))

    def test_metric_measurement_uses_explicit_not_applicable_for_zero_denominator(self) -> None:
        coverage = metric_measurement("citation_coverage", numerator=0, denominator=0, threshold=0.95)
        accuracy = metric_measurement("locked_constraint_accuracy", numerator=9, denominator=10, threshold=0.8)

        self.assertEqual(coverage.status, NOT_APPLICABLE)
        self.assertIsNone(coverage.value)
        self.assertIsNone(coverage.passed)
        self.assertEqual(accuracy.status, "ok")
        self.assertEqual(accuracy.value, 0.9)
        self.assertTrue(accuracy.passed)

    def test_result_schema_serializes_rule_metrics_without_judge_dependency(self) -> None:
        result = MaterialsAgentBenchResult(
            case_id="demo.case",
            passed=False,
            hard_gate_passed=False,
            metrics={
                "critical_hallucination_rate": metric_measurement(
                    "critical_hallucination_rate",
                    numerator=1,
                    denominator=1,
                    threshold=0.0,
                    greater_is_better=False,
                )
            },
            critical_failures=["mock described as real"],
        )
        payload = result.to_dict()

        self.assertFalse(payload["passed"])
        self.assertFalse(payload["hard_gate_passed"])
        self.assertEqual(payload["metrics"]["critical_hallucination_rate"]["value"], 1.0)
        self.assertFalse(payload["metrics"]["critical_hallucination_rate"]["passed"])
        self.assertNotIn("judge", payload)

    def test_metric_report_preserves_legacy_suite_values(self) -> None:
        report = build_materials_agent_metric_report(
            {
                "routing": {"route_accuracy": 1.0, "avg_case_duration_seconds": 0.01},
                "context_compression": {"l2_traceability_rate": 1.0, "request_field_accuracy": None},
            },
            thresholds={"routing.route_accuracy": 0.9, "context_compression.l2_traceability_rate": 1.0},
        )

        route_metric = report["suites"]["routing"]["metrics"]["route_accuracy"]
        l2_metric = report["suites"]["context_compression"]["metrics"]["l2_traceability_rate"]
        null_metric = report["suites"]["context_compression"]["metrics"]["request_field_accuracy"]
        self.assertEqual(route_metric["value"], 1.0)
        self.assertEqual(route_metric["threshold"], 0.9)
        self.assertTrue(route_metric["passed"])
        self.assertEqual(l2_metric["value"], 1.0)
        self.assertTrue(l2_metric["passed"])
        self.assertEqual(null_metric["status"], NOT_APPLICABLE)
        self.assertIsNone(null_metric["value"])

    def test_writer_outputs_manifest_and_split_case_files(self) -> None:
        cases = build_materials_agent_cases(load_source_datasets())
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "materials_agent_bench"
            manifest = write_materials_agent_bench(cases, output_dir)
            manifest_payload = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            development_lines = (output_dir / "development" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
            frozen_lines = (output_dir / "frozen_test" / "cases.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(manifest_payload, manifest)
        self.assertEqual(manifest["case_count"], len(cases))
        self.assertGreater(len(development_lines), 0)
        self.assertGreater(len(frozen_lines), 0)

    def test_manifest_summary_omits_verbose_case_hashes(self) -> None:
        manifest = build_materials_agent_manifest(build_materials_agent_cases(load_source_datasets()))

        summary = _summarize_manifest(manifest)

        self.assertIn("freeze", summary)
        self.assertNotIn("case_hashes", summary["freeze"])
        self.assertIn("split_hash", summary["freeze"])
        self.assertTrue(summary["freeze"]["data_leakage"]["ok"])

    def test_repository_freeze_lock_matches_current_materials_agent_bench(self) -> None:
        self.assertTrue(DEFAULT_FREEZE_LOCK_PATH.exists(), f"missing freeze lock: {DEFAULT_FREEZE_LOCK_PATH}")
        cases = build_materials_agent_cases(load_source_datasets())
        validation = validate_freeze_lock(cases, load_freeze_lock(DEFAULT_FREEZE_LOCK_PATH))

        self.assertTrue(validation["ok"], validation)
        self.assertEqual(validation["locked"]["case_count"], 390)
        self.assertEqual(validation["locked"]["splits"]["development"], 140)
        self.assertEqual(validation["locked"]["freeze"]["case_count"], 250)


if __name__ == "__main__":
    unittest.main()
