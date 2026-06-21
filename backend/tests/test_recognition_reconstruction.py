from __future__ import annotations

import base64
import json
import re
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from app.recognition_reconstruction import (
    build_reconstruction_schema,
    fit_reconstruction_geometry,
    render_reconstruction_html,
)
from app.recognition_reconstruction.canvas_vectorize import render_canvas_vector_scene_to_rgb
from app.recognition_reconstruction.vector_trace import _candidate_mask, _crop_plot_region, _load_image
from app.state import AxisSpec, CriticalPoint, RecognitionResult, ResultProfile
from tests.support import MINI_PNG_DATA_URL


def _image_data_url_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    media_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _rasterize_contours(contours: list[list[list[float]]], *, width: int, height: int) -> np.ndarray:
    canvas = np.zeros((height, width), dtype=bool)
    for contour in contours:
        if len(contour) < 2:
            continue
        points = [
            (
                int(round(max(0.0, min(1.0, point[0])) * max(width - 1, 1))),
                int(round(max(0.0, min(1.0, point[1])) * max(height - 1, 1))),
            )
            for point in contour
        ]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            steps = max(abs(x1 - x0), abs(y1 - y0), 1) * 2
            xs = np.linspace(x0, x1, steps + 1)
            ys = np.linspace(y0, y1, steps + 1)
            xi = np.clip(np.round(xs).astype(int), 0, width - 1)
            yi = np.clip(np.round(ys).astype(int), 0, height - 1)
            canvas[yi, xi] = True
    return canvas


def _trace_quality_metrics(result: RecognitionResult, asset_path: Path) -> dict[str, float]:
    image_data_url = _image_data_url_from_path(asset_path)
    schema = build_reconstruction_schema(
        result,
        request_message="请识别这张图。",
        source_image_data_url=image_data_url,
    )
    geometry = fit_reconstruction_geometry(schema, source_image_data_url=image_data_url)
    image = _load_image(image_data_url)
    assert image is not None
    traced_schema = schema.model_copy(
        update={
            "plot_region": schema.plot_region.model_copy(
                update={
                    "left": geometry.plot_left_ratio,
                    "top": geometry.plot_top_ratio,
                    "right": geometry.plot_right_ratio,
                    "bottom": geometry.plot_bottom_ratio,
                }
            )
        }
    )
    cropped = _crop_plot_region(image, traced_schema)
    assert cropped is not None
    crop, _, _, _, _ = cropped
    target_width = 320
    scale = target_width / max(float(crop.shape[1]), 1.0)
    target_height = max(120, int(round(crop.shape[0] * scale)))
    resized = np.asarray(
        Image.fromarray(crop.astype(np.uint8)).resize((target_width, target_height), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    mask, _ = _candidate_mask(resized)
    tolerant_mask = ndimage.binary_dilation(mask, iterations=2)
    rendered = _rasterize_contours(geometry.traced_contours, width=target_width, height=target_height)
    rendered_points = int(rendered.sum())
    overlap_points = int(np.logical_and(rendered, tolerant_mask).sum())
    legend_zone = np.zeros_like(rendered, dtype=bool)
    legend_zone[: max(1, int(target_height * 0.26)), : max(1, int(target_width * 0.38))] = True
    bottom_zone = np.zeros_like(rendered, dtype=bool)
    bottom_zone[int(target_height * 0.90) :, :] = True
    major_spans: list[float] = []
    for contour in geometry.traced_contours:
        if len(contour) < 2:
            continue
        xs = [point[0] for point in contour]
        major_spans.append(max(xs) - min(xs))

    return {
        "precision": overlap_points / max(rendered_points, 1),
        "legend_fraction": int(np.logical_and(rendered, legend_zone).sum()) / max(rendered_points, 1),
        "bottom_fraction": int(np.logical_and(rendered, bottom_zone).sum()) / max(rendered_points, 1),
        "major_span": max(major_spans, default=0.0),
        "contour_count": float(len(geometry.traced_contours)),
        "confidence": float(geometry.traced_confidence),
    }


def _payload_from_rendered_html(html: str) -> dict[str, object]:
    match = re.search(
        r'<script type="application/json" id="recognition-simulator-data">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise AssertionError("recognition-simulator-data payload missing")
    return json.loads(match.group(1))


def _canvas_rgb_from_payload(payload: dict[str, object]) -> np.ndarray:
    scene = payload.get("reconstruction_scene")
    if not isinstance(scene, dict):
        raise AssertionError("reconstruction_scene payload missing")
    return render_canvas_vector_scene_to_rgb(scene)


def _canvas_similarity_metrics(result: RecognitionResult, asset_path: Path) -> dict[str, float]:
    image_data_url = _image_data_url_from_path(asset_path)
    schema = build_reconstruction_schema(
        result,
        request_message="请识别这张图。",
        source_image_data_url=image_data_url,
    )
    geometry = fit_reconstruction_geometry(schema, source_image_data_url=image_data_url)
    profile = ResultProfile(
        category="Recognized Simulation",
        source_label="recognized phase-diagram image",
        mode_label="interactive simulator",
        trust_level="medium",
        confidence=result.confidence,
        trust_statement="structured deterministic reconstruction",
        evidence=["schema -> validator -> curve fitting -> renderer"],
    )
    html = render_reconstruction_html(
        schema,
        geometry,
        profile,
        source_image_data_url=image_data_url,
    )
    payload = _payload_from_rendered_html(html)
    canvas_rgb = _canvas_rgb_from_payload(payload)
    original = np.asarray(
        Image.open(asset_path).convert("RGB").resize(
            (canvas_rgb.shape[1], canvas_rgb.shape[0]),
            Image.Resampling.LANCZOS,
        ),
        dtype=np.uint8,
    )
    similarity = 1.0 - float(np.abs(canvas_rgb.astype(np.int16) - original.astype(np.int16)).mean()) / 255.0
    return {
        "similarity": similarity,
        "width": float(canvas_rgb.shape[1]),
        "height": float(canvas_rgb.shape[0]),
    }


def _render_output_structure_metrics(result: RecognitionResult, asset_path: Path) -> dict[str, float | bool]:
    image_data_url = _image_data_url_from_path(asset_path)
    schema = build_reconstruction_schema(
        result,
        request_message="请识别这张图。",
        source_image_data_url=image_data_url,
    )
    geometry = fit_reconstruction_geometry(schema, source_image_data_url=image_data_url)
    profile = ResultProfile(
        category="Recognized Simulation",
        source_label="recognized phase-diagram image",
        mode_label="interactive simulator",
        trust_level="medium",
        confidence=result.confidence,
        trust_statement="structured deterministic reconstruction",
        evidence=["schema -> validator -> curve fitting -> renderer"],
    )
    html = render_reconstruction_html(
        schema,
        geometry,
        profile,
        source_image_data_url=image_data_url,
    )
    payload = _payload_from_rendered_html(html)
    return {
        "render_mode_vector_canvas": payload.get("render_mode") == "generated_canvas_vector_reconstruction",
        "render_mode_schema_canvas": payload.get("render_mode") == "generated_canvas_schema_reconstruction",
        "render_mode_svg": False,
        "render_priority_mode": payload.get("render_priority_mode"),
        "source_image_present": bool(payload.get("source_image_present")),
        "has_generated_svg": "recognition-generated-svg" in html,
        "has_canvas_reconstruction_mode": 'data-priority-mode="structured_path_reconstruction"' in html,
        "has_fidelity_banner": "Structured-path mode" in html,
        "has_reconstruction_canvas": "recognition-reconstruction-canvas" in html,
        "has_reconstruction_scene": '"reconstruction_scene"' in html and '"layers"' in html,
        "has_phase_source_image": "phase-source-image" in html,
        "has_data_url": "data:image/" in html,
        "html_length": float(len(html)),
    }


class RecognitionReconstructionTests(unittest.TestCase):
    def test_schema_builder_backfills_controls_and_warnings(self) -> None:
        result = RecognitionResult(
            system="Al-Zn",
            diagram_type="binary",
            x_axis=AxisSpec(label="Mole fraction Zn", minimum=0.0, maximum=1.0, unit=""),
            y_axis=AxisSpec(label="Temperature", minimum=300.0, maximum=1000.0, unit="K"),
            plot_region={"left": 0.12, "top": 0.10, "right": 0.9, "bottom": 0.86, "confidence": 0.88},
            phases=["LIQUID", "FCC_A1", "HCP_A3"],
            confidence=0.68,
            source="llm_recognition_agent",
            raw_summary="识别到 Al-Zn 二元相图。",
        )

        schema = build_reconstruction_schema(result, request_message="请识别这张图。")

        self.assertEqual(schema.system, "Al-Zn")
        self.assertEqual(schema.controls.temperature_min, 300.0)
        self.assertEqual(schema.controls.temperature_max, 1000.0)
        self.assertEqual(len(schema.critical_points), 1)
        self.assertAlmostEqual(schema.plot_region.left or 0, 0.12, places=3)
        self.assertGreater(schema.overlay_confidence, 0.3)
        self.assertIn("recognized reconstruction", " ".join(schema.warnings).lower())
        self.assertIn("deterministic reconstruction code", " ".join(schema.notes).lower())
        self.assertIn("html/canvas", " ".join(schema.notes).lower())

    def test_schema_builder_prefers_recognized_critical_point_temperature_for_slider_default(self) -> None:
        result = RecognitionResult(
            system="Pb-Sn",
            diagram_type="binary",
            x_axis=AxisSpec(label="Mass % Sn", minimum=0.0, maximum=100.0, unit="wt%"),
            y_axis=AxisSpec(label="Temperature", minimum=0.0, maximum=350.0, unit="°C"),
            phases=["LIQUID", "(Sn)", "(Pb)"],
            plot_region={"left": 0.15, "top": 0.12, "right": 0.87, "bottom": 0.83, "confidence": 0.9},
            critical_points=[CriticalPoint(label="eutectic", composition=61.9, temperature=183.0, x_norm=0.62, y_norm=0.47, notes="recognized")],
            confidence=0.95,
            source="llm_recognition_agent",
            raw_summary="识别到 Pb-Sn 共晶图。",
        )

        schema = build_reconstruction_schema(result, request_message="请识别这张图。")

        self.assertAlmostEqual(schema.controls.temperature_default, 183.0, places=3)
        self.assertGreater(schema.controls.temperature_step, 0.0)

    def test_curve_fit_outputs_bounded_geometry(self) -> None:
        result = RecognitionResult(
            system="Pb-Sn",
            diagram_type="binary",
            x_axis=AxisSpec(label="Mass % Pb", minimum=0.0, maximum=100.0, unit="wt%"),
            y_axis=AxisSpec(label="Temperature", minimum=0.0, maximum=350.0, unit="°C"),
            phases=["LIQUID", "(Sn)", "(Pb)"],
            plot_region={"left": 0.15, "top": 0.12, "right": 0.87, "bottom": 0.83, "confidence": 0.9},
            critical_points=[CriticalPoint(label="eutectic", composition=38.1, temperature=183.0, x_norm=0.43, y_norm=0.47, notes="recognized")],
            confidence=0.95,
            source="llm_recognition_agent",
            raw_summary="识别到 Pb-Sn 共晶图。",
        )

        schema = build_reconstruction_schema(result, request_message="请识别这张图。")
        geometry = fit_reconstruction_geometry(schema)

        self.assertGreater(geometry.base_cp_x, geometry.x_min)
        self.assertLess(geometry.base_cp_x, geometry.x_max)
        self.assertGreater(geometry.base_cp_y, geometry.y_min)
        self.assertLess(geometry.base_cp_y, geometry.y_max)
        self.assertEqual(geometry.critical_point_label, "eutectic")
        self.assertGreaterEqual(geometry.base_cp_x_ratio, geometry.plot_left_ratio)
        self.assertLessEqual(geometry.base_cp_x_ratio, geometry.plot_right_ratio)
        self.assertGreaterEqual(geometry.base_cp_y_ratio, geometry.plot_top_ratio)
        self.assertLessEqual(geometry.base_cp_y_ratio, geometry.plot_bottom_ratio)

    def test_schema_builder_uses_image_fallback_plot_region_when_llm_bounds_missing(self) -> None:
        result = RecognitionResult(
            system="Al-Zn",
            diagram_type="binary",
            x_axis=AxisSpec(label="Mole fraction Zn", minimum=0.0, maximum=1.0, unit=""),
            y_axis=AxisSpec(label="Temperature", minimum=300.0, maximum=1000.0, unit="K"),
            phases=["LIQUID", "FCC_A1", "HCP_A3"],
            critical_points=[CriticalPoint(label="eutectic", composition=0.42, temperature=640.0, notes="recognized")],
            confidence=0.79,
            source="llm_recognition_agent",
            raw_summary="识别到 Al-Zn 二元相图。",
        )

        schema = build_reconstruction_schema(
            result,
            request_message="请识别这张图。",
            source_image_data_url=MINI_PNG_DATA_URL,
        )

        self.assertIsNotNone(schema.plot_region.left)
        self.assertIsNotNone(schema.plot_region.right)
        self.assertTrue((schema.plot_region.source or "").endswith("fallback") or schema.plot_region.source == "validator_default")
        self.assertGreater(schema.overlay_confidence, 0.2)

    def test_renderer_outputs_generated_canvas_vector_reconstruction_when_source_image_exists(self) -> None:
        result = RecognitionResult(
            system="Fe-Ni",
            diagram_type="binary",
            x_axis=AxisSpec(label="Ni concentration", minimum=0.0, maximum=20.0, unit="at %"),
            y_axis=AxisSpec(label="Temperature", minimum=6100.0, maximum=6800.0, unit="K"),
            plot_region={"left": 0.16, "top": 0.11, "right": 0.89, "bottom": 0.84, "confidence": 0.91},
            phases=["LIQUID", "BCC", "HCP"],
            critical_points=[CriticalPoint(label="scenario", composition=10.0, temperature=6450.0, x_norm=0.51, y_norm=0.43, notes="recognized")],
            confidence=0.95,
            source="llm_recognition_agent",
            raw_summary="识别到 Fe-Ni 高压相图。",
        )
        schema = build_reconstruction_schema(result, request_message="请识别这张图。")
        geometry = fit_reconstruction_geometry(schema)
        profile = ResultProfile(
            category="Recognized Simulation",
            source_label="recognized phase-diagram image",
            mode_label="interactive simulator",
            trust_level="medium",
            confidence=0.95,
            trust_statement="structured deterministic reconstruction",
            evidence=["schema -> validator -> curve fitting -> renderer"],
        )

        html = render_reconstruction_html(schema, geometry, profile, source_image_data_url=MINI_PNG_DATA_URL)

        self.assertIn("recognition-simulator-data", html)
        self.assertIn("geometry", html)
        self.assertIn("schema", html)
        self.assertIn("recognition-reconstruction-canvas", html)
        self.assertIn("generated_canvas_vector_reconstruction", html)
        self.assertIn("structured_path_reconstruction", html)
        self.assertIn("reconstruction_scene", html)
        self.assertIn('"layers"', html)
        self.assertNotIn("recognition-generated-svg", html)
        self.assertNotIn("phase-source-image", html)
        self.assertNotIn("pixels_rgba_b64", html)
        self.assertNotIn("source_canvas", html)
        self.assertNotIn(MINI_PNG_DATA_URL, html)
        self.assertIn("Structured-path mode", html)

    def test_curve_fit_extracts_major_contours_from_external_phase_diagram_asset(self) -> None:
        asset_path = (
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "assets"
            / "external_phase_diagrams"
            / "al_ni_pmc_phase_diagram.jpg"
        )
        result = RecognitionResult(
            system="Al-Ni",
            diagram_type="binary",
            x_axis=AxisSpec(label="Atomic fraction Ni", minimum=0.0, maximum=1.0, unit=""),
            y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=2200.0, unit="°C"),
            plot_region={"left": 0.16, "top": 0.07, "right": 0.98, "bottom": 0.95, "confidence": 0.9},
            phases=["LIQUID", "NiAl3", "Ni2Al3", "NiAl", "Al"],
            critical_points=[
                CriticalPoint(
                    label="invariant",
                    composition=0.74,
                    temperature=1369.0,
                    x_norm=0.75,
                    y_norm=0.43,
                    notes="benchmark",
                )
            ],
            confidence=0.9,
            source="llm_recognition_agent",
            raw_summary="识别到 Al-Ni 二元相图。",
        )
        schema = build_reconstruction_schema(
            result,
            request_message="请识别这张图。",
            source_image_data_url=_image_data_url_from_path(asset_path),
        )
        geometry = fit_reconstruction_geometry(schema, source_image_data_url=_image_data_url_from_path(asset_path))

        self.assertTrue(geometry.traced_from_image)
        self.assertGreaterEqual(len(geometry.traced_contours), 4)
        self.assertGreaterEqual(max(len(trace) for trace in geometry.traced_contours), 10)
        self.assertGreater(geometry.traced_confidence, 0.5)

    def test_external_phase_diagram_reconstruction_accuracy_regression(self) -> None:
        asset_root = Path(__file__).resolve().parents[1] / "benchmarks" / "assets" / "external_phase_diagrams"
        cases = [
            {
                "name": "Al-Ni",
                "asset_path": asset_root / "al_ni_pmc_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Al-Ni",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Atomic fraction Ni", minimum=0.0, maximum=1.0, unit=""),
                    y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=2200.0, unit="°C"),
                    plot_region={"left": 0.16, "top": 0.05, "right": 0.98, "bottom": 0.95, "confidence": 0.9},
                    phases=["L", "LL2", "AL", "NIAL3", "NI2AL3", "NI3AL4", "NI5AL3"],
                    critical_points=[
                        CriticalPoint(
                            label="eutectic",
                            composition=0.74,
                            temperature=1369.0,
                            x_norm=0.75,
                            y_norm=0.43,
                            notes="benchmark",
                        )
                    ],
                    confidence=0.9,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Al-Ni 二元相图。",
                ),
                "minimum_precision": 0.46,
                "maximum_legend_fraction": 0.12,
                "maximum_bottom_fraction": 0.11,
                "minimum_major_span": 0.42,
                "minimum_contours": 4,
                "minimum_confidence": 0.45,
            },
            {
                "name": "Al-Cu",
                "asset_path": asset_root / "al_cu_pmc_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Al-Cu",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Atomic percent Cu", minimum=0.0, maximum=100.0, unit="at.%"),
                    y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=1200.0, unit="°C"),
                    plot_region={"left": 0.10, "top": 0.02, "right": 0.96, "bottom": 0.93, "confidence": 0.92},
                    phases=["LIQUID", "AL2CU", "AL4CU9", "HT_BCC"],
                    critical_points=[
                        CriticalPoint(
                            label="peritectic",
                            composition=67.0,
                            temperature=1020.0,
                            x_norm=0.68,
                            y_norm=0.19,
                            notes="benchmark",
                        )
                    ],
                    confidence=0.92,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Al-Cu 二元相图。",
                ),
                "minimum_precision": 0.44,
                "maximum_legend_fraction": 0.15,
                "maximum_bottom_fraction": 0.10,
                "minimum_major_span": 0.34,
                "minimum_contours": 5,
                "minimum_confidence": 0.45,
            },
            {
                "name": "Pb-Sn",
                "asset_path": asset_root / "pb_sn_nist_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Pb-Sn",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Mass % Pb", minimum=0.0, maximum=100.0, unit="wt%"),
                    y_axis=AxisSpec(label="Temperature", minimum=0.0, maximum=350.0, unit="°C"),
                    plot_region={"left": 0.11, "top": 0.02, "right": 0.97, "bottom": 0.94, "confidence": 0.94},
                    phases=["LIQUID", "(Sn)", "(Pb)"],
                    critical_points=[
                        CriticalPoint(
                            label="eutectic",
                            composition=38.1,
                            temperature=183.0,
                            x_norm=0.44,
                            y_norm=0.46,
                            notes="benchmark",
                        )
                    ],
                    confidence=0.94,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Pb-Sn 二元相图。",
                ),
                "minimum_precision": 0.50,
                "maximum_legend_fraction": 0.07,
                "maximum_bottom_fraction": 0.08,
                "minimum_major_span": 0.55,
                "minimum_contours": 2,
                "minimum_confidence": 0.42,
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                metrics = _trace_quality_metrics(case["result"], case["asset_path"])
                self.assertGreaterEqual(metrics["precision"], case["minimum_precision"], metrics)
                self.assertLessEqual(metrics["legend_fraction"], case["maximum_legend_fraction"], metrics)
                self.assertLessEqual(metrics["bottom_fraction"], case["maximum_bottom_fraction"], metrics)
                self.assertGreaterEqual(metrics["major_span"], case["minimum_major_span"], metrics)
                self.assertGreaterEqual(metrics["contour_count"], case["minimum_contours"], metrics)
                self.assertGreaterEqual(metrics["confidence"], case["minimum_confidence"], metrics)

    def test_external_phase_diagram_canvas_similarity_regression(self) -> None:
        asset_root = Path(__file__).resolve().parents[1] / "benchmarks" / "assets" / "external_phase_diagrams"
        cases = [
            {
                "name": "Al-Ni",
                "asset_path": asset_root / "al_ni_pmc_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Al-Ni",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Atomic fraction Ni", minimum=0.0, maximum=1.0, unit=""),
                    y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=2200.0, unit="°C"),
                    plot_region={"left": 0.16, "top": 0.05, "right": 0.98, "bottom": 0.95, "confidence": 0.9},
                    phases=["L", "LL2", "AL", "NIAL3"],
                    critical_points=[CriticalPoint(label="eutectic", composition=0.74, temperature=1369.0, x_norm=0.75, y_norm=0.43, notes="benchmark")],
                    confidence=0.9,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Al-Ni 二元相图。",
                ),
                "minimum_similarity": 0.997,
            },
            {
                "name": "Al-Cu",
                "asset_path": asset_root / "al_cu_pmc_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Al-Cu",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Atomic percent Cu", minimum=0.0, maximum=100.0, unit="at.%"),
                    y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=1200.0, unit="°C"),
                    plot_region={"left": 0.10, "top": 0.02, "right": 0.96, "bottom": 0.93, "confidence": 0.92},
                    phases=["LIQUID", "AL2CU", "AL4CU9", "HT_BCC"],
                    critical_points=[CriticalPoint(label="peritectic", composition=67.0, temperature=1020.0, x_norm=0.68, y_norm=0.19, notes="benchmark")],
                    confidence=0.92,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Al-Cu 二元相图。",
                ),
                "minimum_similarity": 0.990,
            },
            {
                "name": "Pb-Sn",
                "asset_path": asset_root / "pb_sn_nist_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Pb-Sn",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Mass % Pb", minimum=0.0, maximum=100.0, unit="wt%"),
                    y_axis=AxisSpec(label="Temperature", minimum=0.0, maximum=350.0, unit="°C"),
                    plot_region={"left": 0.11, "top": 0.02, "right": 0.97, "bottom": 0.94, "confidence": 0.94},
                    phases=["LIQUID", "(Sn)", "(Pb)"],
                    critical_points=[CriticalPoint(label="eutectic", composition=38.1, temperature=183.0, x_norm=0.44, y_norm=0.46, notes="benchmark")],
                    confidence=0.94,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Pb-Sn 二元相图。",
                ),
                "minimum_similarity": 0.999,
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                metrics = _canvas_similarity_metrics(case["result"], case["asset_path"])
                self.assertGreaterEqual(metrics["similarity"], case["minimum_similarity"], metrics)
                self.assertGreater(metrics["width"], 200.0, metrics)
                self.assertGreater(metrics["height"], 200.0, metrics)

    def test_external_phase_diagram_generated_canvas_render_regression(self) -> None:
        asset_root = Path(__file__).resolve().parents[1] / "benchmarks" / "assets" / "external_phase_diagrams"
        cases = [
            {
                "name": "Al-Ni",
                "asset_path": asset_root / "al_ni_pmc_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Al-Ni",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Atomic fraction Ni", minimum=0.0, maximum=1.0, unit=""),
                    y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=2200.0, unit="°C"),
                    plot_region={"left": 0.16, "top": 0.05, "right": 0.98, "bottom": 0.95, "confidence": 0.9},
                    phases=["L", "LL2", "AL", "NIAL3"],
                    critical_points=[CriticalPoint(label="eutectic", composition=0.74, temperature=1369.0, x_norm=0.75, y_norm=0.43, notes="benchmark")],
                    confidence=0.9,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Al-Ni 二元相图。",
                ),
            },
            {
                "name": "Al-Cu",
                "asset_path": asset_root / "al_cu_pmc_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Al-Cu",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Atomic percent Cu", minimum=0.0, maximum=100.0, unit="at.%"),
                    y_axis=AxisSpec(label="Temperature", minimum=200.0, maximum=1200.0, unit="°C"),
                    plot_region={"left": 0.10, "top": 0.02, "right": 0.96, "bottom": 0.93, "confidence": 0.92},
                    phases=["LIQUID", "AL2CU", "AL4CU9", "HT_BCC"],
                    critical_points=[CriticalPoint(label="peritectic", composition=67.0, temperature=1020.0, x_norm=0.68, y_norm=0.19, notes="benchmark")],
                    confidence=0.92,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Al-Cu 二元相图。",
                ),
            },
            {
                "name": "Pb-Sn",
                "asset_path": asset_root / "pb_sn_nist_phase_diagram.jpg",
                "result": RecognitionResult(
                    system="Pb-Sn",
                    diagram_type="binary",
                    x_axis=AxisSpec(label="Mass % Pb", minimum=0.0, maximum=100.0, unit="wt%"),
                    y_axis=AxisSpec(label="Temperature", minimum=0.0, maximum=350.0, unit="°C"),
                    plot_region={"left": 0.11, "top": 0.02, "right": 0.97, "bottom": 0.94, "confidence": 0.94},
                    phases=["LIQUID", "(Sn)", "(Pb)"],
                    critical_points=[CriticalPoint(label="eutectic", composition=38.1, temperature=183.0, x_norm=0.44, y_norm=0.46, notes="benchmark")],
                    confidence=0.94,
                    source="llm_recognition_agent",
                    raw_summary="识别到 Pb-Sn 二元相图。",
                ),
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                metrics = _render_output_structure_metrics(case["result"], case["asset_path"])
                self.assertTrue(metrics["render_mode_vector_canvas"], metrics)
                self.assertFalse(metrics["render_mode_svg"], metrics)
                self.assertEqual(metrics["render_priority_mode"], "structured_path_reconstruction", metrics)
                self.assertTrue(metrics["source_image_present"], metrics)
                self.assertFalse(metrics["has_generated_svg"], metrics)
                self.assertTrue(metrics["has_canvas_reconstruction_mode"], metrics)
                self.assertTrue(metrics["has_fidelity_banner"], metrics)
                self.assertTrue(metrics["has_reconstruction_canvas"], metrics)
                self.assertTrue(metrics["has_reconstruction_scene"], metrics)
                self.assertFalse(metrics["has_phase_source_image"], metrics)
                self.assertFalse(metrics["has_data_url"], metrics)
                self.assertGreater(metrics["html_length"], 12000.0, metrics)


if __name__ == "__main__":
    unittest.main()
