from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


DiagramType = Literal["binary", "ternary"]
WorkspaceId = Literal["phase_diagram", "lammps", "generic"]
WorkspaceStatus = Literal["active", "reserved", "disabled"]
InputChannel = Literal["text", "image", "structured", "generic"]
DeliverableKind = Literal["html", "json", "text", "none"]
TaskRouteName = Literal[
    "phase_diagram.generate",
    "phase_diagram.recognize",
    "phase_diagram.redraw_html",
    "phase_diagram.from_image",
    "phase_diagram.repair",
    "lammps.generate",
    "lammps.repair",
    "materials.lookup",
    "materials.compare",
    "materials.analysis",
    "generic.unknown",
]
ToolName = Literal[
    "phase_diagram_result_review",
    "phase_diagram_codegen",
    "phase_diagram_html_redraw",
    "phase_diagram_html_review",
    "phase_diagram_image_parse",
    "phase_diagram_image_render",
    "phase_diagram_repair",
    "python_execute",
    "load_latest_html_artifact",
    "lammps_command_router",
    "lammps_codegen",
    "lammps_execute",
    "lammps_repair",
]
PlanStepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
ArtifactKind = Literal["html", "code", "text", "json"]
AgentStreamEventType = Literal[
    "run_started",
    "step_started",
    "step_completed",
    "step_failed",
    "step_skipped",
    "run_completed",
    "run_error",
]


class DiagramRequest(BaseModel):
    system_name: str = Field(..., min_length=1, description="Material system name, e.g. Fe-C")
    diagram_type: DiagramType = Field(default="binary")
    temperature_min: float = Field(default=300.0, description="Minimum temperature in K")
    temperature_max: float = Field(default=1800.0, description="Maximum temperature in K")
    pressure: float = Field(default=101325.0, description="Pressure in Pa")
    step_size: float = Field(default=50.0, gt=0, description="Sampling step size")
    notes: str = Field(default="", description="Additional user notes")

    @model_validator(mode="after")
    def validate_temperature_range(self) -> "DiagramRequest":
        if self.temperature_max <= self.temperature_min:
            raise ValueError("temperature_max must be greater than temperature_min")
        return self


class AxisCalibration(BaseModel):
    label: str = Field(..., min_length=1)
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def validate_axis_range(self) -> "AxisCalibration":
        if self.maximum <= self.minimum:
            raise ValueError("maximum must be greater than minimum")
        return self


class ImageDiagramRequest(BaseModel):
    image_data_url: str = Field(..., min_length=20, description="Browser data URL for the uploaded phase-diagram screenshot")
    filename: str = Field(default="")
    system_name: str = Field(default="")
    chart_title: str = Field(default="")
    diagram_type: DiagramType = Field(default="binary")
    x_axis: AxisCalibration
    y_axis: AxisCalibration
    notes: str = Field(default="")

    @model_validator(mode="after")
    def validate_image_data_url(self) -> "ImageDiagramRequest":
        if not self.image_data_url.startswith("data:image/"):
            raise ValueError("image_data_url must be a data:image/* data URL")
        return self


class ImageDiagramLabel(BaseModel):
    text: str = Field(..., min_length=1)
    x: float
    y: float


class ImageDiagramBoundary(BaseModel):
    name: str = Field(..., min_length=1)
    points: list[list[float]] = Field(default_factory=list)


class ImageDiagramSpec(BaseModel):
    chart_title: str
    system_name: str = ""
    filename: str = ""
    diagram_type: DiagramType = "binary"
    source_image_data_url: str
    x_axis: AxisCalibration
    y_axis: AxisCalibration
    detection_mode: Literal["manual_calibrated", "vision_augmented"] = "manual_calibrated"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    notes: list[str] = Field(default_factory=list)
    labels: list[ImageDiagramLabel] = Field(default_factory=list)
    boundaries: list[ImageDiagramBoundary] = Field(default_factory=list)


class HtmlRedrawRequest(BaseModel):
    message: str = Field(..., min_length=1)
    system_name: str = Field(default="")
    chart_title: str = Field(default="")
    diagram_type: DiagramType = Field(default="binary")
    notes: str = Field(default="")
    image_data_url: Optional[str] = None
    filename: str = Field(default="")

    @model_validator(mode="after")
    def validate_redraw_request(self) -> "HtmlRedrawRequest":
        if self.image_data_url and not self.image_data_url.startswith("data:image/"):
            raise ValueError("image_data_url must be a data:image/* data URL")
        return self


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    workspace_hint: Optional[WorkspaceId] = None
    system_name: str = Field(default="")
    chart_title: str = Field(default="")
    diagram_type: DiagramType = Field(default="binary")
    temperature_min: float = Field(default=300.0)
    temperature_max: float = Field(default=1800.0)
    pressure: float = Field(default=101325.0)
    step_size: float = Field(default=50.0, gt=0)
    notes: str = Field(default="")
    image_data_url: Optional[str] = None
    filename: str = Field(default="")
    x_axis: Optional[AxisCalibration] = None
    y_axis: Optional[AxisCalibration] = None

    @model_validator(mode="after")
    def validate_chat_request(self) -> "AgentChatRequest":
        if self.temperature_max <= self.temperature_min:
            raise ValueError("temperature_max must be greater than temperature_min")
        if self.image_data_url and not self.image_data_url.startswith("data:image/"):
            raise ValueError("image_data_url must be a data:image/* data URL")
        return self


class GenerateResponse(BaseModel):
    success: bool = True
    prompt: str
    generated_code: str


class WorkspaceSummary(BaseModel):
    id: WorkspaceId
    title: str
    description: str
    status: WorkspaceStatus = "active"
    available_tools: list[str] = Field(default_factory=list)
    reserved_tools: list[str] = Field(default_factory=list)
    default_route: Optional[TaskRouteName] = None
    supported_routes: list[TaskRouteName] = Field(default_factory=list)


class ToolCatalogEntry(BaseModel):
    name: str
    description: str
    workspace_id: WorkspaceId = "generic"
    status: WorkspaceStatus = "active"
    supports_routes: list[TaskRouteName] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    produces_artifacts: list[ArtifactKind] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)


class AgentCatalogResponse(BaseModel):
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)
    tools: list[ToolCatalogEntry] = Field(default_factory=list)
    supported_routes: list[TaskRouteName] = Field(default_factory=list)


class RouteBlueprintStep(BaseModel):
    tool_name: ToolName
    stage: str = ""
    description: str = ""
    retryable: bool = False
    input_overrides: dict[str, Any] = Field(default_factory=dict)


class RouteBlueprint(BaseModel):
    name: TaskRouteName
    workspace_id: WorkspaceId = "generic"
    entry_tool: Optional[str] = None
    description: str
    default_reason: str
    input_channels: list[InputChannel] = Field(default_factory=list)
    deliverable: DeliverableKind = "none"
    available_tools: list[str] = Field(default_factory=list)
    reserved_tools: list[str] = Field(default_factory=list)
    failure_strategy: str = ""
    sample_prompts: list[str] = Field(default_factory=list)
    steps: list[RouteBlueprintStep] = Field(default_factory=list)


class AgentManifestResponse(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)
    routes: list[RouteBlueprint] = Field(default_factory=list)
    tools: list[ToolCatalogEntry] = Field(default_factory=list)


class RunCodeRequest(BaseModel):
    code: str = Field(..., min_length=1)


class ExecutionResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    html_content: Optional[str] = None
    html_path: Optional[str] = None


class GenerateAndRunResponse(ExecutionResult):
    generated_code: str
    prompt: str
    run_id: Optional[str] = None
    route: Optional[TaskRouteName] = None
    route_reason: Optional[str] = None
    workspace_id: Optional[WorkspaceId] = None
    selected_tool: Optional[str] = None
    entry_tool: Optional[str] = None
    available_tools: list[str] = Field(default_factory=list)
    reserved_tools: list[str] = Field(default_factory=list)
    input_channels: list[InputChannel] = Field(default_factory=list)
    deliverable: DeliverableKind = "none"
    narrative: str = ""
    plan_steps: list["PlanStep"] = Field(default_factory=list)
    trace: list["ToolObservation"] = Field(default_factory=list)
    termination_reason: str = ""


class HealthResponse(BaseModel):
    status: str
    app_name: str
    version: str


class AgentRunRequest(BaseModel):
    user_input: str = Field(..., min_length=1)
    task_type_hint: Optional[str] = None
    workspace_hint: Optional[WorkspaceId] = None
    diagram_request: Optional[DiagramRequest] = None
    image_diagram_request: Optional[ImageDiagramRequest] = None
    html_redraw_request: Optional[HtmlRedrawRequest] = None
    context: dict[str, Any] = Field(default_factory=dict)


class TaskRoute(BaseModel):
    name: TaskRouteName
    workspace_id: WorkspaceId = "generic"
    reason: str
    selected_tool: Optional[str] = None
    available_tools: list[str] = Field(default_factory=list)
    reserved_tools: list[str] = Field(default_factory=list)
    entry_tool: Optional[str] = None
    input_channels: list[InputChannel] = Field(default_factory=list)
    deliverable: DeliverableKind = "none"
    narrative: str = ""
    intent: str = ""
    decision_source: str = ""
    decision_confidence: float | None = None


class PlanStep(BaseModel):
    index: int
    tool_name: ToolName
    input: dict[str, Any] = Field(default_factory=dict)
    status: PlanStepStatus = "pending"
    retryable: bool = False
    description: str = ""
    stage: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRef(BaseModel):
    kind: ArtifactKind
    name: str
    path: Optional[str] = None
    content: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolObservation(BaseModel):
    step_index: int
    tool_name: ToolName
    success: bool
    summary: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    state_delta: dict[str, Any] = Field(default_factory=dict)


class RunTrace(BaseModel):
    run_id: str
    route: TaskRoute
    steps: list[PlanStep] = Field(default_factory=list)
    observations: list[ToolObservation] = Field(default_factory=list)
    termination_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    success: bool
    run_id: str
    route: TaskRoute
    final_message: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    plan_steps: list[PlanStep] = Field(default_factory=list)
    trace: list[ToolObservation] = Field(default_factory=list)
    generated_code: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    html_content: Optional[str] = None
    html_path: Optional[str] = None
    termination_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentStreamEvent(BaseModel):
    type: AgentStreamEventType
    run_id: str
    emitted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)
