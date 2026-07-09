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

export interface ShortTermMemorySnapshot {
  conversation_id: string
  messages: ConversationTurn[]
  uploaded_assets: UploadedAsset[]
  recognition_result: RecognitionResult | null
  last_run_context: LastRunContext
  session_title: string
  last_user_message: string
  message_count: number
  asset_count: number
  summary_version: string
  current_context_summary: string
  updated_at: string
}

export interface LongTermMemorySnapshot {
  conversation_id: string
  summary_version: string
  strategic_summary: string
  salient_facts: string[]
  research_topics: string[]
  completed_run_summaries: string[]
  open_questions: string[]
  preferred_tools: string[]
  user_preferences: string[]
  retrieval_hints: string[]
  compression_method: string
  source_message_count: number
  updated_at: string
}

export interface ConversationSnapshotResponse {
  conversation_id: string
  short_term: ShortTermMemorySnapshot
  long_term: LongTermMemorySnapshot
  latest_run: RunRecordSummary | null
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
  request_id?: string
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

export interface PhysicalQualityReport {
  schema_version?: string
  run_mode?: 'real' | 'mock'
  passed?: boolean
  scientific_result_passed?: boolean
  synthetic_thermo?: boolean
  thermo_rows?: number
  max_step?: number
  requested_steps?: number
  step_coverage?: number
  final_temperature?: number | null
  average_temperature?: number | null
  temperature_deviation?: number | null
  final_total_energy?: number | null
  normalized_energy_drift?: number | null
  max_pressure?: number | null
  pressure_outlier_fraction?: number
  has_nan_or_inf?: boolean
  dump_exists?: boolean
  atom_count_valid?: boolean
  log_errors?: string[]
  issues?: string[]
  warnings?: string[]
  thresholds?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface LammpsEvidenceRef {
  evidence_id?: string
  source_type?: string
  source_ref?: string
  claim?: string
  authority?: string
  content_hash?: string
  supports?: string[]
  metadata?: Record<string, unknown>
}

export interface LammpsReviewFinding {
  finding_id?: string
  dimension?: string
  severity?: 'info' | 'warning' | 'blocking' | string
  message?: string
  evidence_refs?: string[]
  repairable?: boolean
  suggested_action?: string
  metadata?: Record<string, unknown>
}

export interface LammpsReviewScore {
  factual_correctness?: number
  logical_consistency?: number
  script_safety?: number
  physical_validity?: number
  evidence_quality?: number
  overall_score?: number
  blocking_findings?: number
  locked_constraint_violations?: number
  hard_gate_passed?: boolean
  score_source?: string
  metadata?: Record<string, unknown>
}

export interface LammpsReviewPayload {
  summary?: string
  passed?: boolean
  confidence?: number
  review_mode?: string
  issues?: string[]
  advisory_issues?: string[]
  llm_blocking_candidates?: string[]
  red_review?: {
    phase?: string
    passed?: boolean
    summary?: string
    findings?: LammpsReviewFinding[]
    score?: LammpsReviewScore
    evidence_refs?: LammpsEvidenceRef[]
    metadata?: Record<string, unknown>
  }
  score?: LammpsReviewScore
  findings?: LammpsReviewFinding[]
  evidence_refs?: LammpsEvidenceRef[]
  llm_review_parse_audit?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface LammpsRepairHistoryEntry {
  entry_type?: string
  stage?: string
  issues?: string[]
  raw_payload?: Record<string, unknown>
  blue_parse_audit?: Record<string, unknown>
  patch?: {
    schema_version?: string
    patch_id?: string
    operations?: Array<Record<string, unknown>>
    requires_user_confirmation?: boolean
    risk?: string
    source?: string
    metadata?: Record<string, unknown>
  }
  policy_report?: {
    accepted?: boolean
    request_changed?: boolean
    requires_user_confirmation?: boolean
    risk?: string
    applied_operations?: Array<Record<string, unknown>>
    rejected_operations?: Array<Record<string, unknown>>
    locked_constraint_violations?: string[]
    verification_steps?: string[]
    termination_reason?: string
    before_request?: Record<string, unknown>
    after_request?: Record<string, unknown>
    validation_report?: Record<string, unknown>
    metadata?: Record<string, unknown>
  }
  convergence_report?: Record<string, unknown>
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

export interface AgentJobRecord {
  job_id: string
  request_id: string
  job_type: string
  status: RunStatus
  conversation_id: string
  run_id: string
  created_at: string
  updated_at: string
  started_at: string
  finished_at: string
  progress_percent: number | null
  progress_stage: string
  progress_message: string
  request_summary: string
  result_run_id: string
  error: string
  event_count: number
  attempt: number
  source_job_id: string
  source_run_id: string
  source_checkpoint_id: string
  resume_mode: string
}

export interface AgentJobListResponse {
  count: number
  jobs: AgentJobRecord[]
}

export interface AgentJobResultResponse {
  ready: boolean
  job: AgentJobRecord
  run: RunRecordSummary | null
}

export interface AgentJobResumeRequest {
  message?: string
  checkpoint_id?: string
  strategy?: string
}

export interface AgentJobResumeResponse {
  source_job: AgentJobRecord
  resumed_job: AgentJobRecord
  source_run_id: string
  source_run_available: boolean
  checkpoint_id: string
  resume_mode: string
  message: string
}

export interface LlmRuntimeConfig {
  llm_enabled: boolean
  require_llm_for_agents: boolean
  python_executable: string
  llm_api_base_url: string
  llm_model: string
  llm_enable_thinking: boolean
  llm_supports_chat: boolean
  llm_supports_vision: boolean
  llm_supports_embedding: boolean
  llm_request_timeout_seconds: number
  llm_request_max_retries: number
  llm_retry_backoff_seconds: number
  llm_max_tokens: number
  thermo_rag_embedding_backend?: string
  thermo_rag_embedding_api_base_url?: string
  thermo_rag_embedding_model?: string
  thermo_rag_embedding_dimensions?: number
  thermo_rag_bm25_weight?: number
  thermo_rag_embedding_api_batch_size?: number
  thermo_rag_embedding_api_key_set?: boolean
  thermo_rag_embedding_api_key_masked?: string
  materials_rag_enabled?: boolean
  materials_rag_top_k?: number
  materials_rag_embedding_backend?: string
  materials_rag_embedding_api_base_url?: string
  materials_rag_embedding_model?: string
  materials_rag_embedding_dimensions?: number
  materials_rag_vector_weight?: number
  materials_rag_vector_min_similarity?: number
  materials_rag_bm25_weight?: number
  materials_rag_embedding_api_batch_size?: number
  materials_rag_embedding_api_key_set?: boolean
  materials_rag_embedding_api_key_masked?: string
  rag_reranker_enabled?: boolean
  rag_reranker_api_base_url?: string
  rag_reranker_model?: string
  rag_reranker_candidate_pool?: number
  rag_reranker_timeout_seconds?: number
  rag_reranker_api_key_set?: boolean
  rag_reranker_api_key_masked?: string
  rag_vector_store_path?: string
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
