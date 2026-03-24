import { reactive, watch } from 'vue'
import type { ClientSettings } from '../types/api'

const STORAGE_KEY = 'phase-diagram-agent-settings'

const defaultSettings: ClientSettings = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8001',
  generatePath: import.meta.env.VITE_GENERATE_PATH || '/api/generate',
  runPath: import.meta.env.VITE_RUN_PATH || '/api/run',
  generateAndRunPath: import.meta.env.VITE_GENERATE_AND_RUN_PATH || '/api/generate-and-run',
  requestTimeoutMs: Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS || 120000),
  enableAutoRetry: String(import.meta.env.VITE_ENABLE_AUTO_RETRY || 'false') === 'true',
}

function loadStoredSettings(): Partial<ClientSettings> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as Partial<ClientSettings>
    if (parsed.apiBaseUrl === 'http://localhost:8000' || parsed.apiBaseUrl === 'http://127.0.0.1:8000') {
      parsed.apiBaseUrl = defaultSettings.apiBaseUrl
    }
    return parsed
  } catch {
    return {}
  }
}

export function useLocalSettings() {
  const settings = reactive<ClientSettings>({
    ...defaultSettings,
    ...loadStoredSettings(),
  })

  watch(
    settings,
    (value) => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
    },
    { deep: true },
  )

  function resetSettings() {
    Object.assign(settings, defaultSettings)
  }

  return {
    settings,
    resetSettings,
    defaultSettings,
  }
}
