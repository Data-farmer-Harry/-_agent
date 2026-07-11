from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.build_matterlab_agent_bench_500 import (
    DEFAULT_TARGET_COUNT,
    build_matterlab_agent_bench_cases,
    write_matterlab_agent_bench,
)
from benchmarks.materials_agent_bench import validate_materials_agent_cases
from benchmarks.run_benchmarks import run_matterlab_agent_bench_500


class MatterLabBenchmark500Tests(unittest.TestCase):
    def test_builds_exactly_500_valid_cases(self) -> None:
        cases = build_matterlab_agent_bench_cases()
        case_ids = [case.case_id for case in cases]

        self.assertEqual(len(cases), DEFAULT_TARGET_COUNT)
        self.assertEqual(len(set(case_ids)), DEFAULT_TARGET_COUNT)
        self.assertEqual(validate_materials_agent_cases(cases), [])
        self.assertEqual(sum(1 for case in cases if case.metadata.get("generation", {}).get("kind") == "matterlab_bench_500_augmented"), 130)
        self.assertEqual(sum(1 for case in cases if case.source_dataset == "matterlab_trajectory_cases"), 20)

    def test_covers_agent_specific_domains(self) -> None:
        cases = build_matterlab_agent_bench_cases()
        domains = {case.domain for case in cases}
        sources = {case.source_dataset for case in cases}

        for domain in {
            "lammps_execution",
            "materials_rag",
            "mcp_tooling",
            "shared_memory",
            "orchestration_recovery",
            "final_response",
            "phase_diagram_execution",
            "trajectory_evaluation",
        }:
            self.assertIn(domain, domains)
        for source in {
            "matterlab_lammps_planning_cases",
            "matterlab_rag_multihop_cases",
            "matterlab_tool_mcp_cases",
            "matterlab_memory_cases",
            "matterlab_recovery_cases",
            "matterlab_final_response_cases",
            "matterlab_phase_registry_cases",
            "matterlab_dynamic_route_cases",
            "matterlab_trajectory_cases",
        }:
            self.assertIn(source, sources)

    def test_writer_outputs_manifest_and_split_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "matterlab_agent_bench_500"
            manifest = write_matterlab_agent_bench(output_dir=output_dir)
            manifest_payload = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            frozen_lines = (output_dir / "frozen_test" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
            development_lines = (output_dir / "development" / "cases.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(manifest_payload, manifest)
        self.assertEqual(manifest["benchmark_name"], "MatterLabAgentBench-500+Trajectory")
        self.assertEqual(manifest["case_count"], DEFAULT_TARGET_COUNT)
        self.assertEqual(manifest["construction"]["augmented_case_count"], 130)
        self.assertEqual(manifest["construction"]["augmented_sources"]["matterlab_trajectory_cases"], 20)
        self.assertEqual(len(frozen_lines) + len(development_lines), DEFAULT_TARGET_COUNT)
        self.assertGreater(len(frozen_lines), len(development_lines))

    def test_runner_exposes_rule_layer_suite(self) -> None:
        result = run_matterlab_agent_bench_500(limit=10)

        self.assertEqual(result["suite"], "matterlab_agent_bench_500")
        self.assertEqual(result["cases"], 10)
        self.assertEqual(result["passed"], 10)
        self.assertTrue(result["domain_counts"])

    def test_runner_reports_full_trajectory_slice(self) -> None:
        result = run_matterlab_agent_bench_500()

        self.assertEqual(result["cases"], DEFAULT_TARGET_COUNT)
        self.assertEqual(result["passed"], DEFAULT_TARGET_COUNT)
        self.assertEqual(result["augmented_cases"], 130)
        self.assertEqual(result["trajectory_cases"], 20)
        self.assertEqual(result["domain_counts"]["trajectory_evaluation"], 20)


if __name__ == "__main__":
    unittest.main()
