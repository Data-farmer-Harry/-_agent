from __future__ import annotations

from pydantic import BaseModel, Field

from app.state import ArtifactRef, AxisSpec, CriticalPoint, ResultProfile


class RecognitionSimulatorControlSpec(BaseModel):
    temperature_min: float
    temperature_max: float
    temperature_default: float
    pressure_min: float
    pressure_max: float
    pressure_default: float


class RecognitionSimulationReport(BaseModel):
    system: str = ""
    diagram_type: str = "binary"
    x_axis: AxisSpec = Field(default_factory=AxisSpec)
    y_axis: AxisSpec = Field(default_factory=AxisSpec)
    phases: list[str] = Field(default_factory=list)
    critical_points: list[CriticalPoint] = Field(default_factory=list)
    controls: RecognitionSimulatorControlSpec
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    raw_summary: str = ""
    request_message: str = ""
    reconstruction_schema: dict[str, object] = Field(default_factory=dict)
    geometry_model: dict[str, object] = Field(default_factory=dict)


class RecognitionSimulationBundle(BaseModel):
    html_content: str
    html_path: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    summary: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    result_profile: ResultProfile
    report: RecognitionSimulationReport
