export type DiagramType = 'binary' | 'ternary'
export type WorkspaceId = string
export type TaskRouteName = string
export type ToolName = string
export type ArtifactKind = 'html' | 'code' | 'text' | 'json' | 'image' | 'video' | 'markdown' | 'csv'
export type ComputeDomain = 'phase_diagram' | 'lammps' | 'none'
export type PlanStepStatus = 'pending' | 'running' | 'completed' | 'failed'
export type RunStatus = 'draft' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type AgentStreamEventType = 'run_started' | 'step_started' | 'step_completed' | 'step_failed' | 'run_completed' | 'run_error'

export interface ConversationTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface LastRunContext {
  run_id: string
  route_name: string
  compute_domain?: ComputeDomain
  system_name: string
  final_message: string
  generated_code_preview: string
  review_summary: string
  selected_tool: string
  generation_source: string
  request_summary: string
  review_passed: boolean | null
  review_issues: string[]
  review_advisory_issues: string[]
  trace_summary: string[]
  recognition_summary: string
  artifact_names?: string[]
}

export interface UploadedAsset {
  asset_id: string
  name: string
  media_type: string
  data_url: string
  size_bytes: number | null
}

export interface AxisSpec {
  label: string
  minimum: number | null
  maximum: number | null
  unit: string
}

export interface CriticalPoint {
  label: string
  composition: number | null
  temperature: number | null
  notes: string
}

export interface RecognitionResult {
  system: string
  diagram_type: DiagramType
  x_axis: AxisSpec
  y_axis: AxisSpec
  phases: string[]
  critical_points: CriticalPoint[]
  labels: string[]
  confidence: number
  source: string
  raw_summary: string
}

export interface AgentChatRequest {
  conversation_id: string
  message: string
  system_name: string
  diagram_type: DiagramType
  temperature_min: number
  temperature_max: number
  pressure: number
  step_size: number
  notes: string
  uploaded_assets: UploadedAsset[]
  conversation_history: ConversationTurn[]
  last_run_context: LastRunContext
}

export interface PromptSuggestionRequest {
  conversation_id: string
  draft_message: string
  conversation_history: ConversationTurn[]
  last_run_context: LastRunContext
  current_context_summary: string
}

export interface PromptSuggestionResponse {
  suggested_prompt: string
  rationale: string
  source: string
}

export interface ResultProfile {
  category: string
  source_label: string
  mode_label: string
  trust_level: 'high' | 'medium' | 'low' | 'unknown'
  confidence: number | null
  trust_statement: string
  assumptions: string[]
  warnings: string[]
  evidence: string[]
}

export interface ArtifactRef {
  kind: ArtifactKind
  name: string
  path: string | null
  url?: string | null
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
  intent?: string
  decision_source?: string
  decision_confidence?: number | null
  compute_domain?: ComputeDomain
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

export interface AgentRunResponse extends ExecutionResult {
  success: boolean
  run_id: string
  conversation_id: string
  route: TaskRoute
  final_message: string
  artifacts: ArtifactRef[]
  plan_steps: PlanStep[]
  trace: ToolObservation[]
  generated_code: string | null
  termination_reason: string
  metadata: Record<string, unknown>
  recognition_result: RecognitionResult | null
  current_context_summary: string
  summary: Record<string, unknown>
  run_status: RunStatus
}

export interface RunRecordSummary {
  run_id: string
  conversation_id: string
  status: RunStatus
  route: TaskRoute
  final_message: string
  summary: Record<string, unknown>
  artifacts: ArtifactRef[]
  trace: ToolObservation[]
  metadata: Record<string, unknown>
  updated_at: string
}

export interface RunListResponse {
  count: number
  runs: RunRecordSummary[]
}

export interface LlmRuntimeConfig {
  llm_enabled: boolean
  require_llm_for_agents: boolean
  python_executable: string
  llm_api_base_url: string
  llm_model: string
  llm_request_timeout_seconds: number
  llm_request_max_retries: number
  llm_retry_backoff_seconds: number
  llm_max_tokens: number
  api_key_set: boolean
  api_key_masked: string
  updated?: boolean
}

export interface LammpsRuntimeConfig {
  lammps_command: string
  potentials_dir: string
  ovito_location: string
  allow_mock_fallback: boolean
  force_mock: boolean
  max_retries: number
  lammps_command_exists: boolean
  potentials_dir_exists: boolean
  ovito_available: boolean
  ovito_backend: string
  updated?: boolean
}

export interface DiagnosticCheck {
  name: string
  status: 'ok' | 'warning' | 'error' | 'unknown'
  summary: string
  details: Record<string, unknown>
}

export interface SystemDiagnosticsResponse {
  generated_at: string
  overall_status: 'ok' | 'warning' | 'error'
  checks: DiagnosticCheck[]
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
  requestTimeoutMs: number
}
