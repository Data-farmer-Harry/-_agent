import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import {
  cancelJobRequest,
  cancelRunRequest,
  getAgentJobResult,
  getArtifactText,
  getConversationSnapshot,
  getRunResultHtml,
  getRunSummary,
  getRuns,
  runAgentChat,
  streamAgentChat,
  streamAgentChatJob,
  submitAgentChatJob,
} from '../../services/api'
import type {
  AgentChatRequest,
  AgentRunResponse,
  AgentStreamEvent,
  ArtifactRef,
  ClientSettings,
  ConversationSnapshotResponse,
  ConversationTurn,
  LastRunContext,
  PlanStep,
  RecognitionResult,
  RunRecordSummary,
  RunStatus,
  TaskRoute,
  ToolObservation,
  UploadedAsset,
} from '../../types/api'

type UiRunStatus = 'idle' | 'streaming' | 'loading-result' | 'completed' | 'error'
type MessageTone = 'normal' | 'status' | 'warning'
const TERMINAL_RUN_STATUSES = new Set<RunStatus>(['completed', 'failed', 'cancelled'])
const CHAT_STATE_STORAGE_KEY = 'materials-agent-chat-state-v1'
const ACTIVE_CONVERSATION_STORAGE_KEY = 'materials-agent-active-conversation-v1'

export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  tone: MessageTone
  content: string
  kind?: 'text' | 'artifact' | 'recognition'
  htmlContent?: string
  artifactStatus?: string
  recognitionResult?: RecognitionResult
  artifacts?: ArtifactRef[]
  runId?: string
  routeName?: string
  runStatus?: RunStatus | string
  summary?: Record<string, unknown>
  attachments?: UploadedAsset[]
}

export interface LiveProgressStep {
  index: number
  label: string
  status: PlanStep['status']
}

export interface LiveProgressSnapshot {
  percent: number | null
  completed: number
  total: number
  currentLabel: string
  steps: LiveProgressStep[]
  indeterminate: boolean
}

interface AgentChatState {
  conversationId: string
  messages: ConversationMessage[]
  jobId: string
  runId: string
  route: TaskRoute | null
  planSteps: PlanStep[]
  timeline: ToolObservation[]
  htmlContent: string
  finalMessage: string
  generatedCode: string
  stdout: string
  stderr: string
  terminationReason: string
  responseMetadata: Record<string, unknown>
  recognitionResult: RecognitionResult | null
  artifacts: ArtifactRef[]
  summary: Record<string, unknown>
  runStatus: RunStatus | string
  currentContextSummary: string
  isLoading: boolean
  status: UiRunStatus
  statusMessage: string
}

type Action =
  | { type: 'send_started'; payload: AgentChatRequest }
  | { type: 'job_submitted'; jobId: string; message: string }
  | { type: 'assistant_message'; message: ConversationMessage }
  | { type: 'status_updated'; message: string; status?: UiRunStatus; isLoading?: boolean }
  | { type: 'stream_event'; event: AgentStreamEvent }
  | { type: 'response_received'; response: AgentRunResponse }
  | { type: 'run_loaded'; response: AgentRunResponse }
  | { type: 'result_html_loaded'; htmlContent: string }
  | { type: 'hydrate'; state: AgentChatState }
  | { type: 'run_failed'; message: string }
  | { type: 'reset'; conversationId: string }

function createMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function createConversationId(): string {
  return `conv-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function makeAssistantMessage(content: string, tone: MessageTone = 'normal'): ConversationMessage {
  return {
    id: createMessageId(),
    role: 'assistant',
    tone,
    content,
    kind: 'text',
  }
}

function makeArtifactMessage(
  htmlContent: string,
  artifactStatus: string,
  artifacts: ArtifactRef[] = [],
  summary: Record<string, unknown> = {},
  runId = '',
  routeName = '',
  runStatus: RunStatus | string = 'completed',
): ConversationMessage {
  return {
    id: createMessageId(),
    role: 'assistant',
    tone: 'status',
    content: '计算结果',
    kind: 'artifact',
    htmlContent,
    artifactStatus,
    artifacts,
    summary,
    runId,
    routeName,
    runStatus,
  }
}

function mergeArtifacts(current: ArtifactRef[], incoming: ArtifactRef[]): ArtifactRef[] {
  const merged = [...current]
  const seen = new Set(merged.map((artifact) => `${artifact.kind}:${artifact.name}:${artifact.path || artifact.url || ''}`))
  for (const artifact of incoming) {
    const key = `${artifact.kind}:${artifact.name}:${artifact.path || artifact.url || ''}`
    if (seen.has(key)) {
      continue
    }
    merged.push(artifact)
    seen.add(key)
  }
  return merged
}

function hasRenderableArtifacts(artifacts: ArtifactRef[]): boolean {
  return artifacts.some((artifact) => ['html', 'image', 'video', 'markdown'].includes(artifact.kind))
}

function hasRenderablePayload(htmlContent: string | null | undefined, artifacts: ArtifactRef[]): boolean {
  return Boolean(htmlContent) || hasRenderableArtifacts(artifacts)
}

function upsertArtifactMessage(
  messages: ConversationMessage[],
  {
    htmlContent,
    artifactStatus,
    artifacts,
    summary,
    runId,
    routeName,
    runStatus,
  }: {
    htmlContent: string
    artifactStatus: string
    artifacts: ArtifactRef[]
    summary: Record<string, unknown>
    runId: string
    routeName: string
    runStatus: RunStatus | string
  },
): ConversationMessage[] {
  if (!hasRenderablePayload(htmlContent, artifacts)) {
    return messages
  }

  const nextMessage = makeArtifactMessage(htmlContent, artifactStatus, artifacts, summary, runId, routeName, runStatus)
  const existingIndex = messages.findIndex((message) => message.kind === 'artifact' && message.runId === runId)
  if (existingIndex < 0) {
    return [...messages, nextMessage]
  }

  const existing = messages[existingIndex]
  const patched: ConversationMessage = {
    ...existing,
    htmlContent,
    artifactStatus,
    artifacts,
    summary,
    routeName,
    runStatus,
  }
  return [...messages.slice(0, existingIndex), patched, ...messages.slice(existingIndex + 1)]
}


function makeRecognitionMessage(recognitionResult: RecognitionResult): ConversationMessage {
  const system = recognitionResult.system || '未明确体系'
  const confidence = recognitionResult.confidence ? `，置信度 ${recognitionResult.confidence.toFixed(2)}` : ''
  return {
    id: createMessageId(),
    role: 'assistant',
    tone: 'status',
    content: recognitionResult.raw_summary || `已完成截图识别：${system}${confidence}。`,
    kind: 'recognition',
    recognitionResult,
  }
}

function isArtifactRoute(routeName: string | undefined): boolean {
  return routeName === 'phase_diagram.generate' || routeName === 'mixed.request' || routeName === 'lammps.generate' || routeName === 'recognition.analyze'
}

function responseCarriesRenderableArtifact(response: AgentRunResponse): boolean {
  return hasRenderablePayload(response.html_content, response.artifacts || [])
}

function formatAgentRequestError(message: string, apiBaseUrl: string): string {
  const normalized = message.trim()

  if (/not found/i.test(normalized) || normalized.includes('{"detail":"Not Found"}')) {
    return `当前连接的后端 ${apiBaseUrl} 没有暴露新的 4-agent 接口。请确认 backend 已经启动在固定端口 8000。`
  }

  if (/failed to fetch/i.test(normalized) || /network/i.test(normalized)) {
    return `当前无法连接到后端 ${apiBaseUrl}。请确认 backend 已经启动。`
  }

  return normalized
}

function isTransientRunLookupError(message: string): boolean {
  const normalized = message.trim().toLowerCase()
  return (
    normalized.includes('http 404') ||
    normalized.includes('not found') ||
    normalized.includes('failed to fetch') ||
    normalized.includes('network') ||
    normalized.includes('请求超时')
  )
}

function updateStep(steps: PlanStep[], step: PlanStep): PlanStep[] {
  const next = [...steps]
  const index = next.findIndex((item) => item.index === step.index)
  if (index >= 0) {
    next[index] = step
  } else {
    next.push(step)
  }
  return next.sort((a, b) => a.index - b.index)
}

function stepStatusLabel(toolName: string, failed: boolean): string {
  switch (toolName) {
    case 'SupervisorAgent':
      return failed ? '路由判断失败。' : '正在判断这轮该走聊天、识别还是计算。'
    case 'RecognitionAgent':
      return failed ? '截图识别失败。' : '正在识别截图并整理结构化结果。'
    case 'thermo_database_lookup':
      return failed ? '热力学数据库检索失败。' : '正在检索热力学数据库。'
    case 'request_interpreter':
      return failed ? '相图请求解析失败。' : '正在整理相图生成请求。'
    case 'phase_diagram_codegen':
      return failed ? '相图代码生成失败。' : '正在生成相图 Python wrapper。'
    case 'python_execute':
      return failed ? '本地 Python 执行失败。' : '正在本地执行相图代码。'
    case 'phase_diagram_result_review':
      return failed ? '相图结果审查未通过。' : '正在审查相图结果和准确率。'
    case 'phase_diagram_repair':
      return failed ? '相图修复失败。' : '正在根据反馈修复相图代码。'
    case 'lammps_request_interpreter':
      return failed ? 'LAMMPS 请求解析失败。' : '正在整理 LAMMPS 结构化参数。'
    case 'lammps_registry_lookup':
      return failed ? 'LAMMPS registry 未命中。' : '正在检查 LAMMPS registry。'
    case 'lammps_validation':
      return failed ? 'LAMMPS 参数校验未通过。' : '正在校验 LAMMPS 请求参数。'
    case 'lammps_input_codegen':
      return failed ? 'LAMMPS 输入脚本生成失败。' : '正在生成 LAMMPS 输入脚本。'
    case 'lammps_execute':
      return failed ? 'LAMMPS 执行失败。' : '正在本地执行 LAMMPS。'
    case 'lammps_postprocess':
      return failed ? 'LAMMPS 后处理失败。' : '正在整理热力学图、轨迹和报告。'
    case 'lammps_result_review':
      return failed ? 'LAMMPS 结果审查未通过。' : '正在审查 LAMMPS 结果。'
    case 'ChatAgent':
      return failed ? '最终回答生成失败。' : '正在整理最终回答。'
    case 'load_memory':
      return failed ? '上下文加载失败。' : '正在加载上下文记忆。'
    case 'summarize_context':
      return failed ? '上下文摘要失败。' : '正在更新上下文摘要。'
    case 'save_memory':
      return failed ? '会话记忆保存失败。' : '正在保存会话记忆。'
    case 'respond':
      return failed ? '响应整理失败。' : '正在整理返回结果。'
    default:
      return failed ? `${toolName} 失败。` : `正在执行 ${toolName}。`
  }
}

function formatLiveStatus(stepIndex: number, label: string): string {
  return `步骤 ${stepIndex} · ${label}`
}

function terminalMessageFromResponse(response: AgentRunResponse): ConversationMessage | null {
  if (!response.final_message.trim()) {
    return null
  }

  if (response.route.name === 'conversation.answer') {
    return makeAssistantMessage(response.final_message, 'normal')
  }

  return makeAssistantMessage(response.final_message, response.success ? 'status' : 'warning')
}


function artifactMessageFromResponse(response: AgentRunResponse): ConversationMessage | null {
  if (responseCarriesRenderableArtifact(response)) {
    return makeArtifactMessage(
      response.html_content || '',
      nextStatusMessage(response),
      response.artifacts,
      response.summary || {},
      response.run_id,
      response.route.name,
      response.run_status
    )
  }
  return null
}

function responseMessagesFromRun(response: AgentRunResponse): ConversationMessage[] {
  const messages: ConversationMessage[] = []
  if (response.recognition_result && (response.route.name === 'recognition.analyze' || response.route.name === 'mixed.request')) {
    messages.push(makeRecognitionMessage(response.recognition_result))
  }
  const artifactMessage = artifactMessageFromResponse(response)
  if (artifactMessage) {
    messages.push(artifactMessage)
  }
  const terminalMessage = terminalMessageFromResponse(response)
  if (terminalMessage) {
    messages.push(terminalMessage)
  }
  return messages
}

function mergeResponseMessages(
  messages: ConversationMessage[],
  response: AgentRunResponse,
  responseMessages: ConversationMessage[],
): ConversationMessage[] {
  let nextMessages = messages
  for (const message of responseMessages) {
    if (message.kind === 'artifact') {
      nextMessages = upsertArtifactMessage(nextMessages, {
        htmlContent: message.htmlContent || '',
        artifactStatus: message.artifactStatus || '',
        artifacts: message.artifacts || response.artifacts || [],
        summary: message.summary || response.summary || {},
        runId: response.run_id,
        routeName: response.route.name,
        runStatus: response.run_status,
      })
      continue
    }
    nextMessages = [...nextMessages, message]
  }
  return nextMessages
}

export function buildConversationHistory(messages: ConversationMessage[]): ConversationTurn[] {
  return messages
    .filter((message) => message.kind !== 'artifact')
    .slice(-10)
    .map((message) => ({
      role: message.role,
      content: message.content.slice(0, 1200),
    }))
}

function extractSystemNameFromTimeline(timeline: ToolObservation[]): string {
  const requestStep = [...timeline].reverse().find((item) => item.tool_name === 'request_interpreter')
  const diagramRequest = (requestStep?.output?.diagram_request ?? null) as Record<string, unknown> | null
  if (typeof diagramRequest?.system_name === 'string') {
    return diagramRequest.system_name
  }
  const lammpsRequestStep = [...timeline].reverse().find((item) => item.tool_name === 'lammps_request_interpreter')
  const lammpsRequest = (lammpsRequestStep?.output?.request ?? null) as Record<string, unknown> | null
  return typeof lammpsRequest?.material === 'string' ? lammpsRequest.material : ''
}

function extractRequestSummary(timeline: ToolObservation[]): string {
  const requestStep = [...timeline].reverse().find((item) => item.tool_name === 'request_interpreter')
  const diagramRequest = (requestStep?.output?.diagram_request ?? null) as Record<string, unknown> | null
  if (!diagramRequest) {
    return ''
  }

  const systemName = typeof diagramRequest.system_name === 'string' ? diagramRequest.system_name : ''
  const temperatureMin = typeof diagramRequest.temperature_min === 'number' ? diagramRequest.temperature_min : null
  const temperatureMax = typeof diagramRequest.temperature_max === 'number' ? diagramRequest.temperature_max : null
  const diagramType = typeof diagramRequest.diagram_type === 'string' ? diagramRequest.diagram_type : ''

  if (!systemName) {
    const lammpsRequestStep = [...timeline].reverse().find((item) => item.tool_name === 'lammps_request_interpreter')
    const lammpsRequest = (lammpsRequestStep?.output?.request ?? null) as Record<string, unknown> | null
    if (!lammpsRequest) {
      return ''
    }
    const material = typeof lammpsRequest.material === 'string' ? lammpsRequest.material : ''
    const taskType = typeof lammpsRequest.task_type === 'string' ? lammpsRequest.task_type : ''
    const temperature = typeof lammpsRequest.temperature === 'number' ? lammpsRequest.temperature : null
    const steps = typeof lammpsRequest.steps === 'number' ? lammpsRequest.steps : null
    const suffixParts = [
      temperature !== null ? `${Math.round(temperature)} K` : '',
      steps !== null ? `${Math.round(steps)} steps` : '',
    ].filter(Boolean)
    return [material, taskType, suffixParts.join(' / ')].filter(Boolean).join(' ').trim()
  }

  const temperatureSummary =
    temperatureMin !== null && temperatureMax !== null ? `，${Math.round(temperatureMin)}-${Math.round(temperatureMax)} K` : ''

  return `${systemName} ${diagramType || 'phase diagram'}${temperatureSummary}`.trim()
}

function extractReviewSummary(metadata: Record<string, unknown>): string {
  const review = (metadata.review ?? null) as Record<string, unknown> | null
  return typeof review?.summary === 'string' ? review.summary : ''
}

function extractReviewPayload(metadata: Record<string, unknown>): Record<string, unknown> | null {
  const review = metadata.review
  return review && typeof review === 'object' ? (review as Record<string, unknown>) : null
}

export function buildLastRunContext(state: AgentChatState): LastRunContext {
  if (!state.runId || state.route?.name === 'conversation.answer') {
    return {
      run_id: '',
      route_name: '',
      compute_domain: 'none',
      system_name: '',
      final_message: '',
      generated_code_preview: '',
      review_summary: '',
      selected_tool: '',
      generation_source: '',
      request_summary: '',
      review_passed: null,
      review_issues: [],
      review_advisory_issues: [],
      trace_summary: [],
      recognition_summary: '',
      artifact_names: [],
    }
  }

  const review = extractReviewPayload(state.responseMetadata)
  const artifactNames = state.artifacts.map((artifact) => artifact.name)
  return {
    run_id: state.runId,
    route_name: state.route?.name || '',
    compute_domain: state.route?.compute_domain || 'none',
    system_name: extractSystemNameFromTimeline(state.timeline),
    final_message: state.finalMessage.slice(0, 1200),
    generated_code_preview: state.generatedCode.slice(0, 2500),
    review_summary: extractReviewSummary(state.responseMetadata),
    selected_tool: state.route?.selected_tool || '',
    generation_source: typeof state.responseMetadata.generation_source === 'string' ? state.responseMetadata.generation_source : '',
    request_summary: extractRequestSummary(state.timeline),
    review_passed: typeof review?.passed === 'boolean' ? review.passed : null,
    review_issues: Array.isArray(review?.issues) ? review.issues.filter((item): item is string => typeof item === 'string').slice(0, 6) : [],
    review_advisory_issues: Array.isArray(review?.advisory_issues)
      ? review.advisory_issues.filter((item): item is string => typeof item === 'string').slice(0, 6)
      : [],
    trace_summary: state.timeline.slice(-8).map((item) => `${item.tool_name}: ${item.summary}`),
    recognition_summary: state.recognitionResult?.raw_summary || '',
    artifact_names: artifactNames,
  }
}

function nextStatusMessage(response: AgentRunResponse): string {
  if (response.route.name === 'conversation.answer' && responseCarriesRenderableArtifact(response)) {
    return '已在当前对话中直接渲染交互式 HTML。'
  }
  if (response.route.name === 'conversation.answer') {
    return '本轮停留在对话模式，没有调用后端工具链。'
  }
  if (response.route.name === 'recognition.analyze') {
    return response.html_content || hasHtmlArtifact(response)
      ? 'RecognitionAgent 已生成可交互的识别模拟器。'
      : 'RecognitionAgent 已完成截图结构化识别。'
  }
  if (response.route.name === 'lammps.generate') {
    return response.success ? 'LAMMPS 结果已返回。' : 'LAMMPS 任务没有通过完整流程。'
  }
  if (response.html_content) {
    return '相图结果已返回。'
  }
  if (isArtifactRoute(response.route.name) && hasHtmlArtifact(response)) {
    return '相图执行已完成，正在加载结果页面。'
  }
  return '本轮 agent 已处理完成。'
}

function hasHtmlArtifact(response: AgentRunResponse): boolean {
  return Boolean(
    response.html_content ||
      response.html_path ||
      response.artifacts.some((artifact) => artifact.kind === 'html')
  )
}

function buildConversationMessageFromTurn(turn: ConversationTurn): ConversationMessage {
  return {
    id: createMessageId(),
    role: turn.role,
    tone: 'normal',
    content: turn.content,
    kind: 'text',
  }
}

function compactMessageAttachments(attachments: UploadedAsset[] | undefined): UploadedAsset[] | undefined {
  if (!attachments?.length) {
    return undefined
  }
  return attachments.map((asset) => ({
    ...asset,
    data_url: '',
  }))
}

function attachAssetsToLatestUserMessage(
  messages: ConversationMessage[],
  uploadedAssets: UploadedAsset[],
): ConversationMessage[] {
  if (!uploadedAssets.length) {
    return messages
  }
  const latestUserIndex = [...messages].reverse().findIndex((message) => message.role === 'user')
  if (latestUserIndex < 0) {
    return messages
  }
  const targetIndex = messages.length - 1 - latestUserIndex
  const targetMessage = messages[targetIndex]
  if (targetMessage.attachments?.length) {
    return messages
  }
  const patchedMessage: ConversationMessage = {
    ...targetMessage,
    attachments: uploadedAssets,
  }
  return [...messages.slice(0, targetIndex), patchedMessage, ...messages.slice(targetIndex + 1)]
}

function buildStateFromConversationSnapshot(snapshot: ConversationSnapshotResponse): AgentChatState {
  const latestResponse = snapshot.latest_run ? responseFromRunRecord(snapshot.latest_run) : null
  const baseState = latestResponse ? applyAgentRunResponse(initialState, latestResponse) : { ...initialState, conversationId: snapshot.conversation_id }
  let messages = snapshot.short_term.messages.map(buildConversationMessageFromTurn)
  messages = attachAssetsToLatestUserMessage(messages, snapshot.short_term.uploaded_assets || [])
  if (snapshot.short_term.recognition_result && !messages.some((message) => message.kind === 'recognition')) {
    messages = [...messages, makeRecognitionMessage(snapshot.short_term.recognition_result)]
  }
  if (latestResponse) {
    const artifactMessage = artifactMessageFromResponse(latestResponse)
    if (artifactMessage) {
      messages = mergeResponseMessages(messages, latestResponse, [artifactMessage])
    }
  }
  return {
    ...baseState,
    conversationId: snapshot.conversation_id,
    messages,
    recognitionResult: snapshot.short_term.recognition_result,
    currentContextSummary: snapshot.short_term.current_context_summary || baseState.currentContextSummary,
    runId: latestResponse?.run_id || snapshot.short_term.last_run_context.run_id || '',
    route: latestResponse?.route || baseState.route,
    runStatus: latestResponse?.run_status || baseState.runStatus,
    artifacts: latestResponse?.artifacts || baseState.artifacts,
    summary: latestResponse?.summary || baseState.summary,
    responseMetadata: latestResponse?.metadata || baseState.responseMetadata,
    finalMessage: latestResponse?.final_message || baseState.finalMessage,
    isLoading: false,
    status: 'completed',
    statusMessage: messages.length ? '已恢复上一轮会话。' : '等待输入',
  }
}

function readStoredConversationId(): string {
  try {
    return window.localStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function readStoredAgentState(): AgentChatState | null {
  try {
    const raw = window.localStorage.getItem(CHAT_STATE_STORAGE_KEY)
    if (!raw) {
      return null
    }
    const parsed = JSON.parse(raw) as Partial<AgentChatState>
    if (!parsed || typeof parsed !== 'object') {
      return null
    }
    const conversationId = typeof parsed.conversationId === 'string' && parsed.conversationId.trim() ? parsed.conversationId : createConversationId()
    return {
      ...initialState,
      ...parsed,
      conversationId,
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
      planSteps: Array.isArray(parsed.planSteps) ? parsed.planSteps : [],
      timeline: Array.isArray(parsed.timeline) ? parsed.timeline : [],
      artifacts: Array.isArray(parsed.artifacts) ? parsed.artifacts : [],
      responseMetadata: parsed.responseMetadata && typeof parsed.responseMetadata === 'object' ? parsed.responseMetadata : {},
      summary: parsed.summary && typeof parsed.summary === 'object' ? parsed.summary : {},
      isLoading: false,
      status: parsed.messages && Array.isArray(parsed.messages) && parsed.messages.length ? 'completed' : 'idle',
      statusMessage: typeof parsed.statusMessage === 'string' && parsed.statusMessage ? parsed.statusMessage : '等待输入',
    }
  } catch {
    return null
  }
}

function persistAgentState(state: AgentChatState): void {
  try {
    const payload: AgentChatState = {
      ...state,
      messages: state.messages.slice(-40).map((message) => ({
        ...message,
        attachments: compactMessageAttachments(message.attachments),
      })),
      timeline: state.timeline.slice(-20),
      planSteps: state.planSteps.slice(-20),
    }
    window.localStorage.setItem(CHAT_STATE_STORAGE_KEY, JSON.stringify(payload))
    window.localStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, state.conversationId)
  } catch {
    // ignore localStorage failures
  }
}

function applyAgentRunResponse(state: AgentChatState, response: AgentRunResponse): AgentChatState {
  const expectsPhaseArtifact = response.success && responseCarriesRenderableArtifact(response) && hasHtmlArtifact(response)
  return {
    ...state,
    conversationId: response.conversation_id || state.conversationId,
    runId: response.run_id || state.runId,
    route: response.route || state.route,
    planSteps: response.plan_steps || state.planSteps,
    timeline: response.trace || state.timeline,
    responseMetadata: response.metadata || {},
    recognitionResult: response.recognition_result || null,
    artifacts: response.artifacts || [],
    summary: response.summary || {},
    runStatus: response.run_status || 'completed',
    currentContextSummary: response.current_context_summary || '',
    generatedCode: response.generated_code || '',
    stdout: response.stdout || '',
    stderr: response.stderr || '',
    terminationReason: response.termination_reason || '',
    htmlContent: response.html_content || '',
    finalMessage: response.final_message || '',
    isLoading: expectsPhaseArtifact && !response.html_content,
    status: expectsPhaseArtifact && !response.html_content ? 'loading-result' : 'completed',
    statusMessage: nextStatusMessage(response),
  }
}

function responseFromRunRecord(record: RunRecordSummary): AgentRunResponse {
  return {
    success: record.status === 'completed',
    run_id: record.run_id,
    conversation_id: record.conversation_id,
    route: record.route,
    final_message: record.final_message,
    artifacts: record.artifacts,
    plan_steps: [],
    trace: record.trace,
    generated_code: '',
    stdout: '',
    stderr: '',
    html_content: null,
    html_path: null,
    termination_reason: record.status,
    metadata: record.metadata,
    recognition_result: null,
    current_context_summary: '',
    summary: record.summary || {},
    run_status: record.status,
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

const initialState: AgentChatState = {
  conversationId: createConversationId(),
  messages: [],
  jobId: '',
  runId: '',
  route: null,
  planSteps: [],
  timeline: [],
  htmlContent: '',
  finalMessage: '',
  generatedCode: '',
  stdout: '',
  stderr: '',
  terminationReason: '',
  responseMetadata: {},
  recognitionResult: null,
  artifacts: [],
  summary: {},
  runStatus: 'draft',
  currentContextSummary: '',
  isLoading: false,
  status: 'idle',
  statusMessage: '等待输入',
}

function reducer(state: AgentChatState, action: Action): AgentChatState {
  switch (action.type) {
    case 'send_started':
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: createMessageId(),
            role: 'user',
            tone: 'normal',
            content: action.payload.message,
            kind: 'text',
            attachments: action.payload.uploaded_assets,
          },
        ],
        jobId: '',
        runId: '',
        route: null,
        planSteps: [],
        timeline: [],
        htmlContent: '',
        finalMessage: '',
        generatedCode: '',
        stdout: '',
        stderr: '',
        terminationReason: '',
        responseMetadata: {},
        recognitionResult: null,
        artifacts: [],
        summary: {},
        runStatus: 'draft',
        currentContextSummary: '',
        isLoading: true,
        status: 'streaming',
        statusMessage: 'Agent 正在处理中…',
      }
    case 'job_submitted':
      return {
        ...state,
        jobId: action.jobId,
        status: 'streaming',
        isLoading: true,
        statusMessage: action.message,
      }
    case 'assistant_message':
      return {
        ...state,
        messages: [...state.messages, action.message],
      }
    case 'status_updated':
      return {
        ...state,
        statusMessage: action.message,
        status: action.status ?? state.status,
        isLoading: action.isLoading ?? state.isLoading,
      }
    case 'stream_event': {
      const { event } = action
      if (event.type === 'run_started') {
        const route = (event.payload.route as TaskRoute | undefined) || state.route
        return {
          ...state,
          runId: event.run_id || state.runId,
          route,
          status: 'streaming',
          statusMessage: route?.name === 'conversation.answer' ? '进入对话模式。' : '进入 4-agent 编排模式。',
        }
      }

      if (event.type === 'step_started') {
        const step = event.payload.plan_step as PlanStep | undefined
        return {
          ...state,
          runId: event.run_id || state.runId,
          planSteps: step ? updateStep(state.planSteps, step) : state.planSteps,
          statusMessage: step ? formatLiveStatus(step.index, stepStatusLabel(step.tool_name, false)) : state.statusMessage,
        }
      }

      if (event.type === 'step_completed' || event.type === 'step_failed') {
        const step = event.payload.plan_step as PlanStep | undefined
        const observation = event.payload.observation as ToolObservation | undefined
        const mergedArtifacts = observation ? mergeArtifacts(state.artifacts, observation.artifacts || []) : state.artifacts
        const outputMetrics =
          observation?.output && typeof observation.output.metrics === 'object'
            ? (observation.output.metrics as Record<string, unknown>)
            : null
        const nextSummary = outputMetrics ? { ...state.summary, metrics: outputMetrics } : state.summary
        const nextMessages =
          observation && state.route?.name === 'lammps.generate'
            ? upsertArtifactMessage(state.messages, {
                htmlContent: '',
                artifactStatus: observation.summary,
                artifacts: mergedArtifacts,
                summary: nextSummary,
                runId: event.run_id || state.runId,
                routeName: state.route?.name || '',
                runStatus: 'running',
              })
            : state.messages
        return {
          ...state,
          runId: event.run_id || state.runId,
          planSteps: step ? updateStep(state.planSteps, step) : state.planSteps,
          timeline: observation ? [...state.timeline, observation] : state.timeline,
          artifacts: mergedArtifacts,
          summary: nextSummary,
          messages: nextMessages,
          statusMessage:
            observation ? formatLiveStatus(observation.step_index, stepStatusLabel(observation.tool_name, !observation.success)) : state.statusMessage,
        }
      }

      if (event.type === 'run_completed') {
        const response = event.payload.response as AgentRunResponse | undefined
        if (!response) {
          return {
            ...state,
            isLoading: false,
            status: 'completed',
            statusMessage: '运行已结束。',
          }
        }
        const nextState = applyAgentRunResponse(state, response)
        const responseMessages = responseMessagesFromRun(response)
        return responseMessages.length
          ? {
              ...nextState,
              messages: mergeResponseMessages(nextState.messages, response, responseMessages),
            }
          : nextState
      }

      if (event.type === 'run_error') {
        return {
          ...state,
          isLoading: false,
          status: 'error',
          statusMessage: typeof event.payload.message === 'string' ? event.payload.message : '运行失败。',
        }
      }

      return state
    }
    case 'response_received': {
      const nextState = applyAgentRunResponse(state, action.response)
      const responseMessages = responseMessagesFromRun(action.response)
      return responseMessages.length
        ? {
            ...nextState,
            messages: mergeResponseMessages(nextState.messages, action.response, responseMessages),
          }
        : nextState
    }
    case 'run_loaded': {
      const nextState = applyAgentRunResponse(initialState, action.response)
      const responseMessages = responseMessagesFromRun(action.response)
      return {
        ...nextState,
        messages: mergeResponseMessages([], action.response, responseMessages),
        isLoading: false,
        status: 'completed',
      }
    }
    case 'result_html_loaded':
      return {
        ...state,
        htmlContent: action.htmlContent,
        isLoading: false,
        status: 'completed',
        statusMessage: state.route?.name === 'recognition.analyze' ? '识别模拟器已加载。' : '相图结果页已加载。',
        messages: upsertArtifactMessage(state.messages, {
          htmlContent: action.htmlContent,
          artifactStatus: state.route?.name === 'recognition.analyze' ? '识别模拟器已加载。' : '相图结果页已加载。',
          artifacts: state.artifacts,
          summary: state.summary,
          runId: state.runId,
          routeName: state.route?.name || '',
          runStatus: state.runStatus,
        }),
      }
    case 'hydrate':
      return {
        ...initialState,
        ...action.state,
        isLoading: false,
        status: action.state.messages.length ? 'completed' : 'idle',
        statusMessage: action.state.statusMessage || (action.state.messages.length ? '已恢复上一轮会话。' : '等待输入'),
      }
    case 'run_failed':
      return {
        ...state,
        isLoading: false,
        status: 'error',
        statusMessage: action.message,
        messages: [...state.messages, makeAssistantMessage(action.message, 'warning')],
      }
    case 'reset':
      return {
        ...initialState,
        conversationId: action.conversationId,
      }
    default:
      return state
  }
}

export function useAgentChat(settings: ClientSettings) {
  const [state, dispatch] = useReducer(reducer, initialState, () => readStoredAgentState() ?? initialState)
  const [runHistory, setRunHistory] = useState<RunRecordSummary[]>([])
  const hasAttemptedServerRestoreRef = useRef(false)

  const refreshRunHistory = useCallback(async () => {
    try {
      const response = await getRuns(settings)
      setRunHistory(response.runs)
    } catch {
      setRunHistory([])
    }
  }, [settings])

  useEffect(() => {
    void refreshRunHistory()
  }, [refreshRunHistory])

  useEffect(() => {
    persistAgentState(state)
  }, [state])

  const loadResultHtml = useCallback(
    async (response: AgentRunResponse) => {
      if (!responseCarriesRenderableArtifact(response)) {
        return
      }
      if (response.html_content) {
        dispatch({ type: 'result_html_loaded', htmlContent: response.html_content })
        return
      }
      const htmlArtifact = response.artifacts.find((artifact) => artifact.kind === 'html' && (artifact.url || artifact.path))
      const html = htmlArtifact
        ? await getArtifactText(settings, htmlArtifact.url || htmlArtifact.path || '')
        : await getRunResultHtml(settings, response.run_id)
      dispatch({ type: 'result_html_loaded', htmlContent: html })
    },
    [settings],
  )

  const waitForRunTerminalStatus = useCallback(
    async (runId: string): Promise<RunRecordSummary> => {
      const deadline = Date.now() + Math.max(settings.requestTimeoutMs, 15 * 60 * 1000)
      let latestRecord: RunRecordSummary | null = null
      let lastError = ''

      while (!latestRecord || !TERMINAL_RUN_STATUSES.has(latestRecord.status)) {
        if (Date.now() > deadline) {
          if (lastError) {
            throw new Error(`当前长任务仍未完成，最后一次状态查询结果：${lastError}`)
          }
          throw new Error('当前长任务仍未完成。你可以稍后从左侧 Server runs 继续查看这轮结果。')
        }
        try {
          latestRecord = await getRunSummary(settings, runId)
          lastError = ''
        } catch (error) {
          const message = error instanceof Error ? error.message : '运行状态轮询失败。'
          if (!isTransientRunLookupError(message)) {
            throw error
          }
          lastError = message
        }
        await delay(2500)
      }

      return latestRecord
    },
    [settings],
  )

  const waitForJobTerminalResult = useCallback(
    async (jobId: string): Promise<RunRecordSummary> => {
      const deadline = Date.now() + Math.max(settings.requestTimeoutMs, 15 * 60 * 1000)

      while (Date.now() <= deadline) {
        const payload = await getAgentJobResult(settings, jobId)
        if (payload.ready && payload.run) {
          return payload.run
        }
        if (TERMINAL_RUN_STATUSES.has(payload.job.status) && !payload.run) {
          throw new Error(payload.job.error || `Job 已结束，但没有可加载的 run 结果：${payload.job.status}`)
        }
        dispatch({
          type: 'status_updated',
          message: payload.job.progress_message || '后台任务仍在执行，我正在继续等待结果。',
          status: 'streaming',
          isLoading: true,
        })
        await delay(2500)
      }

      throw new Error('当前后台任务仍未完成。你可以稍后从左侧 Server runs 继续查看结果。')
    },
    [settings],
  )

  const handleStreamEvent = useCallback(
    (event: AgentStreamEvent) => {
      dispatch({ type: 'stream_event', event })
      if (event.type === 'run_completed') {
        const response = event.payload.response as AgentRunResponse | undefined
        if (response && responseCarriesRenderableArtifact(response)) {
          void loadResultHtml(response).catch((error) => {
            dispatch({ type: 'run_failed', message: error instanceof Error ? error.message : '结果页面加载失败。' })
          })
        }
        void refreshRunHistory()
      }
    },
    [loadResultHtml, refreshRunHistory],
  )

  const sendMessage = useCallback(
    async (payload: AgentChatRequest) => {
      const payloadWithContext: AgentChatRequest = {
        ...payload,
        conversation_history: [],
        last_run_context: buildLastRunContext(state),
      }

      dispatch({ type: 'send_started', payload: payloadWithContext })
      let streamedRunId = ''
      let streamStarted = false
      let submittedJobId = ''
      const trackedHandleStreamEvent = (event: AgentStreamEvent) => {
        if (event.type === 'run_started' && event.run_id) {
          streamedRunId = event.run_id
          streamStarted = true
        }
        handleStreamEvent(event)
      }

      try {
        const job = await submitAgentChatJob(settings, payloadWithContext)
        submittedJobId = job.job_id
        dispatch({
          type: 'job_submitted',
          jobId: job.job_id,
          message: job.progress_message || '任务已进入后台队列，正在等待 worker 接管。',
        })
        await streamAgentChatJob(settings, job.job_id, trackedHandleStreamEvent)
        return
      } catch (_jobError) {
        if (submittedJobId) {
          dispatch({
            type: 'status_updated',
            message: streamStarted
              ? 'Job 事件流中断了，但任务已经启动。我正在继续轮询当前 run 状态。'
              : 'Job 事件流中断了，但任务已经提交。我正在继续轮询后台 job 结果。',
            status: 'streaming',
            isLoading: true,
          })
          try {
            const record = streamStarted && streamedRunId
              ? await waitForRunTerminalStatus(streamedRunId)
              : await waitForJobTerminalResult(submittedJobId)
            const response = responseFromRunRecord(record)
            dispatch({ type: 'response_received', response })
            if (responseCarriesRenderableArtifact(response) && hasHtmlArtifact(response)) {
              await loadResultHtml(response)
            }
            await refreshRunHistory()
            return
          } catch (pollError) {
            dispatch({
              type: 'run_failed',
              message: formatAgentRequestError(pollError instanceof Error ? pollError.message : '后台任务状态轮询失败。', settings.apiBaseUrl),
            })
            return
          }
        }

        dispatch({
          type: 'status_updated',
          message: '后台队列接口不可用，我正在回退到直接流式模式。',
          status: 'streaming',
          isLoading: true,
        })
      }

      try {
        await streamAgentChat(settings, payloadWithContext, trackedHandleStreamEvent)
      } catch (_streamError) {
        if (streamStarted && streamedRunId) {
          dispatch({
            type: 'status_updated',
            message: '流式连接中断了，但这轮任务已经启动。我正在继续轮询当前 run 状态。',
            status: 'streaming',
            isLoading: true,
          })
          try {
            const record = await waitForRunTerminalStatus(streamedRunId)
            const response = responseFromRunRecord(record)
            dispatch({ type: 'response_received', response })
            if (responseCarriesRenderableArtifact(response) && hasHtmlArtifact(response)) {
              await loadResultHtml(response)
            }
            await refreshRunHistory()
            return
          } catch (pollError) {
            dispatch({
              type: 'run_failed',
              message: formatAgentRequestError(pollError instanceof Error ? pollError.message : '运行状态轮询失败。', settings.apiBaseUrl),
            })
            return
          }
        }

        dispatch({
          type: 'status_updated',
          message: '流式输出失败，我正在回退到同步模式。',
          status: 'streaming',
          isLoading: true,
        })
        try {
          const response = await runAgentChat(settings, payloadWithContext)
          dispatch({ type: 'response_received', response })
          if (responseCarriesRenderableArtifact(response)) {
            await loadResultHtml(response)
          }
          await refreshRunHistory()
        } catch (fallbackError) {
          dispatch({
            type: 'run_failed',
            message: formatAgentRequestError(fallbackError instanceof Error ? fallbackError.message : '请求失败。', settings.apiBaseUrl),
          })
        }
      }
    },
    [handleStreamEvent, loadResultHtml, refreshRunHistory, settings, state, waitForJobTerminalResult, waitForRunTerminalStatus],
  )

  const loadRun = useCallback(
    async (runId: string) => {
      const record = await getRunSummary(settings, runId)
      const response = responseFromRunRecord(record)
      dispatch({ type: 'run_loaded', response })
      if (responseCarriesRenderableArtifact(response) && hasHtmlArtifact(response)) {
        await loadResultHtml(response)
      }
    },
    [loadResultHtml, settings],
  )

  const cancelCurrentRun = useCallback(async () => {
    if (state.jobId && state.isLoading) {
      await cancelJobRequest(settings, state.jobId)
      dispatch({ type: 'assistant_message', message: makeAssistantMessage('已发送停止请求，后端会取消当前后台任务。', 'warning') })
      await refreshRunHistory()
      return
    }
    if (!state.runId) {
      return
    }
    await cancelRunRequest(settings, state.runId)
    dispatch({ type: 'assistant_message', message: makeAssistantMessage('已发送停止请求，后端会在安全点取消当前运行。', 'warning') })
    await refreshRunHistory()
  }, [refreshRunHistory, settings, state.isLoading, state.jobId, state.runId])

  const resetConversation = useCallback(() => {
    dispatch({ type: 'reset', conversationId: createConversationId() })
  }, [])

  const restoreConversationFromServer = useCallback(
    async (conversationId: string) => {
      const snapshot = await getConversationSnapshot(settings, conversationId)
      const restoredState = buildStateFromConversationSnapshot(snapshot)
      dispatch({ type: 'hydrate', state: restoredState })
      if (snapshot.latest_run) {
        const latestResponse = responseFromRunRecord(snapshot.latest_run)
        if (responseCarriesRenderableArtifact(latestResponse) && hasHtmlArtifact(latestResponse)) {
          await loadResultHtml(latestResponse)
        }
      }
    },
    [loadResultHtml, settings],
  )

  useEffect(() => {
    if (hasAttemptedServerRestoreRef.current) {
      return
    }
    hasAttemptedServerRestoreRef.current = true
    const storedConversationId = readStoredConversationId()
    if (!storedConversationId || state.messages.length > 0 || state.runId) {
      return
    }
    void restoreConversationFromServer(storedConversationId).catch(() => {
      // ignore restore failures and keep local initial state
    })
  }, [restoreConversationFromServer, state.messages.length, state.runId])

  const liveProgress = useMemo<LiveProgressSnapshot | null>(() => {
    if (!state.isLoading) {
      return null
    }
    const visibleSteps = state.planSteps.slice(-6).map((step) => ({
      index: step.index,
      label: stepStatusLabel(step.tool_name, step.status === 'failed'),
      status: step.status,
    }))
    const total = state.planSteps.length
    const completed = state.planSteps.filter((step) => step.status === 'completed').length
    const running = state.planSteps.find((step) => step.status === 'running')
    const currentLabel = running
      ? stepStatusLabel(running.tool_name, false)
      : state.statusMessage || 'Agent 正在处理中…'
    const indeterminate = true
    const percent = null
    return {
      percent,
      completed,
      total: Math.max(total, 1),
      currentLabel,
      steps: visibleSteps,
      indeterminate,
    }
  }, [state.isLoading, state.planSteps, state.status, state.statusMessage])

  return useMemo(
    () => ({
      state,
      liveProgress,
      runHistory,
      sendMessage,
      loadRun,
      cancelCurrentRun,
      refreshRunHistory,
      resetConversation,
    }),
    [cancelCurrentRun, liveProgress, loadRun, refreshRunHistory, resetConversation, runHistory, sendMessage, state],
  )
}
