from __future__ import annotations

from app.recognition_reconstruction.schema import ReconstructionGeometry, ReconstructionSchema
from app.recognition_reconstruction.vector_trace import trace_phase_boundaries_from_image


def fit_reconstruction_geometry(
    schema: ReconstructionSchema,
    *,
    source_image_data_url: str | None = None,
) -> ReconstructionGeometry:
    x_axis = schema.x_axis
    y_axis = schema.y_axis
    assert x_axis.minimum is not None and x_axis.maximum is not None
    assert y_axis.minimum is not None and y_axis.maximum is not None
    span_x = max(x_axis.maximum - x_axis.minimum, 1e-6)
    span_y = max(y_axis.maximum - y_axis.minimum, 1e-6)
    primary = schema.critical_points[0]
    base_cp_x = primary.composition if primary.composition is not None else x_axis.minimum + span_x * 0.48
    base_cp_y = primary.temperature if primary.temperature is not None else schema.controls.temperature_default
    base_cp_x = max(x_axis.minimum + span_x * 0.08, min(base_cp_x, x_axis.maximum - span_x * 0.08))
    base_cp_y = max(y_axis.minimum + span_y * 0.12, min(base_cp_y, y_axis.maximum - span_y * 0.08))
    plot_left_ratio = schema.plot_region.left or 0.14
    plot_top_ratio = schema.plot_region.top or 0.12
    plot_right_ratio = schema.plot_region.right or 0.89
    plot_bottom_ratio = schema.plot_region.bottom or 0.84
    if primary.x_norm is not None and primary.y_norm is not None:
        base_cp_x_ratio = max(plot_left_ratio, min(primary.x_norm, plot_right_ratio))
        base_cp_y_ratio = max(plot_top_ratio, min(primary.y_norm, plot_bottom_ratio))
    else:
        base_cp_x_ratio = plot_left_ratio + ((base_cp_x - x_axis.minimum) / span_x) * (plot_right_ratio - plot_left_ratio)
        base_cp_y_ratio = plot_bottom_ratio - ((base_cp_y - y_axis.minimum) / span_y) * (plot_bottom_ratio - plot_top_ratio)
        base_cp_x_ratio = max(plot_left_ratio, min(base_cp_x_ratio, plot_right_ratio))
        base_cp_y_ratio = max(plot_top_ratio, min(base_cp_y_ratio, plot_bottom_ratio))
    left_edge_x = x_axis.minimum + span_x * 0.02
    right_edge_x = x_axis.maximum - span_x * 0.02
    left_shoulder_x = base_cp_x - span_x * 0.22
    right_shoulder_x = base_cp_x + span_x * 0.22
    left_peak_temp = min(y_axis.maximum, base_cp_y + span_y * 0.24)
    right_peak_temp = min(y_axis.maximum, base_cp_y + span_y * 0.21)
    secondary_left_x = x_axis.minimum + span_x * 0.18
    secondary_right_x = x_axis.maximum - span_x * 0.16
    secondary_dip_temp = max(y_axis.minimum + span_y * 0.12, base_cp_y - span_y * 0.08)
    geometry = ReconstructionGeometry(
        x_min=x_axis.minimum,
        x_max=x_axis.maximum,
        y_min=y_axis.minimum,
        y_max=y_axis.maximum,
        plot_left_ratio=plot_left_ratio,
        plot_top_ratio=plot_top_ratio,
        plot_right_ratio=plot_right_ratio,
        plot_bottom_ratio=plot_bottom_ratio,
        base_cp_x_ratio=base_cp_x_ratio,
        base_cp_y_ratio=base_cp_y_ratio,
        base_cp_x=base_cp_x,
        base_cp_y=base_cp_y,
        left_edge_x=left_edge_x,
        right_edge_x=right_edge_x,
        left_shoulder_x=left_shoulder_x,
        right_shoulder_x=right_shoulder_x,
        left_peak_temp=left_peak_temp,
        right_peak_temp=right_peak_temp,
        secondary_left_x=secondary_left_x,
        secondary_right_x=secondary_right_x,
        secondary_dip_temp=secondary_dip_temp,
        liquid_label="Liquid" if any("liq" in phase.lower() for phase in schema.phases) else schema.phases[0],
        left_solid_label=schema.primary_phase_label,
        right_solid_label=schema.secondary_phase_label,
        critical_point_label=primary.label or "critical point",
    )
    traced = trace_phase_boundaries_from_image(schema, geometry, image_data_url=source_image_data_url)
    if traced.traced_from_image:
        if traced.plot_left_ratio is not None:
            geometry.plot_left_ratio = traced.plot_left_ratio
        if traced.plot_top_ratio is not None:
            geometry.plot_top_ratio = traced.plot_top_ratio
        if traced.plot_right_ratio is not None:
            geometry.plot_right_ratio = traced.plot_right_ratio
        if traced.plot_bottom_ratio is not None:
            geometry.plot_bottom_ratio = traced.plot_bottom_ratio
        if traced.anchor_plot_x is not None and traced.anchor_plot_y is not None:
            geometry.base_cp_x_ratio = max(
                geometry.plot_left_ratio,
                min(
                    geometry.plot_right_ratio,
                    geometry.plot_left_ratio + traced.anchor_plot_x * (geometry.plot_right_ratio - geometry.plot_left_ratio),
                ),
            )
            geometry.base_cp_y_ratio = max(
                geometry.plot_top_ratio,
                min(
                    geometry.plot_bottom_ratio,
                    geometry.plot_top_ratio + traced.anchor_plot_y * (geometry.plot_bottom_ratio - geometry.plot_top_ratio),
                ),
            )
        geometry.traced_from_image = True
        geometry.traced_confidence = traced.confidence
        geometry.traced_anchor_plot_x = traced.anchor_plot_x
        geometry.traced_anchor_plot_y = traced.anchor_plot_y
        geometry.traced_liquidus_left = traced.liquidus_left
        geometry.traced_liquidus_right = traced.liquidus_right
        geometry.traced_solidus_left = traced.solidus_left
        geometry.traced_solidus_right = traced.solidus_right
        geometry.traced_contours = traced.contours
    return geometry
