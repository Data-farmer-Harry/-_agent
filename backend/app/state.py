from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.core.agent_protocol import AgentEnvelope


DiagramType = Literal["binary", "ternary"]
ArtifactKind = Literal["html", "code", "text", "json", "image", "video", "markdown", "csv"]
ComputeDomain = Literal["phase_diagram", "lammps", "none"]
PlanStepStatus = Literal["pending", "running", "completed", "failed"]
RunStatus = Literal["draft", "queued", "running", "completed", "failed", "cancelled"]
AgentStreamEventType = Literal[
    "run_started",
    "step_started",
    "step_completed",
    "step_failed",
    "lifecycle_event",
    "dag_event",
    "checkpoint_saved",
    "run_completed",
    "run_error",
]


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", min_length=1, max_length=4000)


class UploadedAsset(BaseModel):
    asset_id: str = ""
    name: str = ""
    media_type: str = "application/octet-stream"
    data_url: str = ""
    size_bytes: int | None = None


class AxisSpec(BaseModel):
    label: str = ""
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""


class PlotRegionHint(BaseModel):
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None
    confidence: float | None = None
    source: str = ""


class CriticalPoint(BaseModel):
    label: str = ""
    composition: float | None = None
    temperature: float | None = None
    notes: str = ""
    x_norm: float | None = None
    y_norm: float | None = None
    confidence: float | None = None


class RecognitionResult(BaseModel):
    system: str = ""
    diagram_type: DiagramType = "binary"
    x_axis: AxisSpec = Field(default_factory=AxisSpec)
    y_axis: AxisSpec = Field(default_factory=AxisSpec)
    plot_region: PlotRegionHint = Field(default_factory=PlotRegionHint)
    phases: list[str] = Field(default_factory=list)
    critical_points: list[CriticalPoint] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source: str = ""
    raw_summary: str = ""


class DiagramRequest(BaseModel):
    system_name: str = Field(..., min_length=1, description="Material system name, e.g. Al-Zn")
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


class LammpsRequest(BaseModel):
    material: str = Field(default="", description="Material symbol such as Cu/Al/Ni")
    potential_family: str = Field(default="eam", description="Potential family such as eam or lj")
    task_type: str = Field(default="equilibration", description="LAMMPS task type")
    temperature: int = Field(default=900, description="Target temperature in K")
    steps: int = Field(default=5000, description="Number of MD steps")
    ensemble: str = Field(default="NVT", description="Simulation ensemble")
    box_size: int = Field(default=4, description="Cubic box repetition size")
    initial_temp: int | None = Field(default=None, description="Initial temperature in K")
    time_step: float = Field(default=0.001, description="Time step in ps")
    dump_file: str = Field(default="dump.atom")
    custom_potential_path: str = Field(default="")
    custom_structure_path: str = Field(default="")
    custom_structure_format: str = Field(default="")
    notes: str = Field(default="")

    @model_validator(mode="after")
    def validate_request(self) -> "LammpsRequest":
        if self.temperature <= 0:
            raise ValueError("temperature must be greater than 0")
        if self.steps <= 0:
            raise ValueError("steps must be greater than 0")
        if self.box_size <= 0:
            raise ValueError("box_size must be greater than 0")
        if self.time_step <= 0:
            raise ValueError("time_step must be greater than 0")
        return self


class LastRunContext(BaseModel):
    run_id: str = ""
    route_name: str = ""
    compute_domain: ComputeDomain = "none"
    system_name: str = ""
    final_message: str = ""
    generated_code_preview: str = ""
    review_summary: str = ""
    selected_tool: str = ""
    generation_source: str = ""
    request_summary: str = ""
    review_passed: bool | None = None
    review_issues: list[str] = Field(default_factory=list)
    review_advisory_issues: list[str] = Field(default_factory=list)
    trace_summary: list[str] = Field(default_factory=list)
    recognition_summary: str = ""
    artifact_names: list[str] = Field(default_factory=list)


class AgentChatRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    conversation_id: str = Field(default="default")
    message: str = Field(..., min_length=1)
    system_name: str = Field(default="")
    diagram_type: DiagramType = Field(default="binary")
    temperature_min: float = Field(default=300.0)
    temperature_max: float = Field(default=1800.0)
    pressure: float = Field(default=101325.0)
    step_size: float = Field(default=50.0, gt=0)
    notes: str = Field(default="")
    uploaded_assets: list[UploadedAsset] = Field(default_factory=list)
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    last_run_context: LastRunContext = Field(default_factory=LastRunContext)

    @model_validator(mode="after")
    def validate_chat_request(self) -> "AgentChatRequest":
        if self.temperature_max <= self.temperature_min:
            raise ValueError("temperature_max must be greater than temperature_min")
        return self


class PromptSuggestionRequest(BaseModel):
    conversation_id: str = Field(default="default")
    draft_message: str = Field(default="", max_length=4000)
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    last_run_context: LastRunContext = Field(default_factory=LastRunContext)
    current_context_summary: str = Field(default="", max_length=4000)


class PromptSuggestionResponse(BaseModel):
    suggested_prompt: str
    rationale: str = ""
    source: str = "llm_prompt_suggester"


class ResultProfile(BaseModel):
    category: str = ""
    source_label: str = ""
    mode_label: str = ""
    trust_level: Literal["high", "medium", "low", "unknown"] = "unknown"
    confidence: float | None = None
    trust_statement: str = ""
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class DiagnosticCheck(BaseModel):
    name: str
    status: Literal["ok", "warning", "error", "unknown"] = "unknown"
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class SystemDiagnosticsResponse(BaseModel):
    generated_at: str
    overall_status: Literal["ok", "warning", "error"] = "warning"
    checks: list[DiagnosticCheck] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    app_name: str
    version: str


class ThermoRegistryEntry(BaseModel):
    system_name: str
    aliases: list[str] = Field(default_factory=list)
    family: str
    format: str
    database_name: str
    database_file: str
    documentation_url: str
    source_url: str
    provenance: str
    components: list[str] = Field(default_factory=list)
    phases: list[str] = Field(default_factory=list)
    x_component: str
    x_axis_label: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    accuracy_reference: dict[str, object] = Field(default_factory=dict)


class ThermoRegistryResponse(BaseModel):
    count: int
    systems: list[ThermoRegistryEntry] = Field(default_factory=list)


class ThermoRagCandidate(BaseModel):
    system_name: str
    score: float
    lexical_score: float = 0.0
    bm25_score: float = 0.0
    vector_score: float = 0.0
    selection_strategy: str = ""
    embedding_backend: str = ""
    match_reasons: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    phases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    database_name: str = ""
    summary: str = ""
    source_url: str = ""


class ThermoRagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=10)


class ThermoRagSearchResponse(BaseModel):
    query: str
    matched: bool = False
    selection_strategy: str = "none"
    selected_system_name: str | None = None
    embedding_backend: str = ""
    recommended_embedding_model: str = ""
    candidates: list[ThermoRagCandidate] = Field(default_factory=list)
    note: str = ""


class ExecutionResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    html_content: Optional[str] = None
    html_path: Optional[str] = None


class ArtifactRef(BaseModel):
    kind: ArtifactKind
    name: str
    path: Optional[str] = None
    url: Optional[str] = None
    content: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskRoute(BaseModel):
    name: str = "conversation.answer"
    workspace_id: str = "materials_agent"
    reason: str = ""
    selected_tool: Optional[str] = None
    intent: str = ""
    decision_source: str = ""
    decision_confidence: float | None = None
    compute_domain: ComputeDomain = "none"


class PlanStep(BaseModel):
    index: int
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    status: PlanStepStatus = "pending"
    retryable: bool = False
    description: str = ""
    stage: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolObservation(BaseModel):
    step_index: int
    tool_name: str
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


class AgentRunResponse(ExecutionResult):
    success: bool
    run_id: str
    conversation_id: str = "default"
    route: TaskRoute
    final_message: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    plan_steps: list[PlanStep] = Field(default_factory=list)
    trace: list[ToolObservation] = Field(default_factory=list)
    generated_code: Optional[str] = None
    termination_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    recognition_result: RecognitionResult | None = None
    current_context_summary: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    run_status: RunStatus = "completed"


class RunRecordSummary(BaseModel):
    run_id: str
    conversation_id: str = "default"
    status: RunStatus = "completed"
    route: TaskRoute = Field(default_factory=TaskRoute)
    final_message: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    trace: list[ToolObservation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentStreamEvent(BaseModel):
    type: AgentStreamEventType
    run_id: str
    emitted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentJobRecord(BaseModel):
    job_id: str
    request_id: str = ""
    job_type: str = "agent_chat"
    status: RunStatus = "queued"
    conversation_id: str = "default"
    run_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str = ""
    finished_at: str = ""
    progress_percent: int | None = None
    progress_stage: str = "queued"
    progress_message: str = "等待后台 worker 执行。"
    request_summary: str = ""
    result_run_id: str = ""
    error: str = ""
    event_count: int = 0
    attempt: int = 1
    source_job_id: str = ""
    source_run_id: str = ""
    source_checkpoint_id: str = ""
    resume_mode: str = ""


class AgentJobListResponse(BaseModel):
    count: int
    jobs: list[AgentJobRecord] = Field(default_factory=list)


class AgentJobEventRecord(BaseModel):
    event_id: int
    job_id: str
    event: AgentStreamEvent


class AgentJobResumeRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    checkpoint_id: str = Field(default="", max_length=160)
    strategy: str = Field(default="checkpoint_context", max_length=80)


class AgentJobResumeResponse(BaseModel):
    source_job: AgentJobRecord
    resumed_job: AgentJobRecord
    source_run_id: str = ""
    source_run_available: bool = False
    checkpoint_id: str = ""
    resume_mode: str = "new_attempt_with_checkpoint_context"
    message: str = ""


class ShortTermMemorySnapshot(BaseModel):
    conversation_id: str
    messages: list[ConversationTurn] = Field(default_factory=list)
    uploaded_assets: list[UploadedAsset] = Field(default_factory=list)
    recognition_result: RecognitionResult | None = None
    last_run_context: LastRunContext = Field(default_factory=LastRunContext)
    session_title: str = ""
    last_user_message: str = ""
    message_count: int = 0
    asset_count: int = 0
    summary_version: str = "v2"
    current_context_summary: str = ""
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LongTermMemorySnapshot(BaseModel):
    conversation_id: str
    summary_version: str = "v1"
    strategic_summary: str = ""
    salient_facts: list[str] = Field(default_factory=list)
    research_topics: list[str] = Field(default_factory=list)
    completed_run_summaries: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)
    user_preferences: list[str] = Field(default_factory=list)
    retrieval_hints: list[str] = Field(default_factory=list)
    compression_method: str = "heuristic_compaction"
    source_message_count: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MemorySnapshot(BaseModel):
    conversation_id: str
    short_term: ShortTermMemorySnapshot | None = None
    long_term: LongTermMemorySnapshot | None = None

    @model_validator(mode="after")
    def ensure_memory_layers(self) -> "MemorySnapshot":
        if self.short_term is None:
            self.short_term = ShortTermMemorySnapshot(conversation_id=self.conversation_id)
        if self.long_term is None:
            self.long_term = LongTermMemorySnapshot(conversation_id=self.conversation_id)
        return self

    @property
    def messages(self) -> list[ConversationTurn]:
        return self.short_term.messages

    @property
    def uploaded_assets(self) -> list[UploadedAsset]:
        return self.short_term.uploaded_assets

    @property
    def recognition_result(self) -> RecognitionResult | None:
        return self.short_term.recognition_result

    @property
    def last_run_context(self) -> LastRunContext:
        return self.short_term.last_run_context

    @property
    def session_title(self) -> str:
        return self.short_term.session_title

    @property
    def last_user_message(self) -> str:
        return self.short_term.last_user_message

    @property
    def message_count(self) -> int:
        return self.short_term.message_count

    @property
    def asset_count(self) -> int:
        return self.short_term.asset_count

    @property
    def summary_version(self) -> str:
        return self.short_term.summary_version

    @property
    def current_context_summary(self) -> str:
        return self.short_term.current_context_summary


class ConversationSnapshotResponse(BaseModel):
    conversation_id: str
    short_term: ShortTermMemorySnapshot
    long_term: LongTermMemorySnapshot
    latest_run: RunRecordSummary | None = None

    @property
    def updated_at(self) -> str:
        return self.short_term.updated_at


class AgentGraphState(TypedDict, total=False):
    run_id: str
    conversation_id: str
    request: AgentChatRequest
    messages: list[ConversationTurn]
    uploaded_assets: list[UploadedAsset]
    user_intent: str
    next_step: str
    supervisor_decision: dict[str, Any]
    compute_domain: ComputeDomain
    route: TaskRoute
    recognition_result: RecognitionResult | None
    phase_diagram_request: DiagramRequest | None
    phase_diagram_result: AgentRunResponse | None
    lammps_request: LammpsRequest | None
    lammps_result: AgentRunResponse | None
    last_run_context: LastRunContext
    artifact_messages: list[ArtifactRef]
    html_content: str
    html_path: str
    current_context_summary: str
    final_answer: str
    error: str
    success: bool
    termination_reason: str
    response_metadata: dict[str, Any]
    response_summary: dict[str, Any]
    plan_steps: list[PlanStep]
    trace: list[ToolObservation]
    event_sink: Any
    request_id: str
    memory_snapshot: MemorySnapshot
    long_term_memory_hits: list[str]
    shared_memory_context: dict[str, Any]
    shared_memory_events: list[dict[str, Any]]
    protocol_messages: list[AgentEnvelope]
    tool_decision: dict[str, Any]
    tool_results: list[dict[str, Any]]
    skill_decision: dict[str, Any]
    skill_context: str
