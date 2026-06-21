from __future__ import annotations

from app.recognition_reconstruction.image_analysis import estimate_plot_region_from_image_data_url
from app.recognition_reconstruction.schema import ReconstructionControlSpec, ReconstructionSchema
from app.state import AxisSpec, CriticalPoint, PlotRegionHint, RecognitionResult


def _normalized_axis(axis: AxisSpec, fallback_label: str, fallback_min: float, fallback_max: float, fallback_unit: str) -> AxisSpec:
    minimum = axis.minimum if axis.minimum is not None else fallback_min
    maximum = axis.maximum if axis.maximum is not None else fallback_max
    if maximum <= minimum:
        minimum, maximum = fallback_min, fallback_max
    return AxisSpec(
        label=axis.label or fallback_label,
        minimum=minimum,
        maximum=maximum,
        unit=axis.unit or fallback_unit,
    )


def _default_critical_point(x_axis: AxisSpec, y_axis: AxisSpec) -> CriticalPoint:
    assert x_axis.minimum is not None and x_axis.maximum is not None
    assert y_axis.minimum is not None and y_axis.maximum is not None
    return CriticalPoint(
        label="simulated-eutectic",
        composition=(x_axis.minimum + x_axis.maximum) / 2,
        temperature=y_axis.minimum + (y_axis.maximum - y_axis.minimum) * 0.55,
        notes="Backfilled from the recognized diagram layout because no explicit critical point was extracted.",
    )


def _build_controls(recognition_result: RecognitionResult, y_axis: AxisSpec) -> ReconstructionControlSpec:
    assert y_axis.minimum is not None and y_axis.maximum is not None
    span = max(y_axis.maximum - y_axis.minimum, 50.0)
    extracted_temperatures = [
        point.temperature
        for point in recognition_result.critical_points
        if point.temperature is not None and y_axis.minimum <= point.temperature <= y_axis.maximum
    ]
    base_temp = extracted_temperatures[0] if extracted_temperatures else y_axis.minimum + span * 0.55
    if span <= 40:
        temperature_step = 0.1
    elif span <= 200:
        temperature_step = 0.5
    else:
        temperature_step = 1.0
    return ReconstructionControlSpec(
        temperature_min=y_axis.minimum,
        temperature_max=y_axis.maximum,
        temperature_default=base_temp,
        temperature_step=temperature_step,
        pressure_min=0.60,
        pressure_max=1.40,
        pressure_default=1.00,
        pressure_step=0.01,
    )


def _default_plot_region(*, source: str = "validator_default") -> PlotRegionHint:
    return PlotRegionHint(left=0.14, top=0.12, right=0.89, bottom=0.84, confidence=0.40, source=source)


def _normalized_plot_region(region: PlotRegionHint) -> tuple[PlotRegionHint, bool]:
    default = _default_plot_region()
    left = region.left
    top = region.top
    right = region.right
    bottom = region.bottom
    if None in (left, top, right, bottom):
        return default, False
    if right <= left or bottom <= top:
        return default, False
    left = min(max(left, 0.02), 0.70)
    top = min(max(top, 0.02), 0.70)
    right = min(max(right, left + 0.12), 0.98)
    bottom = min(max(bottom, top + 0.12), 0.98)
    return PlotRegionHint(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        confidence=region.confidence,
        source=region.source or "llm_plot_region",
    ), True


def _canonical_system(system: str) -> str:
    text = (system or "").strip()
    if not text:
        return ""
    if "-" in text:
        parts = [part.strip() for part in text.split("-") if part.strip()]
        if len(parts) >= 2:
            return "-".join(parts[:2])
    return text


def _phase_labels(phases: list[str]) -> tuple[str, str]:
    if not phases:
        return ("Solid solution", "Secondary phase")
    non_liquid = [phase for phase in phases if "liq" not in phase.lower()]
    if not non_liquid:
        return ("Primary solid", "Secondary solid")
    left = non_liquid[0]
    right = non_liquid[-1] if len(non_liquid) > 1 else non_liquid[0]
    return left, right


def _overlay_confidence(recognition_result: RecognitionResult, *, has_plot_region: bool) -> float:
    score = max(0.1, min(recognition_result.confidence or 0.0, 1.0))
    if not has_plot_region:
        score -= 0.18
    if recognition_result.x_axis.minimum is None or recognition_result.x_axis.maximum is None:
        score -= 0.10
    if recognition_result.y_axis.minimum is None or recognition_result.y_axis.maximum is None:
        score -= 0.10
    if not recognition_result.critical_points:
        score -= 0.08
    elif not any(point.x_norm is not None and point.y_norm is not None for point in recognition_result.critical_points):
        score -= 0.06
    if len(recognition_result.phases) < 2:
        score -= 0.04
    return max(0.1, min(score, 1.0))


def _resolved_plot_region(recognition_result: RecognitionResult, *, source_image_data_url: str | None) -> tuple[PlotRegionHint, bool, bool]:
    normalized_region, has_llm_region = _normalized_plot_region(recognition_result.plot_region)
    if has_llm_region:
        return normalized_region, True, False

    if source_image_data_url:
        heuristic_region = estimate_plot_region_from_image_data_url(source_image_data_url)
        normalized_heuristic, has_heuristic = _normalized_plot_region(heuristic_region)
        if has_heuristic:
            normalized_heuristic.source = heuristic_region.source or "image_axis_scan_fallback"
            return normalized_heuristic, True, True

    default_region = _default_plot_region()
    return default_region, False, False


def build_reconstruction_schema(
    recognition_result: RecognitionResult,
    *,
    request_message: str,
    source_image_data_url: str | None = None,
) -> ReconstructionSchema:
    x_axis = _normalized_axis(recognition_result.x_axis, "composition", 0.0, 100.0, "at.%")
    y_axis = _normalized_axis(recognition_result.y_axis, "temperature", 300.0, 1200.0, "K")
    plot_region, has_plot_region, used_image_fallback = _resolved_plot_region(
        recognition_result,
        source_image_data_url=source_image_data_url,
    )
    critical_points = recognition_result.critical_points[:] or [_default_critical_point(x_axis, y_axis)]
    overlay_confidence = _overlay_confidence(recognition_result, has_plot_region=has_plot_region)
    warnings = [
        "This panel is a recognized reconstruction, not a fresh thermodynamic equilibrium solve.",
        "Pressure-driven motion is qualitative unless the uploaded figure explicitly encodes pressure dependence.",
    ]
    if not has_plot_region:
        warnings.append("Plot-frame geometry was not recognized confidently, so the overlay uses a conservative fallback chart window.")
    elif used_image_fallback:
        warnings.append("Plot-frame geometry was approximated from the uploaded image using a deterministic axis-scan fallback.")
    if not any(point.x_norm is not None and point.y_norm is not None for point in recognition_result.critical_points):
        warnings.append("Critical-point image anchors are incomplete; marker placement falls back to axis-mapped coordinates.")
    if recognition_result.confidence < 0.75:
        warnings.append("Recognition confidence is limited; treat reconstructed boundary motion as approximate.")
    notes = [
        "The LLM only extracts structured diagram facts; geometry and interaction come from deterministic reconstruction code.",
        "The uploaded image is only used to extract structured diagram facts; the final panel is regenerated as HTML/canvas instead of displaying the source image.",
        "Temperature slider shifts the reconstructed invariant point vertically within the recognized axis bounds.",
        "Pressure factor perturbs the reconstructed boundary curvature as a qualitative exploration aid.",
    ]
    if has_plot_region:
        notes.append("The plotting window is anchored by the recognized plot_region and clamped by validator rules.")
    if used_image_fallback:
        notes.append("Because the model did not return a usable plot_region, the validator inferred the chart window from the uploaded image itself before regenerating the vector view.")
    left_label, right_label = _phase_labels(recognition_result.phases)
    return ReconstructionSchema(
        system=recognition_result.system or "Recognized phase diagram",
        canonical_system=_canonical_system(recognition_result.system),
        diagram_type=recognition_result.diagram_type,
        x_axis=x_axis,
        y_axis=y_axis,
        plot_region=plot_region,
        phases=recognition_result.phases[:] or ["Liquid", "Solid solution"],
        critical_points=critical_points,
        controls=_build_controls(recognition_result, y_axis),
        warnings=warnings,
        notes=notes,
        raw_summary=recognition_result.raw_summary,
        request_message=request_message,
        confidence=recognition_result.confidence,
        overlay_confidence=overlay_confidence,
        primary_phase_label=left_label,
        secondary_phase_label=right_label,
    )
