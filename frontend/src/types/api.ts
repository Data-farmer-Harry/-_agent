export type DiagramType = 'binary' | 'ternary'
export type TaskRouteName =
  | 'phase_diagram.generate'
  | 'phase_diagram.repair'
  | 'materials.lookup'
  | 'materials.compare'
  | 'materials.analysis'
  | 'generic.unknown'
export type ToolName =
  | 'phase_diagram_codegen'
  | 'phase_diagram_repair'
  | 'python_execute'
  | 'load_latest_html_artifact'
export type ArtifactKind = 'html' | 'code' | 'text' | 'json'
export type PlanStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
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
}

export interface TaskRoute {
  name: TaskRouteName
  reason: string
}

export interface PlanStep {
  index: number
  tool_name: ToolName
  input: Record<string, unknown>
  status: PlanStepStatus
  retryable: boolean
  description: string
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

export interface ClientSettings {
  apiBaseUrl: string
  generatePath: string
  runPath: string
  generateAndRunPath: string
  requestTimeoutMs: number
  enableAutoRetry: boolean
}
