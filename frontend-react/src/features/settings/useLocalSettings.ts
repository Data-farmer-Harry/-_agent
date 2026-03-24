import { useCallback, useEffect, useMemo, useState } from 'react'
import { probeAgentBackend } from '../../services/api'
import type { ClientSettings } from '../../types/api'

const STORAGE_KEY = 'phase-diagram-agent-react-settings'
const FIXED_AGENT_API_BASE_URL = 'http://127.0.0.1:8000'
const ALLOWED_AGENT_API_BASE_URLS = ['http://127.0.0.1:8000', 'http://localhost:8000']

const defaultSettings: ClientSettings = {
  apiBaseUrl: FIXED_AGENT_API_BASE_URL,
  generatePath: import.meta.env.VITE_GENERATE_PATH || '/api/generate',
  runPath: import.meta.env.VITE_RUN_PATH || '/api/run',
  generateAndRunPath: import.meta.env.VITE_GENERATE_AND_RUN_PATH || '/api/generate-and-run',
  requestTimeoutMs: Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS || 120000),
  enableAutoRetry: String(import.meta.env.VITE_ENABLE_AUTO_RETRY || 'false') === 'true',
}

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/$/, '')
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map(normalizeBaseUrl).filter(Boolean)))
}

function isAllowedAgentApiBaseUrl(value: string): boolean {
  return ALLOWED_AGENT_API_BASE_URLS.includes(normalizeBaseUrl(value))
}

function coerceApiBaseUrl(value: string | undefined): string {
  if (value && isAllowedAgentApiBaseUrl(value)) {
    return normalizeBaseUrl(value)
  }
  return FIXED_AGENT_API_BASE_URL
}

function buildCandidateApiBaseUrls(preferredBaseUrl: string, fallbackBaseUrl: string): string[] {
  return uniqueStrings([coerceApiBaseUrl(preferredBaseUrl), coerceApiBaseUrl(fallbackBaseUrl), ...ALLOWED_AGENT_API_BASE_URLS])
}

function loadStoredSettings(): Partial<ClientSettings> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as Partial<ClientSettings>
    parsed.apiBaseUrl = coerceApiBaseUrl(typeof parsed.apiBaseUrl === 'string' ? parsed.apiBaseUrl : undefined)

    return parsed
  } catch {
    return {}
  }
}

type ApiConnectionStatus = 'resolving' | 'ready' | 'agent-unavailable' | 'offline'

interface ApiConnectionState {
  status: ApiConnectionStatus
  message: string
  resolvedApiBaseUrl: string
  checkedBaseUrls: string[]
}

export function useLocalSettings() {
  const [settings, setSettings] = useState<ClientSettings>(() => ({
    ...defaultSettings,
    ...loadStoredSettings(),
  }))
  const [apiConnection, setApiConnection] = useState<ApiConnectionState>({
    status: 'resolving',
    message: '正在探测支持 agent 的后端…',
    resolvedApiBaseUrl: normalizeBaseUrl(defaultSettings.apiBaseUrl),
    checkedBaseUrls: [],
  })

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  }, [settings])

  useEffect(() => {
    let cancelled = false

    const resolveApiBaseUrl = async () => {
      setApiConnection((current) => ({
        ...current,
        status: 'resolving',
        message: '正在探测支持 agent 的后端…',
      }))

      const candidates = buildCandidateApiBaseUrls(settings.apiBaseUrl, defaultSettings.apiBaseUrl)
      const probes = await Promise.all(candidates.map((candidate) => probeAgentBackend(candidate, settings.requestTimeoutMs)))
      if (cancelled) {
        return
      }

      const bestProbe = [...probes].sort((left, right) => right.score - left.score)[0]
      const currentBaseUrl = normalizeBaseUrl(settings.apiBaseUrl)

      if (!bestProbe || bestProbe.score === 0) {
        setApiConnection({
          status: 'offline',
          message: `固定后端 ${FIXED_AGENT_API_BASE_URL} 不可用。请确认当前仓库的 backend 已在 8000 端口启动。`,
          resolvedApiBaseUrl: FIXED_AGENT_API_BASE_URL,
          checkedBaseUrls: candidates,
        })
        return
      }

      if (bestProbe.apiBaseUrl !== currentBaseUrl) {
        setSettings((current) => ({
          ...current,
          apiBaseUrl: bestProbe.apiBaseUrl,
        }))
      }

      if (bestProbe.manifestAvailable || bestProbe.catalogAvailable) {
        setApiConnection({
          status: 'ready',
          message:
            bestProbe.apiBaseUrl === currentBaseUrl
              ? `已连接固定 agent 后端：${bestProbe.apiBaseUrl}`
              : `已切换回固定 agent 后端：${bestProbe.apiBaseUrl}`,
          resolvedApiBaseUrl: bestProbe.apiBaseUrl,
          checkedBaseUrls: candidates,
        })
        return
      }

      setApiConnection({
        status: 'agent-unavailable',
        message: `固定后端 ${bestProbe.apiBaseUrl} 可以访问，但缺少 agent 接口。请确认 8000 端口跑的是当前仓库的 backend。`,
        resolvedApiBaseUrl: bestProbe.apiBaseUrl,
        checkedBaseUrls: candidates,
      })
    }

    void resolveApiBaseUrl()

    return () => {
      cancelled = true
    }
  }, [settings.apiBaseUrl, settings.requestTimeoutMs])

  const updateSettings = useCallback((next: Partial<ClientSettings>) => {
    setSettings((current) => ({
      ...current,
      ...next,
      apiBaseUrl: coerceApiBaseUrl(typeof next.apiBaseUrl === 'string' ? next.apiBaseUrl : current.apiBaseUrl),
    }))
  }, [])

  const resetSettings = useCallback(() => {
    setSettings(defaultSettings)
  }, [])

  return useMemo(
    () => ({ settings, updateSettings, resetSettings, defaultSettings, apiConnection }),
    [apiConnection, resetSettings, settings, updateSettings],
  )
}
