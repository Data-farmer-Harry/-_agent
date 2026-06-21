from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.artifacts import ArtifactService
from app.recognition_simulator import RecognitionSimulationService
from app.state import AxisSpec, CriticalPoint, RecognitionResult
from tests.support import MINI_PNG_DATA_URL


class RecognitionSimulationServiceTests(unittest.TestCase):
    def test_build_bundle_writes_interactive_html_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            service = RecognitionSimulationService(artifact_service)
            result = RecognitionResult(
                system="Al-Zn",
                diagram_type="binary",
                x_axis=AxisSpec(label="composition", minimum=0, maximum=100, unit="at.%"),
                y_axis=AxisSpec(label="temperature", minimum=300, maximum=1000, unit="K"),
                plot_region={"left": 0.12, "top": 0.1, "right": 0.89, "bottom": 0.84, "confidence": 0.9},
                phases=["Liquid", "FCC_A1", "HCP_A3"],
                critical_points=[CriticalPoint(label="eutectic", composition=54.0, temperature=655.0, x_norm=0.52, y_norm=0.45, notes="recognized")],
                confidence=0.82,
                source="llm_recognition_agent",
                raw_summary="识别到 Al-Zn 二元相图截图。",
            )

            bundle = service.build_bundle(
                run_id="run-123",
                recognition_result=result,
                request_message="请识别这张相图。",
                source_image_data_url=MINI_PNG_DATA_URL,
            )

            self.assertIn("Recognized Simulation", bundle.html_content)
            self.assertIn("HTML/canvas", bundle.html_content)
            self.assertIn("recognition-reconstruction-canvas", bundle.html_content)
            self.assertIn("reconstruction_scene", bundle.html_content)
            self.assertIn('data-priority-mode="', bundle.html_content)
            self.assertNotIn("recognition-generated-svg", bundle.html_content)
            self.assertNotIn("phase-source-image", bundle.html_content)
            self.assertNotIn(MINI_PNG_DATA_URL, bundle.html_content)
            self.assertEqual(bundle.summary["recognized_system"], "Al-Zn")
            self.assertEqual(bundle.summary["simulation_render_mode"], "generated_canvas_vector_reconstruction")
            self.assertTrue(bundle.summary["source_image_present"])
            self.assertGreater(bundle.summary["overlay_confidence"], 0.3)
            self.assertIn("structured_path_reconstruction", bundle.html_content)
            self.assertIn("Structured-path mode", bundle.html_content)
            self.assertAlmostEqual(
                bundle.summary["reconstruction_schema"]["controls"]["temperature_default"],
                655.0,
                places=3,
            )
            self.assertEqual(bundle.result_profile.category, "Recognized Simulation")
            self.assertIn("reconstruction_schema", bundle.summary)
            self.assertIn("geometry_model", bundle.summary)
            self.assertIn("traced_contours", bundle.summary["geometry_model"])
            self.assertTrue(Path(bundle.html_path).exists())
            artifact_names = {artifact.name for artifact in bundle.artifacts}
            self.assertIn("result.html", artifact_names)
            self.assertIn("recognition_simulator.json", artifact_names)

    def test_build_bundle_gracefully_backfills_missing_critical_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            service = RecognitionSimulationService(artifact_service)
            result = RecognitionResult(
                system="Pb-Sn",
                diagram_type="binary",
                x_axis=AxisSpec(label="composition", minimum=0, maximum=100, unit="wt.%"),
                y_axis=AxisSpec(label="temperature", minimum=250, maximum=700, unit="K"),
                phases=["Liquid", "FCC_A1"],
                critical_points=[],
                confidence=0.61,
                source="llm_recognition_agent",
                raw_summary="识别到铅锡相图截图。",
            )

            bundle = service.build_bundle(run_id="run-234", recognition_result=result, request_message="请识别铅锡截图。")

            points = bundle.summary["recognized_critical_points"]
            self.assertEqual(len(points), 1)
            self.assertIn("simulated-eutectic", bundle.html_content)
            self.assertEqual(bundle.summary["simulation_render_mode"], "generated_canvas_schema_reconstruction")
            self.assertNotIn("recognition-generated-svg", bundle.html_content)


if __name__ == "__main__":
    unittest.main()
