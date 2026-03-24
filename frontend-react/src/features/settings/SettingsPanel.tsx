import type { ChangeEvent } from 'react'
import type { ClientSettings } from '../../types/api'

interface SettingsPanelProps {
  settings: ClientSettings
  disabled?: boolean
  onChange: (patch: Partial<ClientSettings>) => void
  onReset: () => void
}

function parseNumber(value: string, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function SettingsPanel({ settings, disabled = false, onChange, onReset }: SettingsPanelProps) {
  const updateText = (key: 'apiBaseUrl' | 'generatePath' | 'runPath' | 'generateAndRunPath') => (event: ChangeEvent<HTMLInputElement>) => {
    onChange({ [key]: event.target.value })
  }

  return (
    <section className="panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>运行设置</h2>
          <p>继续使用 localStorage 持久化，让工作台在演示、联调和本地实验之间切换时保持稳定配置。</p>
        </div>
        <span className="badge">localStorage</span>
      </div>

      <div className="field-grid two-columns">
        <label className="field">
          <span>API Base URL</span>
          <input value={settings.apiBaseUrl} type="text" onChange={updateText('apiBaseUrl')} disabled={disabled} />
        </label>

        <label className="field">
          <span>Generate Path</span>
          <input value={settings.generatePath} type="text" onChange={updateText('generatePath')} disabled={disabled} />
        </label>

        <label className="field">
          <span>Run Path</span>
          <input value={settings.runPath} type="text" onChange={updateText('runPath')} disabled={disabled} />
        </label>

        <label className="field">
          <span>Generate &amp; Run Path</span>
          <input value={settings.generateAndRunPath} type="text" onChange={updateText('generateAndRunPath')} disabled={disabled} />
        </label>

        <label className="field">
          <span>Timeout (ms)</span>
          <input
            value={settings.requestTimeoutMs}
            type="number"
            min="1000"
            step="1000"
            onChange={(event) => onChange({ requestTimeoutMs: parseNumber(event.target.value, settings.requestTimeoutMs) })}
            disabled={disabled}
          />
        </label>

        <label className="toggle-row">
          <span>启用自动重试</span>
          <input
            checked={settings.enableAutoRetry}
            type="checkbox"
            onChange={(event) => onChange({ enableAutoRetry: event.target.checked })}
            disabled={disabled}
          />
        </label>
      </div>

      <button className="secondary-button" type="button" onClick={onReset} disabled={disabled}>
        恢复默认配置
      </button>
    </section>
  )
}
