import type {
  AgentChatRequest,
  AgentJobRecord,
  AgentJobResultResponse,
  AgentJobResumeRequest,
  AgentJobResumeResponse,
  AgentRunResponse,
  AgentStreamEvent,
  ClientSettings,
  ConversationSnapshotResponse,
  HealthResponse,
  LammpsRuntimeConfig,
  LlmRuntimeConfig,
  PromptSuggestionRequest,
  PromptSuggestionResponse,
  RunListResponse,
  RunRecordSummary,
  SystemDiagnosticsResponse,
} from '../types/api'

function buildUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

async function requestJson<T>(url: string, init: RequestInit, timeoutMs: number): Promise<T> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init.headers || {}),
      },
      signal: controller.signal,
    })

    if (!response.ok) {
      const message = await response.text()
      throw new Error(message || `HTTP ${response.status}`)
    }

    return (await response.json()) as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`请求超时（${timeoutMs} ms）`)
    }
    if (error instanceof Error) {
      throw error
    }
    throw new Error('请求失败。')
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function requestText(url: string, init: RequestInit, timeoutMs: number): Promise<string> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      ...init,
      headers: {
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers || {}),
      },
      signal: controller.signal,
    })

    if (!response.ok) {
      const message = await response.text()
      throw new Error(message || `HTTP ${response.status}`)
    }

    return await response.text()
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export interface AgentBackendProbe {
  apiBaseUrl: string
  healthAvailable: boolean
  score: number
}

export async function probeAgentBackend(apiBaseUrl: string, timeoutMs: number): Promise<AgentBackendProbe> {
  try {
    await requestJson<HealthResponse>(buildUrl(apiBaseUrl, '/api/health'), { method: 'GET' }, Math.min(timeoutMs, 3000))
    return { apiBaseUrl, healthAvailable: true, score: 1 }
  } catch {
    return { apiBaseUrl, healthAvailable: false, score: 0 }
  }
}

export async function runAgentChat(settings: ClientSettings, payload: AgentChatRequest): Promise<AgentRunResponse> {
  return requestJson<AgentRunResponse>(
    buildUrl(settings.apiBaseUrl, '/api/agent/chat'),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    settings.requestTimeoutMs,
  )
}

export async function submitAgentChatJob(settings: ClientSettings, payload: AgentChatRequest): Promise<AgentJobRecord> {
  return requestJson<AgentJobRecord>(
    buildUrl(settings.apiBaseUrl, '/api/jobs/agent-chat'),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    Math.min(settings.requestTimeoutMs, 15000),
  )
}

export async function getAgentJobResult(settings: ClientSettings, jobId: string): Promise<AgentJobResultResponse> {
  return requestJson<AgentJobResultResponse>(
    buildUrl(settings.apiBaseUrl, `/api/jobs/${jobId}/result`),
    { method: 'GET' },
    Math.min(settings.requestTimeoutMs, 15000),
  )
}

export async function cancelJobRequest(settings: ClientSettings, jobId: string): Promise<AgentJobRecord> {
  return requestJson<AgentJobRecord>(
    buildUrl(settings.apiBaseUrl, `/api/jobs/${jobId}/cancel`),
    { method: 'POST' },
    Math.min(settings.requestTimeoutMs, 15000),
  )
}

export async function resumeJobRequest(
  settings: ClientSettings,
  jobId: string,
  payload: AgentJobResumeRequest,
): Promise<AgentJobResumeResponse> {
  return requestJson<AgentJobResumeResponse>(
    buildUrl(settings.apiBaseUrl, `/api/jobs/${jobId}/resume`),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    Math.min(settings.requestTimeoutMs, 15000),
  )
}

export async function requestPromptSuggestion(
  settings: ClientSettings,
  payload: PromptSuggestionRequest,
): Promise<PromptSuggestionResponse> {
  return requestJson<PromptSuggestionResponse>(
    buildUrl(settings.apiBaseUrl, '/api/agent/prompt-suggestion'),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    settings.requestTimeoutMs,
  )
}

export async function getRunResultHtml(settings: ClientSettings, runId: string): Promise<string> {
  return requestText(buildUrl(settings.apiBaseUrl, `/api/runs/${runId}/result`), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function getRuns(settings: ClientSettings): Promise<RunListResponse> {
  return requestJson<RunListResponse>(buildUrl(settings.apiBaseUrl, '/api/runs'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function getLlmConfig(settings: ClientSettings): Promise<LlmRuntimeConfig> {
  return requestJson<LlmRuntimeConfig>(buildUrl(settings.apiBaseUrl, '/api/config/llm'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function updateLlmConfig(settings: ClientSettings, payload: Partial<LlmRuntimeConfig> & { llm_api_key?: string }): Promise<LlmRuntimeConfig> {
  return requestJson<LlmRuntimeConfig>(
    buildUrl(settings.apiBaseUrl, '/api/config/llm'),
    { method: 'POST', body: JSON.stringify(payload) },
    settings.requestTimeoutMs,
  )
}

export async function getLammpsConfig(settings: ClientSettings): Promise<LammpsRuntimeConfig> {
  return requestJson<LammpsRuntimeConfig>(buildUrl(settings.apiBaseUrl, '/api/config/lammps'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function getSystemDiagnostics(settings: ClientSettings): Promise<SystemDiagnosticsResponse> {
  return requestJson<SystemDiagnosticsResponse>(
    buildUrl(settings.apiBaseUrl, '/api/system/diagnostics'),
    { method: 'GET' },
    settings.requestTimeoutMs,
  )
}

export async function updateLammpsConfig(settings: ClientSettings, payload: Partial<LammpsRuntimeConfig>): Promise<LammpsRuntimeConfig> {
  return requestJson<LammpsRuntimeConfig>(
    buildUrl(settings.apiBaseUrl, '/api/config/lammps'),
    { method: 'POST', body: JSON.stringify(payload) },
    settings.requestTimeoutMs,
  )
}

export async function getRunSummary(settings: ClientSettings, runId: string): Promise<RunRecordSummary> {
  return requestJson<RunRecordSummary>(buildUrl(settings.apiBaseUrl, `/api/runs/${runId}`), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function deleteRunRequest(settings: ClientSettings, runId: string): Promise<{ run_id: string; deleted: boolean }> {
  return requestJson<{ run_id: string; deleted: boolean }>(
    buildUrl(settings.apiBaseUrl, `/api/runs/${runId}`),
    { method: 'DELETE' },
    settings.requestTimeoutMs,
  )
}

export async function deleteConversationRequest(
  settings: ClientSettings,
  conversationId: string,
): Promise<{ conversation_id: string; deleted: boolean; deleted_runs: number; deleted_memory: boolean }> {
  return requestJson<{ conversation_id: string; deleted: boolean; deleted_runs: number; deleted_memory: boolean }>(
    buildUrl(settings.apiBaseUrl, `/api/conversations/${conversationId}`),
    { method: 'DELETE' },
    settings.requestTimeoutMs,
  )
}

export async function getConversationSnapshot(settings: ClientSettings, conversationId: string): Promise<ConversationSnapshotResponse> {
  return requestJson<ConversationSnapshotResponse>(
    buildUrl(settings.apiBaseUrl, `/api/conversations/${conversationId}`),
    { method: 'GET' },
    settings.requestTimeoutMs,
  )
}

export async function cancelRunRequest(settings: ClientSettings, runId: string): Promise<{ run_id: string; status: string }> {
  return requestJson<{ run_id: string; status: string }>(
    buildUrl(settings.apiBaseUrl, `/api/runs/${runId}/cancel`),
    { method: 'POST' },
    settings.requestTimeoutMs,
  )
}

export async function getArtifactText(settings: ClientSettings, artifactUrl: string): Promise<string> {
  const url = artifactUrl.startsWith('http') ? artifactUrl : buildUrl(settings.apiBaseUrl, artifactUrl)
  return requestText(url, { method: 'GET' }, settings.requestTimeoutMs)
}

export function resolveArtifactUrl(settings: ClientSettings, artifactUrl: string | null | undefined): string {
  if (!artifactUrl) {
    return ''
  }
  return artifactUrl.startsWith('http') ? artifactUrl : buildUrl(settings.apiBaseUrl, artifactUrl)
}

export async function streamAgentChat(
  settings: ClientSettings,
  payload: AgentChatRequest,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  try {
    const response = await fetch(buildUrl(settings.apiBaseUrl, '/api/agent/chat/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    if (!response.ok) {
      const message = await response.text()
      throw new Error(message || `HTTP ${response.status}`)
    }

    if (!response.body) {
      throw new Error('流式响应不可用。')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() || ''

      for (const chunk of chunks) {
        const lines = chunk.split('\n')
        const eventLine = lines.find((line) => line.startsWith('event: '))
        const dataLine = lines.find((line) => line.startsWith('data: '))
        if (!eventLine || !dataLine) {
          continue
        }
        const parsed = JSON.parse(dataLine.slice(6)) as AgentStreamEvent
        onEvent({ ...parsed, type: eventLine.slice(7) as AgentStreamEvent['type'] })
      }
    }
  } catch (error) {
    if (error instanceof Error) {
      throw error
    }
    throw new Error('流式请求失败。')
  }
}

export async function streamAgentChatJob(
  settings: ClientSettings,
  jobId: string,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  try {
    const response = await fetch(buildUrl(settings.apiBaseUrl, `/api/jobs/${jobId}/events`), {
      method: 'GET',
    })

    if (!response.ok) {
      const message = await response.text()
      throw new Error(message || `HTTP ${response.status}`)
    }

    if (!response.body) {
      throw new Error('Job 流式响应不可用。')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() || ''

      for (const chunk of chunks) {
        const lines = chunk.split('\n')
        const eventLine = lines.find((line) => line.startsWith('event: '))
        const dataLine = lines.find((line) => line.startsWith('data: '))
        if (!eventLine || !dataLine) {
          continue
        }
        const parsed = JSON.parse(dataLine.slice(6)) as AgentStreamEvent
        onEvent({ ...parsed, type: eventLine.slice(7) as AgentStreamEvent['type'] })
      }
    }
  } catch (error) {
    if (error instanceof Error) {
      throw error
    }
    throw new Error('Job 流式请求失败。')
  }
}
