from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.lammps_contract_baseline import (
    build_lammps_contract_baseline,
    render_lammps_contract_markdown,
    write_lammps_contract_baseline,
)
from benchmarks.run_benchmarks import run_lammps_contract_benchmark


def _suite_result() -> dict[str, object]:
    return {
        "suite": "lammps_contract",
        "cases": 2,
        "passed": 1,
        "results": [
            {
                "case_id": "lammps_contract.cu_heating",
                "passed": True,
                "details": {
                    "status_code": 200,
                    "run_id": "run-cu",
                    "run_status": "completed",
                    "success": True,
                    "termination_reason": "review_passed",
                    "elapsed_seconds": 12.345,
                    "run_mode": "real",
                    "artifact_count": 4,
                    "artifact_names": ["in.lammps", "plot.png", "report.md", "thermo.csv"],
                    "required_artifacts": ["report.md", "plot.png", "thermo.csv"],
                    "missing_required_artifacts": [],
                    "plan_steps": ["lammps_request_interpreter", "lammps_input_codegen", "lammps_result_review"],
                    "required_plan_steps": ["lammps_request_interpreter", "lammps_input_codegen", "lammps_result_review"],
                    "missing_plan_steps": [],
                },
            },
            {
                "case_id": "lammps_contract.al_equilibration",
                "passed": False,
                "details": {
                    "status_code": 200,
                    "run_id": "run-al",
                    "elapsed_seconds": 3.0,
                    "run_mode": "mock",
                    "artifact_names": ["report.md"],
                    "required_artifacts": ["report.md", "plot.png", "thermo.csv"],
                    "plan_steps": ["lammps_request_interpreter"],
                    "required_plan_steps": ["lammps_request_interpreter", "lammps_input_codegen"],
                },
            },
        ],
    }


class LammpsContractBaselineTests(unittest.TestCase):
    def test_build_lammps_contract_baseline_summarizes_modes_artifacts_and_thresholds(self) -> None:
        baseline = build_lammps_contract_baseline(
            _suite_result(),
            suite_metrics={"artifact_completeness": 0.67, "avg_case_duration_seconds": 7.7},
            threshold_checks=[{"metric": "lammps_contract.artifact_completeness", "value": 0.67, "threshold": 0.8, "passed": False}],
            benchmark_manifest={"cases_total": 354},
            generated_at="2026-07-06T00:00:00+0800",
            elapsed_seconds=15.5,
        )

        self.assertEqual(baseline["schema_version"], "lammps-contract-baseline/v1")
        self.assertFalse(baseline["passed"])
        self.assertEqual(baseline["summary"]["cases"], 2)
        self.assertEqual(baseline["summary"]["passed"], 1)
        self.assertEqual(baseline["summary"]["run_modes"], {"mock": 1, "real": 1})
        self.assertFalse(baseline["summary"]["all_required_artifacts_present"])
        self.assertIn("thermo.csv", baseline["summary"]["required_artifacts"])
        self.assertEqual(baseline["cases"][1]["missing_required_artifacts"], ["plot.png", "thermo.csv"])
        self.assertEqual(baseline["cases"][1]["missing_plan_steps"], ["lammps_input_codegen"])

    def test_render_markdown_includes_case_table_and_artifact_universe(self) -> None:
        baseline = build_lammps_contract_baseline(_suite_result())

        markdown = render_lammps_contract_markdown(baseline)

        self.assertIn("# LAMMPS Contract Baseline", markdown)
        self.assertIn("lammps_contract.cu_heating", markdown)
        self.assertIn("plot.png, thermo.csv", markdown)
        self.assertIn("Artifact universe", markdown)

    def test_write_lammps_contract_baseline_creates_json_and_markdown(self) -> None:
        baseline = build_lammps_contract_baseline(_suite_result())
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = write_lammps_contract_baseline(baseline, Path(tmp_dir))
            json_payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            markdown = Path(paths["markdown"]).read_text(encoding="utf-8")

        self.assertEqual(json_payload["schema_version"], "lammps-contract-baseline/v1")
        self.assertIn("LAMMPS Contract Baseline", markdown)

    def test_contract_benchmark_uses_mock_runtime_by_default(self) -> None:
        case = {
            "case_id": "lammps_contract.unit_mock_default",
            "prompt": "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，并返回热力学图。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "required_artifacts": ["report.md", "plot.png", "thermo.csv"],
                "plan_steps": ["lammps_request_interpreter", "lammps_input_codegen", "lammps_result_review"],
            },
        }

        with patch("app.runtimes.lammps.run_lammps", side_effect=AssertionError("real LAMMPS should be opt-in")):
            result = run_lammps_contract_benchmark([case])

        self.assertEqual(result["passed"], 1)
        details = result["results"][0]["details"]
        self.assertEqual(details["run_mode"], "mock")
        self.assertEqual(details["missing_required_artifacts"], [])


if __name__ == "__main__":
    unittest.main()
