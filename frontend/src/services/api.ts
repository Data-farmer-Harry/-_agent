import type { AgentStreamEvent, ClientSettings, DiagramRequest, GenerateAndRunResponse, GenerateResponse, RunCodeRequest, ExecutionResult } from '../types/api'

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

export async function streamGenerateAndRun(
  settings: ClientSettings,
  payload: DiagramRequest,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), settings.requestTimeoutMs)

  try {
    const response = await fetch(buildUrl(settings.apiBaseUrl, '/api/generate-and-run/stream'), {
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
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() || ''

      for (const chunk of chunks) {
        const eventLine = chunk.split('\n').find((line) => line.startsWith('event: '))
        const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '))
        if (!eventLine || !dataLine) continue
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
