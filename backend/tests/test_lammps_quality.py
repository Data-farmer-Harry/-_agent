from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.artifacts import ArtifactService
from app.lammps.config import LammpsConfig
from app.lammps.quality import ThermoParseError, build_physical_quality_report, parse_real_thermo_to_csv
from app.lammps.runner import run_mock
from app.runtimes.lammps import LammpsRuntime
from tests.support import ScriptedLLMClient, build_request


class _NoopMaterialsRagService:
    @staticmethod
    def search(*args, **kwargs):  # noqa: ANN002, ANN003
        return []

    @staticmethod
    def build_context(*args, **kwargs):  # noqa: ANN002, ANN003
        return ""


class LammpsQualityTests(unittest.TestCase):
    def test_real_thermo_parser_fails_empty_stdout_without_seeding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            thermo_path = Path(tmp_dir) / "thermo.csv"

            with self.assertRaises(ThermoParseError):
                parse_real_thermo_to_csv("LAMMPS finished but no thermo table", thermo_path)

            self.assertFalse(thermo_path.exists())

    def test_mock_quality_report_marks_synthetic_not_scientific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            request = {
                "material": "Cu",
                "task_type": "heating",
                "temperature": 800,
                "steps": 1000,
                "dump_file": "dump.atom",
            }
            metrics = run_mock(output_dir, request, "Mock mode forced by test.")

            report = build_physical_quality_report(
                output_dir=output_dir,
                request=request,
                run_mode="mock",
                metrics=metrics,
                execution_error="Mock mode forced by test.",
            )

        self.assertTrue(report.passed)
        self.assertTrue(report.synthetic_thermo)
        self.assertFalse(report.scientific_result_passed)
        self.assertGreaterEqual(report.thermo_rows, 2)
        self.assertEqual(metrics["synthetic_thermo"], True)
        self.assertIn("synthetic thermo", " ".join(report.warnings).lower())

    def test_runtime_terminates_real_thermo_parse_failure_without_mock_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp)),
                llm_client=ScriptedLLMClient(),
                materials_rag_service=_NoopMaterialsRagService(),
                config_loader=lambda: LammpsConfig(
                    allow_mock_fallback=True,
                    force_mock=False,
                    lammps_command="/bin/echo",
                    potentials_dir=str(Path(tmp)),
                    max_retries=0,
                    lammps_preflight_dag_enabled=False,
                ),
            )
            with patch(
                "app.runtimes.lammps.run_lammps",
                side_effect=ThermoParseError("thermo_parse_failed: no numeric thermo rows found in real LAMMPS stdout."),
            ):
                result = runtime.run(
                    run_id="lammps-thermo-parse-failed",
                    request=build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。"),
                )

        self.assertFalse(result.success)
        self.assertEqual(result.termination_reason, "thermo_parse_failed")
        self.assertEqual(result.metadata["run_mode"], "real")
        self.assertFalse(result.metadata["quality"]["passed"])
        self.assertFalse(result.metadata["quality"]["scientific_result_passed"])
        self.assertIn("thermo_parse_failed", " ".join(result.metadata["quality"]["issues"]))
        self.assertIn("quality_report.json", {artifact.name for artifact in result.artifacts})

    def test_runtime_mock_success_exposes_quality_report_as_non_scientific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp)),
                llm_client=ScriptedLLMClient(),
                materials_rag_service=_NoopMaterialsRagService(),
                config_loader=lambda: LammpsConfig(
                    allow_mock_fallback=True,
                    force_mock=True,
                    lammps_command="",
                    potentials_dir="",
                    max_retries=0,
                    lammps_preflight_dag_enabled=False,
                ),
            )
            result = runtime.run(
                run_id="lammps-mock-quality",
                request=build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。"),
            )

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["run_mode"], "mock")
        self.assertTrue(result.metadata["quality"]["passed"])
        self.assertTrue(result.metadata["quality"]["synthetic_thermo"])
        self.assertFalse(result.metadata["quality"]["scientific_result_passed"])
        self.assertIn(result.metadata["review"]["review_mode"], {"deterministic_red_review", "llm_plus_deterministic_red_review"})
        self.assertIn("red_review", result.metadata["review"])
        self.assertIn("quality_report.json", {artifact.name for artifact in result.artifacts})
        self.assertIn("red_review_post.json", {artifact.name for artifact in result.artifacts})

    def test_red_blue_feature_flag_can_roll_back_to_legacy_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp)),
                llm_client=ScriptedLLMClient(),
                materials_rag_service=_NoopMaterialsRagService(),
                config_loader=lambda: LammpsConfig(
                    allow_mock_fallback=True,
                    force_mock=True,
                    lammps_command="",
                    potentials_dir="",
                    max_retries=0,
                    lammps_preflight_dag_enabled=False,
                    lammps_red_blue_review_enabled=False,
                ),
            )
            result = runtime.run(
                run_id="lammps-legacy-review",
                request=build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。"),
            )

        artifact_names = {artifact.name for artifact in result.artifacts}
        self.assertTrue(result.success)
        self.assertFalse(result.metadata["config"]["lammps_red_blue_review_enabled"])
        self.assertEqual(result.metadata["review"]["review_mode"], "legacy_review")
        self.assertNotIn("red_review", result.metadata["review"])
        self.assertIn("quality_report.json", artifact_names)
        self.assertNotIn("red_review_post.json", artifact_names)


if __name__ == "__main__":
    unittest.main()
