from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.build_datasets import build_all_datasets, build_manifest, write_jsonl
from benchmarks.run_benchmarks import (
    _materials_rag_observed,
    _suite_metric_summary,
    _threshold_results,
    run_context_compression_benchmark,
    run_judge_calibration_benchmark,
    run_lammps_quality_benchmark,
    run_lammps_recovery_benchmark,
    run_lammps_red_blue_benchmark,
    run_materials_multihop_benchmark,
    run_memory_conflict_benchmark,
    run_orchestration_benchmark,
    run_review_json_fallback_benchmark,
    run_shared_memory_benchmark,
    validate_datasets,
)


class BenchmarkAssetsTests(unittest.TestCase):
    def test_build_all_datasets_has_expected_suites_and_counts(self) -> None:
        datasets = build_all_datasets()
        self.assertIn("routing_cases", datasets)
        self.assertIn("phase_execution_cases", datasets)
        self.assertIn("external_recognition_cases", datasets)
        self.assertIn("lammps_quality_cases", datasets)
        self.assertIn("lammps_red_blue_cases", datasets)
        self.assertIn("review_json_fallback_cases", datasets)
        self.assertIn("orchestration_cases", datasets)
        self.assertIn("judge_calibration_cases", datasets)
        self.assertIn("lammps_recovery_cases", datasets)
        self.assertIn("memory_followup_cases", datasets)
        self.assertIn("memory_retrieval_cases", datasets)
        self.assertIn("shared_memory_cases", datasets)
        self.assertIn("memory_conflict_cases", datasets)
        self.assertIn("context_compression_cases", datasets)
        self.assertIn("materials_multihop_cases", datasets)
        self.assertIn("mcp_cases", datasets)
        self.assertGreaterEqual(len(datasets["routing_cases"]), 8)
        self.assertGreaterEqual(len(datasets["phase_execution_cases"]), 20)
        self.assertGreaterEqual(len(datasets["external_recognition_cases"]), 4)
        self.assertGreaterEqual(len(datasets["lammps_quality_cases"]), 6)
        self.assertGreaterEqual(len(datasets["lammps_red_blue_cases"]), 7)
        self.assertGreaterEqual(len(datasets["review_json_fallback_cases"]), 5)
        self.assertGreaterEqual(len(datasets["orchestration_cases"]), 5)
        self.assertGreaterEqual(len(datasets["judge_calibration_cases"]), 30)
        self.assertGreaterEqual(len(datasets["lammps_recovery_cases"]), 4)
        self.assertGreaterEqual(len(datasets["shared_memory_cases"]), 4)
        self.assertGreaterEqual(len(datasets["memory_conflict_cases"]), 4)
        self.assertGreaterEqual(len(datasets["context_compression_cases"]), 3)
        self.assertGreaterEqual(len(datasets["materials_multihop_cases"]), 3)
        self.assertGreaterEqual(len(datasets["mcp_cases"]), 5)

    def test_materials_rag_observed_accepts_current_metadata_and_trace_schema(self) -> None:
        self.assertTrue(_materials_rag_observed({"metadata": {"materials_rag": {"used": True, "hit_count": 2}}}))
        self.assertTrue(
            _materials_rag_observed(
                {
                    "trace": [
                        {
                            "tool_name": "materials_rag_search",
                            "success": True,
                            "output": {"hits": [{"title": "LAMMPS pair_style eam"}]},
                        }
                    ]
                }
            )
        )
        self.assertFalse(_materials_rag_observed({"metadata": {"materials_rag": {"used": False, "hit_count": 0}}, "trace": []}))

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
        self.assertEqual(manifest["datasets"]["lammps_quality_cases"], len(reloaded["lammps_quality_cases"]))
        self.assertEqual(manifest["datasets"]["lammps_red_blue_cases"], len(reloaded["lammps_red_blue_cases"]))
        self.assertEqual(manifest["datasets"]["review_json_fallback_cases"], len(reloaded["review_json_fallback_cases"]))
        self.assertEqual(manifest["datasets"]["orchestration_cases"], len(reloaded["orchestration_cases"]))
        self.assertEqual(manifest["datasets"]["judge_calibration_cases"], len(reloaded["judge_calibration_cases"]))
        self.assertEqual(manifest["datasets"]["lammps_recovery_cases"], len(reloaded["lammps_recovery_cases"]))
        self.assertEqual(manifest["datasets"]["shared_memory_cases"], len(reloaded["shared_memory_cases"]))
        self.assertEqual(manifest["datasets"]["memory_conflict_cases"], len(reloaded["memory_conflict_cases"]))
        self.assertEqual(manifest["datasets"]["context_compression_cases"], len(reloaded["context_compression_cases"]))
        self.assertEqual(manifest["datasets"]["materials_multihop_cases"], len(reloaded["materials_multihop_cases"]))

    def test_benchmark_threshold_results_report_pass_and_fail(self) -> None:
        checks = _threshold_results(
            {
                "routing": {"route_accuracy": 1.0, "compute_domain_accuracy": 1.0},
                "rag_recall": {"materials_hit@5": 1.0, "thermo_hit@5": 0.1},
                "lammps_quality": {"fatal_anomaly_recall": 1.0, "valid_run_pass_rate": 1.0, "real_synthetic_guard_rate": 1.0},
                "lammps_red_blue": {
                    "fatal_finding_recall": 1.0,
                    "valid_run_non_block_rate": 1.0,
                    "locked_field_protection_rate": 1.0,
                    "patch_verification_rate": 1.0,
                    "evidence_traceability_rate": 1.0,
                    "rag_evidence_traceability_rate": 1.0,
                    "request_script_consistency_block_rate": 1.0,
                    "bounded_loop_rate": 1.0,
                },
                "review_json_fallback": {"protocol_recovery_rate": 1.0, "invalid_patch_rejection_rate": 1.0},
                "orchestration": {
                    "dependency_correctness_rate": 1.0,
                    "no_concurrency_violation_rate": 1.0,
                    "injected_delay_speedup": 0.25,
                    "degradation_decision_accuracy": 1.0,
                    "partial_report_safety_rate": 1.0,
                },
                "judge_calibration": {
                    "within_one_agreement_rate": 1.0,
                    "parse_recovery_rate": 1.0,
                    "hard_gate_non_override_rate": 1.0,
                    "blind_input_safety_rate": 1.0,
                    "drift_free_rate": 1.0,
                    "quick_ci_backend_available_rate": 1.0,
                    "backend_matrix_secret_safety_rate": 1.0,
                },
                "lammps_recovery": {"checkpoint_resume_correctness": 1.0},
                "shared_memory": {
                    "duplicate_recall": 1.0,
                    "scope_isolation_rate": 1.0,
                    "locked_retention_rate": 1.0,
                    "evidence_traceability_rate": 1.0,
                },
                "memory_conflict": {
                    "conflict_recall": 1.0,
                    "needs_user_rate": 1.0,
                    "quarantine_rate": 1.0,
                    "semantic_candidate_rate": 1.0,
                    "no_incorrect_auto_resolution_rate": 1.0,
                },
                "context_compression": {
                    "l2_traceability_rate": 1.0,
                    "noncompressible_protection_rate": 1.0,
                },
                "materials_multihop": {
                    "required_hop_completion": 1.0,
                    "evidence_chain_completeness": 1.0,
                    "no_unsupported_bridge_claim_rate": 1.0,
                    "final_conclusion_correctness": 1.0,
                    "citation_order_authority_rate": 1.0,
                    "missing_hop_honesty_rate": 1.0,
                },
            }
        )
        by_name = {item["metric"]: item for item in checks}

        self.assertTrue(by_name["routing.route_accuracy"]["passed"])
        self.assertFalse(by_name["rag_recall.thermo_hit@5"]["passed"])
        self.assertTrue(by_name["lammps_quality.fatal_anomaly_recall"]["passed"])
        self.assertTrue(by_name["lammps_red_blue.locked_field_protection_rate"]["passed"])
        self.assertTrue(by_name["review_json_fallback.protocol_recovery_rate"]["passed"])
        self.assertTrue(by_name["orchestration.dependency_correctness_rate"]["passed"])
        self.assertTrue(by_name["orchestration.injected_delay_speedup"]["passed"])
        self.assertTrue(by_name["judge_calibration.within_one_agreement_rate"]["passed"])
        self.assertTrue(by_name["judge_calibration.blind_input_safety_rate"]["passed"])
        self.assertTrue(by_name["judge_calibration.drift_free_rate"]["passed"])
        self.assertTrue(by_name["judge_calibration.quick_ci_backend_available_rate"]["passed"])
        self.assertTrue(by_name["lammps_recovery.checkpoint_resume_correctness"]["passed"])
        self.assertTrue(by_name["shared_memory.duplicate_recall"]["passed"])
        self.assertTrue(by_name["memory_conflict.semantic_candidate_rate"]["passed"])
        self.assertTrue(by_name["context_compression.l2_traceability_rate"]["passed"])
        self.assertTrue(by_name["materials_multihop.required_hop_completion"]["passed"])

    def test_lammps_quality_benchmark_detects_fatal_and_synthetic_cases(self) -> None:
        datasets = build_all_datasets()
        result = run_lammps_quality_benchmark(datasets["lammps_quality_cases"])
        metrics = _suite_metric_summary("lammps_quality", result, elapsed_seconds=1.0)

        self.assertEqual(result["passed"], result["cases"])
        self.assertEqual(metrics["fatal_anomaly_recall"], 1.0)
        self.assertEqual(metrics["valid_run_pass_rate"], 1.0)
        self.assertEqual(metrics["real_synthetic_guard_rate"], 1.0)

    def test_red_blue_and_json_fallback_benchmarks_pass(self) -> None:
        datasets = build_all_datasets()
        red_blue = run_lammps_red_blue_benchmark(datasets["lammps_red_blue_cases"])
        red_blue_metrics = _suite_metric_summary("lammps_red_blue", red_blue, elapsed_seconds=1.0)
        json_fallback = run_review_json_fallback_benchmark(datasets["review_json_fallback_cases"])
        json_metrics = _suite_metric_summary("review_json_fallback", json_fallback, elapsed_seconds=1.0)

        self.assertEqual(red_blue["passed"], red_blue["cases"])
        self.assertEqual(red_blue_metrics["fatal_finding_recall"], 1.0)
        self.assertEqual(red_blue_metrics["locked_field_protection_rate"], 1.0)
        self.assertEqual(red_blue_metrics["patch_verification_rate"], 1.0)
        self.assertEqual(red_blue_metrics["rag_evidence_traceability_rate"], 1.0)
        self.assertEqual(red_blue_metrics["request_script_consistency_block_rate"], 1.0)
        self.assertEqual(red_blue_metrics["bounded_loop_rate"], 1.0)
        self.assertEqual(json_fallback["passed"], json_fallback["cases"])
        self.assertEqual(json_metrics["protocol_recovery_rate"], 1.0)
        self.assertEqual(json_metrics["invalid_patch_rejection_rate"], 1.0)

    def test_lammps_recovery_benchmark_passes(self) -> None:
        datasets = build_all_datasets()
        recovery = run_lammps_recovery_benchmark(datasets["lammps_recovery_cases"])
        recovery_metrics = _suite_metric_summary("lammps_recovery", recovery, elapsed_seconds=1.0)

        self.assertEqual(recovery["passed"], recovery["cases"])
        self.assertEqual(recovery_metrics["checkpoint_resume_correctness"], 1.0)
        self.assertEqual(recovery_metrics["timeout_partial_report_rate"], 1.0)
        self.assertEqual(recovery_metrics["replan_checkpoint_reuse_rate"], 1.0)
        self.assertEqual(recovery_metrics["worker_crash_guard_rate"], 1.0)
        self.assertEqual(recovery_metrics["running_cancel_guard_rate"], 1.0)

    def test_orchestration_benchmark_passes(self) -> None:
        datasets = build_all_datasets()
        orchestration = run_orchestration_benchmark(datasets["orchestration_cases"])
        metrics = _suite_metric_summary("orchestration", orchestration, elapsed_seconds=1.0)

        self.assertEqual(orchestration["passed"], orchestration["cases"])
        self.assertEqual(metrics["dependency_correctness_rate"], 1.0)
        self.assertEqual(metrics["no_concurrency_violation_rate"], 1.0)
        self.assertGreaterEqual(metrics["injected_delay_speedup"], 0.25)
        self.assertEqual(metrics["degradation_decision_accuracy"], 1.0)
        self.assertEqual(metrics["partial_report_safety_rate"], 1.0)

    def test_judge_calibration_benchmark_passes(self) -> None:
        datasets = build_all_datasets()
        judge = run_judge_calibration_benchmark(datasets["judge_calibration_cases"])
        metrics = _suite_metric_summary("judge_calibration", judge, elapsed_seconds=1.0)

        self.assertGreaterEqual(judge["passed"], 4)
        self.assertGreaterEqual(metrics["within_one_agreement_rate"], 0.8)
        self.assertEqual(metrics["parse_recovery_rate"], 1.0)
        self.assertEqual(metrics["hard_gate_non_override_rate"], 1.0)
        self.assertEqual(metrics["blind_input_safety_rate"], 1.0)
        self.assertEqual(metrics["drift_free_rate"], 1.0)
        self.assertEqual(metrics["quick_ci_backend_available_rate"], 1.0)
        self.assertEqual(metrics["backend_matrix_secret_safety_rate"], 1.0)
        self.assertIn("drift_report", judge)
        self.assertIn("backend_matrix", judge)

    def test_shared_memory_advanced_benchmarks_pass(self) -> None:
        datasets = build_all_datasets()
        shared = run_shared_memory_benchmark(datasets["shared_memory_cases"])
        conflict = run_memory_conflict_benchmark(datasets["memory_conflict_cases"])
        compression = run_context_compression_benchmark(datasets["context_compression_cases"])
        shared_metrics = _suite_metric_summary("shared_memory", shared, elapsed_seconds=1.0)
        conflict_metrics = _suite_metric_summary("memory_conflict", conflict, elapsed_seconds=1.0)
        compression_metrics = _suite_metric_summary("context_compression", compression, elapsed_seconds=1.0)

        self.assertEqual(shared["passed"], shared["cases"])
        self.assertEqual(conflict["passed"], conflict["cases"])
        self.assertEqual(compression["passed"], compression["cases"])
        self.assertEqual(shared_metrics["duplicate_recall"], 1.0)
        self.assertEqual(shared_metrics["scope_isolation_rate"], 1.0)
        self.assertEqual(shared_metrics["locked_retention_rate"], 1.0)
        self.assertEqual(shared_metrics["evidence_traceability_rate"], 1.0)
        self.assertEqual(conflict_metrics["conflict_recall"], 1.0)
        self.assertEqual(conflict_metrics["needs_user_rate"], 1.0)
        self.assertEqual(conflict_metrics["quarantine_rate"], 1.0)
        self.assertEqual(conflict_metrics["semantic_candidate_rate"], 1.0)
        self.assertEqual(conflict_metrics["no_incorrect_auto_resolution_rate"], 1.0)
        self.assertEqual(compression_metrics["l2_traceability_rate"], 1.0)
        self.assertEqual(compression_metrics["noncompressible_protection_rate"], 1.0)

    def test_materials_multihop_benchmark_passes(self) -> None:
        datasets = build_all_datasets()
        multihop = run_materials_multihop_benchmark(datasets["materials_multihop_cases"])
        metrics = _suite_metric_summary("materials_multihop", multihop, elapsed_seconds=1.0)

        self.assertEqual(multihop["passed"], multihop["cases"])
        self.assertEqual(metrics["required_hop_completion"], 1.0)
        self.assertEqual(metrics["evidence_chain_completeness"], 1.0)
        self.assertEqual(metrics["no_unsupported_bridge_claim_rate"], 1.0)
        self.assertEqual(metrics["final_conclusion_correctness"], 1.0)
        self.assertEqual(metrics["citation_order_authority_rate"], 1.0)
        self.assertEqual(metrics["missing_hop_honesty_rate"], 1.0)
