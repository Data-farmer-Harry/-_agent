import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { getLatestResultHtml, getRunResultHtml, runAgentChat, streamAgentChat } from '../../services/api'
import type {
  AgentChatRequest,
  AgentRunResponse,
  AgentStreamEvent,
  ArtifactRef,
  ClientSettings,
  PlanStep,
  TaskRoute,
  ToolObservation,
} from '../../types/api'

type RunStatus = 'idle' | 'loading-latest' | 'streaming' | 'loading-result' | 'falling-back' | 'completed' | 'error'
type MessageTone = 'normal' | 'status' | 'warning'

export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  tone: MessageTone
  content: string
  attachmentName?: string
}

interface ReviewState {
  passed: boolean | null
  summary: string
  confidence: number | null
  issues: string[]
  mode: string
}

interface AgentChatState {
  messages: ConversationMessage[]
  requestPayload: AgentChatRequest | null
  sourceImageDataUrl: string
  runId: string
  route: TaskRoute | null
  planSteps: PlanStep[]
  timeline: ToolObservation[]
  artifacts: ArtifactRef[]
  responseMetadata: Record<string, unknown>
  generatedCode: string
  stdout: string
  stderr: string
  terminationReason: string
  htmlContent: string
  isLoading: boolean
  status: RunStatus
  statusMessage: string
  review: ReviewState
}

function formatAgentRequestError(message: string, apiBaseUrl: string): string {
  const normalized = message.trim()

  if (/not found/i.test(normalized) || normalized.includes('{"detail":"Not Found"}')) {
    return `当前连接的后端 ${apiBaseUrl} 没有暴露 agent chat 接口（/api/agent/chat 或 /api/agent/chat/stream）。这通常表示前端仍连着旧端口或旧版本服务。`
  }

  if (/failed to fetch/i.test(normalized) || /network/i.test(normalized)) {
    return `当前无法连接到后端 ${apiBaseUrl}。请确认当前仓库的 backend 已启动，并且前端连接的是正确端口。`
  }

  return normalized
}

type Action =
  | { type: 'load_latest_started' }
  | { type: 'load_latest_succeeded'; htmlContent: string }
  | { type: 'load_latest_failed' }
  | { type: 'send_started'; payload: AgentChatRequest }
  | { type: 'assistant_message'; message: ConversationMessage }
  | { type: 'stream_event'; event: AgentStreamEvent }
  | { type: 'result_html_loaded'; htmlContent: string }
  | { type: 'fallback_started' }
  | { type: 'fallback_succeeded'; response: AgentRunResponse }
  | { type: 'result_html_failed'; message: string }
  | { type: 'run_failed'; message: string }
  | { type: 'run_finished'; message: string; success: boolean }

function createMessageId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function makeAssistantMessage(content: string, tone: MessageTone = 'normal'): ConversationMessage {
  return {
    id: createMessageId(),
    role: 'assistant',
    tone,
    content,
  }
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

function reviewFromObservation(observation: ToolObservation): ReviewState | null {
  if (observation.tool_name !== 'phase_diagram_result_review' && observation.tool_name !== 'phase_diagram_html_review') {
    return null
  }

  return {
    passed: Boolean(observation.output.review_passed),
    summary: String(observation.output.review_summary || ''),
    confidence: typeof observation.output.review_confidence === 'number' ? observation.output.review_confidence : null,
    issues: Array.isArray(observation.output.review_issues) ? observation.output.review_issues.map(String) : [],
    mode: String(observation.output.review_mode || ''),
  }
}

function reviewFromResponse(response: AgentRunResponse): ReviewState {
  const review = (response.metadata.review as Record<string, unknown> | undefined) || {}
  return {
    passed: typeof review.passed === 'boolean' ? review.passed : null,
    summary: String(review.summary || ''),
    confidence: typeof review.confidence === 'number' ? review.confidence : null,
    issues: Array.isArray(review.issues) ? review.issues.map(String) : [],
    mode: String(review.mode || ''),
  }
}

function applyAgentRunResponse(state: AgentChatState, response: AgentRunResponse): AgentChatState {
  return {
    ...state,
    runId: response.run_id || state.runId,
    route: response.route || state.route,
    planSteps: response.plan_steps || state.planSteps,
    timeline: response.trace || state.timeline,
    artifacts: response.artifacts || [],
    responseMetadata: response.metadata || {},
    generatedCode: response.generated_code || '',
    stdout: response.stdout || '',
    stderr: response.stderr || '',
    terminationReason: response.termination_reason || '',
    htmlContent: response.html_content || state.htmlContent,
    review: reviewFromResponse(response),
  }
}

function observationMessage(observation: ToolObservation): ConversationMessage | null {
  switch (observation.tool_name) {
    case 'phase_diagram_codegen':
      return makeAssistantMessage(
        observation.success ? '我已经生成了初版绘图代码，接下来调用 Python tool 执行出图。' : '代码生成阶段没有成功结束。',
        observation.success ? 'status' : 'warning',
      )
    case 'python_execute':
      return makeAssistantMessage(
        observation.success ? 'Python tool 已执行完成，接下来检查结果页结构和体系语义。' : '执行阶段报错，我会根据错误信息继续修复。',
        observation.success ? 'status' : 'warning',
      )
    case 'phase_diagram_result_review':
    case 'phase_diagram_html_review':
      return makeAssistantMessage(
        observation.success
          ? '当前结果通过了 agent 自检，产物结构和任务目标基本一致。'
          : '自检发现结果还不够稳妥，我会继续修复或把问题明确暴露出来。',
        observation.success ? 'status' : 'warning',
      )
    case 'phase_diagram_html_redraw':
      return makeAssistantMessage(
        observation.success ? '我已经生成了讲解/重绘页面，接下来会继续做页面级复核。' : 'HTML 重绘阶段没有成功完成。',
        observation.success ? 'status' : 'warning',
      )
    case 'phase_diagram_repair':
      return makeAssistantMessage(
        observation.success ? '我已经根据报错修复了代码，准备重新执行。' : '修复没有通过语义校验，后续会继续回退到更稳的方案。',
        observation.success ? 'status' : 'warning',
      )
    case 'phase_diagram_image_parse':
      return makeAssistantMessage(
        observation.success ? '我已经完成了相图截图的结构化识别，识别结果和保守结论已经写进当前 run。' : '截图解析没有成功完成。',
        observation.success ? 'status' : 'warning',
      )
    case 'phase_diagram_image_render':
      return makeAssistantMessage(
        observation.success ? '图片模式的页面已经重建完成，你可以直接在右侧检查原图和结果。' : '图片重建没有成功完成。',
        observation.success ? 'status' : 'warning',
      )
    case 'lammps_command_router':
      return makeAssistantMessage(
        observation.success
          ? '我判断这是一个 LAMMPS 方向的任务。目前先返回预留工具链，后续可以接入真实模拟。'
          : 'LAMMPS 路由阶段没有成功完成。',
        observation.success ? 'status' : 'warning',
      )
    default:
      return null
  }
}

const initialState: AgentChatState = {
  messages: [],
  requestPayload: null,
  sourceImageDataUrl: '',
  runId: '',
  route: null,
  planSteps: [],
  timeline: [],
  artifacts: [],
  responseMetadata: {},
  generatedCode: '',
  stdout: '',
  stderr: '',
  terminationReason: '',
  htmlContent: '',
  isLoading: false,
  status: 'idle',
  statusMessage: '待执行',
  review: {
    passed: null,
    summary: '',
    confidence: null,
    issues: [],
    mode: '',
  },
}

function reducer(state: AgentChatState, action: Action): AgentChatState {
  switch (action.type) {
    case 'load_latest_started':
      return {
        ...state,
        status: 'loading-latest',
        statusMessage: '正在加载最近一次结果…',
      }
    case 'load_latest_succeeded':
      return {
        ...state,
        htmlContent: action.htmlContent,
        status: 'idle',
        statusMessage: '已加载最近一次生成结果。',
      }
    case 'load_latest_failed':
      return {
        ...state,
        status: 'idle',
        statusMessage: '待执行',
      }
    case 'send_started':
      return {
        ...state,
        requestPayload: action.payload,
        sourceImageDataUrl: action.payload.image_data_url || '',
        runId: '',
        route: null,
        planSteps: [],
        timeline: [],
        artifacts: [],
        responseMetadata: {},
        generatedCode: '',
        stdout: '',
        stderr: '',
        terminationReason: '',
        htmlContent: '',
        isLoading: true,
        status: 'streaming',
        statusMessage: 'Agent 正在理解需求并选择工具…',
        review: { passed: null, summary: '', confidence: null, issues: [], mode: '' },
        messages: [
          ...state.messages,
          {
            id: createMessageId(),
            role: 'user',
            tone: 'normal',
            content: action.payload.message,
            attachmentName: action.payload.filename || undefined,
          },
          makeAssistantMessage('我先解析你的材料需求，选择合适的 route 和 tool，然后按步骤出图并自检。', 'status'),
        ],
      }
    case 'assistant_message':
      return {
        ...state,
        messages: [...state.messages, action.message],
      }
    case 'stream_event': {
      const { event } = action
      if (event.type === 'run_started') {
        const route = (event.payload.route as TaskRoute | undefined) || null
        const planSteps = ((event.payload.plan_steps as PlanStep[] | undefined) || []).map((step) => ({ ...step }))
        const toolNames = planSteps.map((step) => step.tool_name).join(' -> ')
        const decisionLabel = route?.intent || route?.name || '未命名任务'
        const decisionSource = route?.decision_source ? `（${route.decision_source}）` : ''
        return {
          ...state,
          runId: event.run_id,
          route,
          planSteps,
          status: 'streaming',
          statusMessage: 'Agent 已开始执行。',
          messages: route
            ? [
                ...state.messages,
                makeAssistantMessage(`我已将任务判定为 ${decisionLabel}${decisionSource}，路由到 ${route.name}，计划依次调用 ${toolNames || '暂无 tool'}。`, 'status'),
              ]
            : state.messages,
        }
      }

      if (event.type === 'step_started' || event.type === 'step_skipped') {
        const step = event.payload.step as PlanStep
        return {
          ...state,
          runId: event.run_id,
          planSteps: updateStep(state.planSteps, step),
          status: 'streaming',
          statusMessage:
            event.type === 'step_started'
              ? `正在执行 Step ${step.index} · ${step.tool_name}`
              : `已跳过 Step ${step.index} · ${step.tool_name}`,
        }
      }

      if (event.type === 'step_completed' || event.type === 'step_failed') {
        const step = event.payload.step as PlanStep
        const observation = event.payload.observation as ToolObservation
        const review = reviewFromObservation(observation)
        const assistantUpdate = observationMessage(observation)
        return {
          ...state,
          runId: event.run_id,
          planSteps: updateStep(state.planSteps, step),
          timeline: [...state.timeline, observation],
          status: 'streaming',
          statusMessage:
            event.type === 'step_completed'
              ? `已完成 Step ${step.index} · ${step.tool_name}`
              : `Step ${step.index} · ${step.tool_name} 执行失败。`,
          review: review || state.review,
          messages: assistantUpdate ? [...state.messages, assistantUpdate] : state.messages,
        }
      }

      if (event.type === 'run_completed' || event.type === 'run_error') {
        const response = event.payload.response as AgentRunResponse | undefined
        const nextState = response ? applyAgentRunResponse(state, response) : state
        const terminalMessage =
          response?.final_message ||
          (typeof event.payload.message === 'string' ? event.payload.message : event.type === 'run_completed' ? '执行完成。' : '运行结束。')
        return {
          ...nextState,
          runId: event.run_id,
          status: 'loading-result',
          statusMessage: event.payload.html_ready ? '正在加载最终结果页…' : '正在整理最终结果…',
          messages: [...nextState.messages, makeAssistantMessage(terminalMessage, event.type === 'run_completed' ? 'status' : 'warning')],
        }
      }

      return state
    }
    case 'result_html_loaded':
      return {
        ...state,
        htmlContent: action.htmlContent,
      }
    case 'fallback_started':
      return {
        ...state,
        status: 'falling-back',
        statusMessage: '流式请求失败，正在回退到普通模式…',
        messages: [...state.messages, makeAssistantMessage('流式链路没有成功完成，我正在回退到同步执行模式。', 'warning')],
      }
    case 'fallback_succeeded':
      return applyAgentRunResponse(
        {
          ...state,
          status: 'completed',
          messages: [...state.messages, makeAssistantMessage(action.response.final_message || '同步执行完成。', action.response.success ? 'status' : 'warning')],
        },
        action.response,
      )
    case 'result_html_failed':
      return {
        ...state,
        stderr: state.stderr ? `${state.stderr}\n\n${action.message}` : action.message,
        messages: [...state.messages, makeAssistantMessage(action.message, 'warning')],
      }
    case 'run_failed':
      return {
        ...state,
        isLoading: false,
        status: 'error',
        statusMessage: '请求失败。',
        stderr: action.message,
        messages: [...state.messages, makeAssistantMessage(action.message, 'warning')],
      }
    case 'run_finished':
      return {
        ...state,
        isLoading: false,
        status: action.success ? 'completed' : 'error',
        statusMessage: action.message,
      }
    default:
      return state
  }
}

export function useAgentChat(settings: ClientSettings) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const stateRef = useRef(state)

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const loadLatestResult = useCallback(async () => {
    dispatch({ type: 'load_latest_started' })
    try {
      const latestHtml = await getLatestResultHtml(settings)
      dispatch({ type: 'load_latest_succeeded', htmlContent: latestHtml })
    } catch {
      dispatch({ type: 'load_latest_failed' })
    }
  }, [settings])

  const sendMessage = useCallback(
    async (payload: AgentChatRequest) => {
      dispatch({ type: 'send_started', payload })
      let streamRunId = ''
      let htmlReady = false
      let streamSucceeded = false
      let finalMessage = ''
      let resultHtmlLoadFailed = false

      try {
        await streamAgentChat(settings, payload, (event) => {
          streamRunId = event.run_id

          if (event.type === 'run_completed' || event.type === 'run_error') {
            const response = event.payload.response as AgentRunResponse | undefined
            htmlReady = Boolean(event.payload.html_ready)
            streamSucceeded = Boolean(response?.success)
            finalMessage = response?.final_message || finalMessage
          }

          dispatch({ type: 'stream_event', event })
        })
      } catch (streamError) {
        dispatch({ type: 'fallback_started' })
        try {
          const response = await runAgentChat(settings, payload)
          dispatch({ type: 'fallback_succeeded', response })
          dispatch({
            type: 'run_finished',
            success: response.success,
            message: response.success ? '同步执行完成。' : '同步执行完成，但结果未通过最终校验。',
          })
          return
        } catch (fallbackError) {
          const message =
            fallbackError instanceof Error
              ? fallbackError.message
              : streamError instanceof Error
                ? streamError.message
                : '请求失败。'
          dispatch({ type: 'run_failed', message: formatAgentRequestError(message, settings.apiBaseUrl) })
          return
        }
      }

      if (streamRunId && htmlReady) {
        try {
          const htmlContent = await getRunResultHtml(settings, streamRunId)
          dispatch({ type: 'result_html_loaded', htmlContent })
        } catch (error) {
          resultHtmlLoadFailed = true
          const message = error instanceof Error ? error.message : '最终 HTML 结果加载失败。'
          dispatch({ type: 'result_html_failed', message })
        }
      }

      const current = stateRef.current
      dispatch({
        type: 'run_finished',
        success: streamSucceeded,
        message:
          finalMessage ||
          (streamSucceeded && !resultHtmlLoadFailed && !current.stderr ? '执行完成。' : '执行结束，请查看 tool 调用、自检和结果页。'),
      })
    },
    [settings],
  )

  return useMemo(
    () => ({
      state,
      loadLatestResult,
      sendMessage,
    }),
    [loadLatestResult, sendMessage, state],
  )
}
