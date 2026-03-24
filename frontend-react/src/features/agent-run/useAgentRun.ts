import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { generateAndRun, generatePhaseDiagramFromImage, getLatestResultHtml, getRunResultHtml, streamGenerateAndRun } from '../../services/api'
import type {
  AgentRunResponse,
  AgentStreamEvent,
  ClientSettings,
  DiagramRequest,
  GenerateAndRunResponse,
  ImageDiagramRequest,
  PlanStep,
  TaskRoute,
  ToolObservation,
} from '../../types/api'

type RunStatus = 'idle' | 'loading-latest' | 'streaming' | 'loading-result' | 'falling-back' | 'completed' | 'error'

interface AgentRunSessionState {
  requestPayload: DiagramRequest | null
  imageRequestPayload: ImageDiagramRequest | null
  sourceImageDataUrl: string
  runId: string
  route: TaskRoute | null
  planSteps: PlanStep[]
  timeline: ToolObservation[]
  generatedCode: string
  stdout: string
  stderr: string
  terminationReason: string
  htmlContent: string
  isLoading: boolean
  status: RunStatus
  statusMessage: string
}

type Action =
  | { type: 'load_latest_started' }
  | { type: 'load_latest_succeeded'; htmlContent: string }
  | { type: 'load_latest_failed' }
  | { type: 'run_started'; payload: DiagramRequest }
  | { type: 'image_run_started'; payload: ImageDiagramRequest }
  | { type: 'stream_event'; event: AgentStreamEvent }
  | { type: 'result_html_loaded'; htmlContent: string }
  | { type: 'fallback_started' }
  | { type: 'fallback_succeeded'; response: GenerateAndRunResponse }
  | { type: 'result_html_failed'; message: string }
  | { type: 'run_failed'; message: string }
  | { type: 'run_finished'; message: string }

const initialState: AgentRunSessionState = {
  requestPayload: null,
  imageRequestPayload: null,
  sourceImageDataUrl: '',
  runId: '',
  route: null,
  planSteps: [],
  timeline: [],
  generatedCode: '',
  stdout: '',
  stderr: '',
  terminationReason: '',
  htmlContent: '',
  isLoading: false,
  status: 'idle',
  statusMessage: '待执行',
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

function applyGenerateAndRunResponse(state: AgentRunSessionState, response: GenerateAndRunResponse): AgentRunSessionState {
  return {
    ...state,
    runId: response.run_id || state.runId,
    route: response.route
      ? {
          name: response.route,
          workspace_id: response.workspace_id || 'generic',
          reason: response.route_reason || '',
          selected_tool: response.selected_tool || undefined,
          available_tools: response.available_tools || [],
          reserved_tools: response.reserved_tools || [],
          entry_tool: response.entry_tool || undefined,
          input_channels: response.input_channels || [],
          deliverable: response.deliverable,
          narrative: response.narrative,
        }
      : state.route,
    planSteps: response.plan_steps || state.planSteps,
    timeline: response.trace || state.timeline,
    generatedCode: response.generated_code || '',
    stdout: response.stdout || '',
    stderr: response.stderr || '',
    terminationReason: response.termination_reason || '',
    htmlContent: response.html_content || state.htmlContent,
  }
}

function reducer(state: AgentRunSessionState, action: Action): AgentRunSessionState {
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
    case 'run_started':
      return {
        ...state,
        requestPayload: action.payload,
        imageRequestPayload: null,
        sourceImageDataUrl: '',
        runId: '',
        route: null,
        planSteps: [],
        timeline: [],
        generatedCode: '',
        stdout: '',
        stderr: '',
        terminationReason: '',
        isLoading: true,
        status: 'streaming',
        statusMessage: '正在生成代码并执行…',
      }
    case 'image_run_started':
      return {
        ...state,
        requestPayload: null,
        imageRequestPayload: action.payload,
        sourceImageDataUrl: action.payload.image_data_url,
        runId: '',
        route: null,
        planSteps: [],
        timeline: [],
        generatedCode: '',
        stdout: '',
        stderr: '',
        terminationReason: '',
        htmlContent: '',
        isLoading: true,
        status: 'loading-result',
        statusMessage: '正在解析图片并生成页面…',
      }
    case 'stream_event': {
      const { event } = action
      if (event.type === 'run_started') {
        return {
          ...state,
          runId: event.run_id,
          route: (event.payload.route as TaskRoute | undefined) || null,
          planSteps: ((event.payload.plan_steps as PlanStep[] | undefined) || []).map((step) => ({ ...step })),
          status: 'streaming',
          statusMessage: 'Agent 已开始规划与执行。',
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
        }
      }

      if (event.type === 'run_completed' || event.type === 'run_error') {
        const response = event.payload.response as AgentRunResponse | undefined
        const nextState = response
          ? {
              ...state,
              runId: response.run_id || state.runId,
              route: response.route || state.route,
              planSteps: response.plan_steps || state.planSteps,
              timeline: response.trace || state.timeline,
              generatedCode: response.generated_code || '',
              stdout: response.stdout || '',
              stderr: response.stderr || '',
              terminationReason: response.termination_reason || '',
            }
          : state
        return {
          ...nextState,
          runId: event.run_id,
          status: 'loading-result',
          statusMessage: event.type === 'run_completed' ? '执行结束，正在加载最终结果…' : '运行结束，正在整理结果…',
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
      }
    case 'fallback_succeeded':
      return applyGenerateAndRunResponse(
        {
          ...state,
          status: 'completed',
        },
        action.response,
      )
    case 'result_html_failed':
      return {
        ...state,
        stderr: state.stderr ? `${state.stderr}\n\n${action.message}` : action.message,
      }
    case 'run_failed':
      return {
        ...state,
        runId: '',
        route: null,
        planSteps: [],
        timeline: [],
        generatedCode: '',
        stdout: '',
        stderr: action.message,
        terminationReason: '',
        htmlContent: '',
        isLoading: false,
        status: 'error',
        statusMessage: '请求失败。',
      }
    case 'run_finished':
      return {
        ...state,
        isLoading: false,
        status: state.status === 'error' ? 'error' : 'completed',
        statusMessage: action.message,
      }
    default:
      return state
  }
}

export function useAgentRun(settings: ClientSettings) {
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

  const run = useCallback(
    async (payload: DiagramRequest) => {
      dispatch({ type: 'run_started', payload })
      let streamRunId = ''
      let terminalStderr = ''
      let streamRunErrored = false
      let resultHtmlLoadFailed = false

      try {
        await streamGenerateAndRun(settings, payload, (event) => {
          streamRunId = event.run_id

          if (event.type === 'run_completed' || event.type === 'run_error') {
            const response = event.payload.response as GenerateAndRunResponse | undefined
            terminalStderr = response?.stderr || ''
            streamRunErrored = event.type === 'run_error'
          }

          dispatch({ type: 'stream_event', event })
        })
      } catch (streamError) {
        dispatch({ type: 'fallback_started' })

        try {
          const response = await generateAndRun(settings, payload)
          dispatch({ type: 'fallback_succeeded', response })
          dispatch({
            type: 'run_finished',
            message: response.success ? '已回退到普通模式并执行完成。' : '已回退到普通模式，执行失败，请查看日志。',
          })
          return
        } catch (fallbackError) {
          const message =
            fallbackError instanceof Error
              ? fallbackError.message
              : streamError instanceof Error
                ? streamError.message
                : '请求失败。'
          dispatch({ type: 'run_failed', message })
          return
        }
      }

      if (streamRunId) {
        try {
          const htmlContent = await getRunResultHtml(settings, streamRunId)
          dispatch({ type: 'result_html_loaded', htmlContent })
        } catch (error) {
          const message = error instanceof Error ? error.message : '最终 HTML 结果加载失败。'
          resultHtmlLoadFailed = true
          dispatch({ type: 'result_html_failed', message })
        }
      }

      const current = stateRef.current
      dispatch({
        type: 'run_finished',
        message:
          streamRunErrored || resultHtmlLoadFailed || terminalStderr || current.stderr
            ? '执行结束，请查看 agent 过程与日志。'
            : '执行完成。',
      })
    },
    [settings],
  )

  const runFromImage = useCallback(
    async (payload: ImageDiagramRequest) => {
      dispatch({ type: 'image_run_started', payload })

      try {
        const response = await generatePhaseDiagramFromImage(settings, payload)
        dispatch({ type: 'fallback_succeeded', response })
        dispatch({
          type: 'run_finished',
          message: response.success ? '图片已解析并生成页面。' : '图片解析失败，请查看日志。',
        })
      } catch (error) {
        const message = error instanceof Error ? error.message : '图片解析请求失败。'
        dispatch({ type: 'run_failed', message })
      }
    },
    [settings],
  )

  return useMemo(
    () => ({
      state,
      loadLatestResult,
      run,
      runFromImage,
    }),
    [loadLatestResult, run, runFromImage, state],
  )
}
