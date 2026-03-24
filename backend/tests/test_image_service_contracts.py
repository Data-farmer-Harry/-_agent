from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.phase_diagram_image_service import PhaseDiagramImageService

from tests.support import sample_image_request


class ImageServiceContractTests(unittest.TestCase):
    def test_extract_json_object_handles_fenced_and_prefixed_content(self) -> None:
        service = PhaseDiagramImageService()

        fenced = "```json\n{\"summary\": \"ok\", \"confidence\": 0.7}\n```"
        prefixed = "Here is the JSON you asked for:\n{\"summary\": \"ok\", \"confidence\": 0.4}"

        self.assertEqual(service._extract_json_object(fenced), {"summary": "ok", "confidence": 0.7})
        self.assertEqual(service._extract_json_object(prefixed), {"summary": "ok", "confidence": 0.4})
        self.assertIsNone(service._extract_json_object("not json at all"))

    def test_analyze_image_falls_back_to_manual_spec_on_llm_error(self) -> None:
        service = PhaseDiagramImageService()

        with patch("app.services.phase_diagram_image_service.settings.llm_api_base_url", "https://example.invalid"), patch(
            "app.services.phase_diagram_image_service.settings.llm_api_key", "token"
        ), patch.object(service, "_analyze_with_llm", side_effect=RuntimeError("vision failed")):
            spec, prompt = service.analyze_image(sample_image_request())

        self.assertEqual(spec.detection_mode, "manual_calibrated")
        self.assertEqual(spec.labels, [])
        self.assertEqual(spec.boundaries, [])
        self.assertTrue(prompt)

    def test_analyze_image_discards_invalid_vision_payload_and_returns_manual_spec(self) -> None:
        service = PhaseDiagramImageService()
        llm_payload = {
            "chart_title": "ignored title",
            "summary": "ignored summary",
            "confidence": 0.9,
            "labels": [{"text": "", "x": 1, "y": 2}, {"text": "Too high", "x": 0.4, "y": 9999}],
            "boundaries": [{"name": "Out of range", "points": [[0.0, 9999.0], [6.7, 1601.0]]}],
        }

        with patch("app.services.phase_diagram_image_service.settings.llm_api_base_url", "https://example.invalid"), patch(
            "app.services.phase_diagram_image_service.settings.llm_api_key", "token"
        ), patch.object(service, "_analyze_with_llm", return_value=llm_payload):
            spec, _ = service.analyze_image(sample_image_request())

        self.assertEqual(spec.detection_mode, "manual_calibrated")
        self.assertEqual(spec.chart_title, sample_image_request().chart_title)
        self.assertEqual(spec.summary, service._build_manual_spec(sample_image_request()).summary)

    def test_analyze_image_merges_valid_vision_payload_with_axis_bounded_features(self) -> None:
        service = PhaseDiagramImageService()
        llm_payload = {
            "chart_title": "Vision title",
            "system_name": "Vision system",
            "summary": "Detected one boundary and one label.",
            "confidence": 0.81,
            "notes": ["OCR recovered one label."],
            "labels": [
                {"text": "Liquid", "x": 2.1, "y": 1380.0},
                {"text": "Ignored", "x": 12.0, "y": 1380.0},
            ],
            "boundaries": [
                {"name": "Liquidus", "points": [[0.0, 1530.0], [4.3, 1147.0], [6.7, 1250.0]]},
                {"name": "Ignored", "points": [[-1.0, 10.0], [2.0, 10.0]]},
            ],
        }

        with patch("app.services.phase_diagram_image_service.settings.llm_api_base_url", "https://example.invalid"), patch(
            "app.services.phase_diagram_image_service.settings.llm_api_key", "token"
        ), patch.object(service, "_analyze_with_llm", return_value=llm_payload):
            spec, _ = service.analyze_image(sample_image_request())

        self.assertEqual(spec.detection_mode, "vision_augmented")
        self.assertEqual(spec.chart_title, sample_image_request().chart_title)
        self.assertEqual(spec.system_name, sample_image_request().system_name)
        self.assertEqual(spec.summary, "Detected one boundary and one label.")
        self.assertGreaterEqual(spec.confidence, 0.81)
        self.assertEqual([label.text for label in spec.labels], ["Liquid"])
        self.assertEqual([boundary.name for boundary in spec.boundaries], ["Liquidus"])
        self.assertIn("OCR recovered one label.", spec.notes)
        self.assertEqual(spec.boundaries[0].points[0], [0.0, 1530.0])


if __name__ == "__main__":
    unittest.main()
