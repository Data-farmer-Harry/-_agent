export type DiagramType = 'binary' | 'ternary'
export type WorkspaceId = 'phase_diagram' | 'lammps' | 'generic'
export type WorkspaceStatus = 'active' | 'reserved' | 'disabled'
export type TaskRouteName =
  | 'phase_diagram.generate'
  | 'phase_diagram.recognize'
  | 'phase_diagram.redraw_html'
  | 'phase_diagram.from_image'
  | 'phase_diagram.repair'
  | 'lammps.generate'
  | 'lammps.repair'
  | 'materials.lookup'
  | 'materials.compare'
  | 'materials.analysis'
  | 'generic.unknown'
export type ToolName =
  | 'phase_diagram_result_review'
  | 'phase_diagram_codegen'
  | 'phase_diagram_html_redraw'
  | 'phase_diagram_html_review'
  | 'phase_diagram_image_parse'
  | 'phase_diagram_image_render'
  | 'phase_diagram_repair'
  | 'python_execute'
  | 'load_latest_html_artifact'
  | 'lammps_command_router'
  | 'lammps_codegen'
  | 'lammps_execute'
  | 'lammps_repair'
export type ArtifactKind = 'html' | 'code' | 'text' | 'json'
export type PlanStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
export type InputChannel = 'text' | 'image' | 'structured' | 'generic'
export type DeliverableKind = 'html' | 'json' | 'text' | 'none'
export type AgentStreamEventType =
  | 'run_started'
  | 'step_started'
  | 'step_completed'
  | 'step_failed'
  | 'step_skipped'
  | 'run_completed'
  | 'run_error'

export interface DiagramRequest {
  system_name: string
  diagram_type: DiagramType
  temperature_min: number
  temperature_max: number
  pressure: number
  step_size: number
  notes: string
}

export interface AxisCalibration {
  label: string
  minimum: number
  maximum: number
}

export interface ImageDiagramRequest {
  image_data_url: string
  filename: string
  system_name: string
  chart_title: string
  diagram_type: DiagramType
  x_axis: AxisCalibration
  y_axis: AxisCalibration
  notes: string
}

export interface AgentChatRequest {
  message: string
  workspace_hint?: WorkspaceId
  system_name: string
  chart_title: string
  diagram_type: DiagramType
  temperature_min: number
  temperature_max: number
  pressure: number
  step_size: number
  notes: string
  image_data_url?: string
  filename: string
  x_axis?: AxisCalibration
  y_axis?: AxisCalibration
}

export interface GenerateResponse {
  success: boolean
  prompt: string
  generated_code: string
}

export interface RunCodeRequest {
  code: string
}

export interface ArtifactRef {
  kind: ArtifactKind
  name: string
  path: string | null
  content: string | null
  metadata?: Record<string, unknown>
}

export interface ToolObservation {
  step_index: number
  tool_name: ToolName
  success: boolean
  summary: string
  input: Record<string, unknown>
  output: Record<string, unknown>
  artifacts: ArtifactRef[]
  metadata?: Record<string, unknown>
  state_delta?: Record<string, unknown>
}

export interface TaskRoute {
  name: TaskRouteName
  workspace_id: WorkspaceId
  reason: string
  selected_tool?: string | null
  available_tools?: string[]
  reserved_tools?: string[]
  entry_tool?: string | null
  input_channels?: InputChannel[]
  deliverable?: DeliverableKind
  narrative?: string
  intent?: string
  decision_source?: string
  decision_confidence?: number | null
}

export interface PlanStep {
  index: number
  tool_name: ToolName
  input: Record<string, unknown>
  status: PlanStepStatus
  retryable: boolean
  description: string
  stage?: string
  metadata?: Record<string, unknown>
}

export interface ExecutionResult {
  success: boolean
  stdout: string
  stderr: string
  html_content: string | null
  html_path: string | null
}

export interface GenerateAndRunResponse extends ExecutionResult {
  generated_code: string
  prompt: string
  run_id: string | null
  route: TaskRouteName | null
  route_reason: string | null
  workspace_id: WorkspaceId | null
  selected_tool: string | null
  entry_tool: string | null
  available_tools: string[]
  reserved_tools: string[]
  input_channels: InputChannel[]
  deliverable: DeliverableKind
  narrative: string
  plan_steps: PlanStep[]
  trace: ToolObservation[]
  termination_reason: string
}

export interface AgentRunResponse extends ExecutionResult {
  success: boolean
  run_id: string
  route: TaskRoute
  final_message: string
  artifacts: ArtifactRef[]
  plan_steps: PlanStep[]
  trace: ToolObservation[]
  generated_code: string | null
  termination_reason: string
  metadata: Record<string, unknown>
}

export interface AgentStreamEvent {
  type: AgentStreamEventType
  run_id: string
  emitted_at: string
  payload: Record<string, unknown>
}

export interface HealthResponse {
  status: string
  app_name: string
  version: string
}

export interface WorkspaceSummary {
  id: WorkspaceId
  title: string
  description: string
  status: WorkspaceStatus
  available_tools: string[]
  reserved_tools: string[]
  default_route: TaskRouteName | null
  supported_routes: TaskRouteName[]
}

export interface ToolCatalogEntry {
  name: string
  description: string
  workspace_id: WorkspaceId
  status: WorkspaceStatus
  supports_routes: TaskRouteName[]
  tags: string[]
  produces_artifacts: ArtifactKind[]
  consumes: string[]
}

export interface AgentCatalogResponse {
  workspaces: WorkspaceSummary[]
  tools: ToolCatalogEntry[]
  supported_routes: TaskRouteName[]
}

export interface RouteBlueprintStep {
  tool_name: ToolName
  stage: string
  description: string
  retryable: boolean
  input_overrides: Record<string, unknown>
}

export interface RouteBlueprint {
  name: TaskRouteName
  workspace_id: WorkspaceId
  entry_tool: string | null
  description: string
  default_reason: string
  input_channels: InputChannel[]
  deliverable: DeliverableKind
  available_tools: string[]
  reserved_tools: string[]
  failure_strategy: string
  sample_prompts: string[]
  steps: RouteBlueprintStep[]
}

export interface AgentManifestResponse {
  generated_at: string
  workspaces: WorkspaceSummary[]
  routes: RouteBlueprint[]
  tools: ToolCatalogEntry[]
}

export interface ClientSettings {
  apiBaseUrl: string
  generatePath: string
  runPath: string
  generateAndRunPath: string
  requestTimeoutMs: number
  enableAutoRetry: boolean
}
