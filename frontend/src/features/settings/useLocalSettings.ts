import { useCallback, useEffect, useMemo, useState } from 'react'

import { probeAgentBackend } from '../../services/api'
import type { ClientSettings } from '../../types/api'

const IS_DESKTOP_BUILD = import.meta.env.VITE_DESKTOP_BUILD === 'true'
const DEFAULT_AGENT_API_BASE_URL = IS_DESKTOP_BUILD
  ? window.location.origin
  : (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000')
const LOCAL_SETTINGS_KEY = 'materials-agent-client-settings'

const defaultSettings: ClientSettings = {
  apiBaseUrl: DEFAULT_AGENT_API_BASE_URL,
  requestTimeoutMs: Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS || 900000),
}

type ApiConnectionStatus = 'resolving' | 'ready' | 'offline'

interface ApiConnectionState {
  status: ApiConnectionStatus
  message: string
  resolvedApiBaseUrl: string
}

function readStoredSettings(): ClientSettings {
  if (IS_DESKTOP_BUILD) {
    return defaultSettings
  }
  try {
    const raw = window.localStorage.getItem(LOCAL_SETTINGS_KEY)
    if (!raw) {
      return defaultSettings
    }
    const parsed = JSON.parse(raw) as Partial<ClientSettings>
    return {
      apiBaseUrl: typeof parsed.apiBaseUrl === 'string' && parsed.apiBaseUrl.trim() ? parsed.apiBaseUrl.trim() : defaultSettings.apiBaseUrl,
      requestTimeoutMs:
        typeof parsed.requestTimeoutMs === 'number' && Number.isFinite(parsed.requestTimeoutMs)
          ? parsed.requestTimeoutMs
          : defaultSettings.requestTimeoutMs,
    }
  } catch {
    return defaultSettings
  }
}

export function useLocalSettings() {
  const [settings, setSettings] = useState<ClientSettings>(defaultSettings)
  const [apiConnection, setApiConnection] = useState<ApiConnectionState>({
    status: 'resolving',
    message: '正在连接 backend…',
    resolvedApiBaseUrl: defaultSettings.apiBaseUrl,
  })

  useEffect(() => {
    setSettings(readStoredSettings())
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(settings))
    } catch {
      // ignore localStorage failures
    }
  }, [settings])

  const refreshApiConnection = useCallback(async (nextSettings?: ClientSettings) => {
    const effective = nextSettings || settings
    setApiConnection({
      status: 'resolving',
      message: '正在连接 backend…',
      resolvedApiBaseUrl: effective.apiBaseUrl,
    })

    const probe = await probeAgentBackend(effective.apiBaseUrl, effective.requestTimeoutMs)
    if (probe.healthAvailable) {
      setApiConnection({
        status: 'ready',
        message: `已连接 backend：${probe.apiBaseUrl}`,
        resolvedApiBaseUrl: probe.apiBaseUrl,
      })
      return
    }

    setApiConnection({
      status: 'offline',
      message: `后端 ${effective.apiBaseUrl} 不可用。请确认 backend 已启动，并检查 API 地址。`,
      resolvedApiBaseUrl: effective.apiBaseUrl,
    })
  }, [settings])

  useEffect(() => {
    void refreshApiConnection(settings)
  }, [refreshApiConnection, settings])

  const updateSettings = useCallback((patch: Partial<ClientSettings>) => {
    setSettings((current) => ({
      ...current,
      ...patch,
    }))
  }, [])

  return useMemo(
    () => ({
      settings,
      apiConnection,
      updateSettings,
      refreshApiConnection,
    }),
    [apiConnection, refreshApiConnection, settings, updateSettings],
  )
}
