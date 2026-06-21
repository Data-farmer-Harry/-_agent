from __future__ import annotations

from pydantic import BaseModel, Field

from app.state import AxisSpec, CriticalPoint, PlotRegionHint


class ReconstructionControlSpec(BaseModel):
    temperature_min: float
    temperature_max: float
    temperature_default: float
    temperature_step: float = 1.0
    pressure_min: float
    pressure_max: float
    pressure_default: float
    pressure_step: float = 0.01
    pressure_mode: str = "qualitative_factor"


class ReconstructionSchema(BaseModel):
    system: str = ""
    diagram_type: str = "binary"
    x_axis: AxisSpec = Field(default_factory=AxisSpec)
    y_axis: AxisSpec = Field(default_factory=AxisSpec)
    plot_region: PlotRegionHint = Field(default_factory=PlotRegionHint)
    phases: list[str] = Field(default_factory=list)
    critical_points: list[CriticalPoint] = Field(default_factory=list)
    controls: ReconstructionControlSpec
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    raw_summary: str = ""
    request_message: str = ""
    confidence: float = 0.0
    overlay_confidence: float = 0.0
    canonical_system: str = ""
    primary_phase_label: str = ""
    secondary_phase_label: str = ""


class ReconstructionGeometry(BaseModel):
    svg_width: int = 920
    svg_height: int = 620
    margin_left: float = 120.0
    margin_top: float = 80.0
    chart_width: float = 680.0
    chart_height: float = 420.0
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    plot_left_ratio: float = 0.14
    plot_top_ratio: float = 0.12
    plot_right_ratio: float = 0.89
    plot_bottom_ratio: float = 0.84
    base_cp_x_ratio: float = 0.50
    base_cp_y_ratio: float = 0.45
    base_cp_x: float
    base_cp_y: float
    left_edge_x: float
    right_edge_x: float
    left_shoulder_x: float
    right_shoulder_x: float
    left_peak_temp: float
    right_peak_temp: float
    secondary_left_x: float
    secondary_right_x: float
    secondary_dip_temp: float
    liquid_label: str = "Liquid"
    left_solid_label: str = ""
    right_solid_label: str = ""
    critical_point_label: str = "critical point"
    traced_from_image: bool = False
    traced_confidence: float = 0.0
    traced_anchor_plot_x: float | None = None
    traced_anchor_plot_y: float | None = None
    traced_liquidus_left: list[list[float]] = Field(default_factory=list)
    traced_liquidus_right: list[list[float]] = Field(default_factory=list)
    traced_solidus_left: list[list[float]] = Field(default_factory=list)
    traced_solidus_right: list[list[float]] = Field(default_factory=list)
    traced_contours: list[list[list[float]]] = Field(default_factory=list)
