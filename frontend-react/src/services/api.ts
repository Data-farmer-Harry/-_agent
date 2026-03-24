import type {
  AgentCatalogResponse,
  AgentChatRequest,
  AgentManifestResponse,
  AgentRunResponse,
  AgentStreamEvent,
  ClientSettings,
  DiagramRequest,
  ImageDiagramRequest,
  GenerateAndRunResponse,
  GenerateResponse,
  HealthResponse,
  RunCodeRequest,
  ExecutionResult,
} from '../types/api'

function buildUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

export interface AgentBackendProbe {
  apiBaseUrl: string
  manifestAvailable: boolean
  catalogAvailable: boolean
  healthAvailable: boolean
  score: number
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

async function requestJsonProbe(
  url: string,
  timeoutMs: number,
  validator: (payload: unknown) => boolean,
): Promise<boolean> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })

    if (!response.ok) {
      return false
    }

    const contentType = response.headers.get('content-type') || ''
    if (!contentType.toLowerCase().includes('application/json')) {
      return false
    }

    const payload = (await response.json()) as unknown
    return validator(payload)
  } catch {
    return false
  } finally {
    window.clearTimeout(timeoutId)
  }
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

export async function getHealth(settings: ClientSettings): Promise<HealthResponse> {
  return requestJson<HealthResponse>(buildUrl(settings.apiBaseUrl, '/api/health'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function probeAgentBackend(apiBaseUrl: string, timeoutMs: number): Promise<AgentBackendProbe> {
  const normalizedBaseUrl = apiBaseUrl.replace(/\/$/, '')
  const probeTimeoutMs = Math.max(500, Math.min(timeoutMs, 2500))
  const [manifestAvailable, catalogAvailable, healthAvailable] = await Promise.all([
    requestJsonProbe(buildUrl(normalizedBaseUrl, '/api/agent/manifest'), probeTimeoutMs, (payload) => {
      return isObjectRecord(payload) && Array.isArray(payload.routes) && Array.isArray(payload.tools)
    }),
    requestJsonProbe(buildUrl(normalizedBaseUrl, '/api/agent/catalog'), probeTimeoutMs, (payload) => {
      return isObjectRecord(payload) && Array.isArray(payload.workspaces) && Array.isArray(payload.tools)
    }),
    requestJsonProbe(buildUrl(normalizedBaseUrl, '/api/health'), probeTimeoutMs, (payload) => {
      return isObjectRecord(payload) && payload.status === 'ok'
    }),
  ])

  return {
    apiBaseUrl: normalizedBaseUrl,
    manifestAvailable,
    catalogAvailable,
    healthAvailable,
    score: (manifestAvailable ? 4 : 0) + (catalogAvailable ? 2 : 0) + (healthAvailable ? 1 : 0),
  }
}

export async function getAgentCatalog(settings: ClientSettings): Promise<AgentCatalogResponse> {
  return requestJson<AgentCatalogResponse>(buildUrl(settings.apiBaseUrl, '/api/agent/catalog'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function getAgentManifest(settings: ClientSettings): Promise<AgentManifestResponse> {
  return requestJson<AgentManifestResponse>(buildUrl(settings.apiBaseUrl, '/api/agent/manifest'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function generateCode(settings: ClientSettings, payload: DiagramRequest): Promise<GenerateResponse> {
  return requestJson<GenerateResponse>(
    buildUrl(settings.apiBaseUrl, settings.generatePath),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    settings.requestTimeoutMs,
  )
}

export async function runCode(settings: ClientSettings, payload: RunCodeRequest): Promise<ExecutionResult> {
  return requestJson<ExecutionResult>(
    buildUrl(settings.apiBaseUrl, settings.runPath),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    settings.requestTimeoutMs,
  )
}

export async function generateAndRun(settings: ClientSettings, payload: DiagramRequest): Promise<GenerateAndRunResponse> {
  return requestJson<GenerateAndRunResponse>(
    buildUrl(settings.apiBaseUrl, settings.generateAndRunPath),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    settings.requestTimeoutMs,
  )
}

export async function generatePhaseDiagramFromImage(settings: ClientSettings, payload: ImageDiagramRequest): Promise<GenerateAndRunResponse> {
  return requestJson<GenerateAndRunResponse>(
    buildUrl(settings.apiBaseUrl, '/api/phase-diagram/from-image'),
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
    settings.requestTimeoutMs,
  )
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

export async function streamAgentChat(
  settings: ClientSettings,
  payload: AgentChatRequest,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), settings.requestTimeoutMs)

  try {
    const response = await fetch(buildUrl(settings.apiBaseUrl, '/api/agent/chat/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
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

        const event = JSON.parse(dataLine.slice(6)) as AgentStreamEvent
        onEvent({ ...event, type: eventLine.slice(7) as AgentStreamEvent['type'] })
      }
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`请求超时（${settings.requestTimeoutMs} ms）`)
    }

    if (error instanceof Error) {
      throw error
    }

    throw new Error('请求失败。')
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export async function streamGenerateAndRun(
  settings: ClientSettings,
  payload: DiagramRequest,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), settings.requestTimeoutMs)
  const streamPath = `${settings.generateAndRunPath.replace(/\/$/, '')}/stream`

  try {
    const response = await fetch(buildUrl(settings.apiBaseUrl, streamPath), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
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

        const event = JSON.parse(dataLine.slice(6)) as AgentStreamEvent
        onEvent({ ...event, type: eventLine.slice(7) as AgentStreamEvent['type'] })
      }
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`请求超时（${settings.requestTimeoutMs} ms）`)
    }

    if (error instanceof Error) {
      throw error
    }

    throw new Error('请求失败。')
  } finally {
    window.clearTimeout(timeoutId)
  }
}

export async function getLatestResultHtml(settings: ClientSettings): Promise<string> {
  return requestText(buildUrl(settings.apiBaseUrl, '/api/latest-result'), { method: 'GET' }, settings.requestTimeoutMs)
}

export async function getRunResultHtml(settings: ClientSettings, runId: string): Promise<string> {
  return requestText(buildUrl(settings.apiBaseUrl, `/api/runs/${runId}/result`), { method: 'GET' }, settings.requestTimeoutMs)
}
