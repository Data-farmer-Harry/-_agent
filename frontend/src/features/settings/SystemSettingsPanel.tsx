import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Save, Wrench, X } from 'lucide-react'

import { getLammpsConfig, getLlmConfig, getSystemDiagnostics, updateLammpsConfig, updateLlmConfig } from '../../services/api'
import type { ClientSettings, LammpsRuntimeConfig, LlmRuntimeConfig, SystemDiagnosticsResponse } from '../../types/api'

interface SystemSettingsPanelProps {
  open: boolean
  settings: ClientSettings
  connectionStatus: 'resolving' | 'ready' | 'offline'
  onClose: () => void
  onUpdateClientSettings: (patch: Partial<ClientSettings>) => void
  onRefreshConnection: (nextSettings?: ClientSettings) => Promise<void>
}

function SettingField({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <label className="block min-w-0 space-y-2">
      <div className="min-w-0 space-y-1">
        <p className="break-words text-[13px] font-semibold leading-5 text-slate-800">{label}</p>
        {description ? <p className="break-words text-xs leading-5 text-slate-500">{description}</p> : null}
      </div>
      {children}
    </label>
  )
}

function inputClassName() {
  return 'min-w-0 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm leading-5 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100'
}

function checkboxLabelClassName() {
  return 'flex min-w-0 items-start justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm leading-5 text-slate-700 [&>span]:min-w-0 [&>span]:flex-1 [&>span]:break-words [&>input]:mt-0.5 [&>input]:shrink-0'
}

function sectionHeaderClassName() {
  return 'mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between'
}

function sectionActionClassName(color: 'slate' | 'indigo' | 'emerald') {
  const tone = color === 'indigo'
    ? 'bg-indigo-600 hover:bg-indigo-700'
    : color === 'emerald'
      ? 'bg-emerald-600 hover:bg-emerald-700'
      : 'bg-slate-900 hover:bg-slate-800'
  return `inline-flex shrink-0 items-center justify-center gap-2 self-start whitespace-nowrap rounded-xl px-3.5 py-2.5 text-xs font-semibold text-white transition disabled:opacity-60 ${tone}`
}

function diagnosticPill(status: 'ok' | 'warning' | 'error' | 'unknown') {
  if (status === 'ok') return 'bg-emerald-50 text-emerald-700 border-emerald-100'
  if (status === 'warning') return 'bg-amber-50 text-amber-700 border-amber-100'
  if (status === 'error') return 'bg-rose-50 text-rose-700 border-rose-100'
  return 'bg-slate-100 text-slate-600 border-slate-200'
}

function diagnosticLabel(status: 'ok' | 'warning' | 'error' | 'unknown') {
  if (status === 'ok') return '正常'
  if (status === 'warning') return '警告'
  if (status === 'error') return '阻塞'
  return '未知'
}

function compactValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length > 6 ? `${value.slice(0, 6).join(', ')} 等 ${value.length} 项` : value.join(', ')
  }
  if (typeof value === 'object' && value !== null) {
    return JSON.stringify(value)
  }
  return String(value)
}

export function SystemSettingsPanel({
  open,
  settings,
  connectionStatus,
  onClose,
  onUpdateClientSettings,
  onRefreshConnection,
}: SystemSettingsPanelProps) {
  const [localSettings, setLocalSettings] = useState<ClientSettings>(settings)
  const [llmConfig, setLlmConfig] = useState<LlmRuntimeConfig | null>(null)
  const [lammpsConfig, setLammpsConfig] = useState<LammpsRuntimeConfig | null>(null)
  const [diagnostics, setDiagnostics] = useState<SystemDiagnosticsResponse | null>(null)
  const [apiKeyDraft, setApiKeyDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [savingSection, setSavingSection] = useState<'client' | 'llm' | 'lammps' | null>(null)
  const [feedback, setFeedback] = useState('')

  useEffect(() => {
    setLocalSettings(settings)
  }, [settings])

  useEffect(() => {
    if (!open) {
      return
    }
    let cancelled = false
    setLoading(true)
    setFeedback('')
    void Promise.all([getLlmConfig(settings), getLammpsConfig(settings), getSystemDiagnostics(settings)])
      .then(([nextLlmConfig, nextLammpsConfig, nextDiagnostics]) => {
        if (cancelled) {
          return
        }
        setLlmConfig(nextLlmConfig)
        setLammpsConfig(nextLammpsConfig)
        setDiagnostics(nextDiagnostics)
      })
      .catch((error) => {
        if (cancelled) {
          return
        }
        setFeedback(error instanceof Error ? error.message : '系统设置加载失败。')
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [open, settings])

  const connectionLabel = useMemo(() => {
    if (connectionStatus === 'ready') {
      return '已连接'
    }
    if (connectionStatus === 'offline') {
      return '离线'
    }
    return '连接中'
  }, [connectionStatus])

  const diagnosticSummary = useMemo(() => {
    const checks = diagnostics?.checks || []
    return {
      ok: checks.filter((check) => check.status === 'ok').length,
      warning: checks.filter((check) => check.status === 'warning').length,
      error: checks.filter((check) => check.status === 'error').length,
      total: checks.length,
    }
  }, [diagnostics])

  if (!open) {
    return null
  }

  const handleSaveClient = async () => {
    setSavingSection('client')
    setFeedback('')
    try {
      onUpdateClientSettings(localSettings)
      await onRefreshConnection(localSettings)
      setFeedback('客户端连接设置已更新。')
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : '客户端设置更新失败。')
    } finally {
      setSavingSection(null)
    }
  }

  const handleSaveLlm = async () => {
    if (!llmConfig) {
      return
    }
    setSavingSection('llm')
    setFeedback('')
    try {
      const next = await updateLlmConfig(settings, {
        ...llmConfig,
        llm_api_key: apiKeyDraft.trim() || undefined,
      })
      setLlmConfig(next)
      setApiKeyDraft('')
      setFeedback('LLM 配置已更新。')
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'LLM 配置更新失败。')
    } finally {
      setSavingSection(null)
    }
  }

  const handleSaveLammps = async () => {
    if (!lammpsConfig) {
      return
    }
    setSavingSection('lammps')
    setFeedback('')
    try {
      const next = await updateLammpsConfig(settings, lammpsConfig)
      setLammpsConfig(next)
      setFeedback('运行时路径与 LAMMPS 配置已更新。')
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : 'LAMMPS 配置更新失败。')
    } finally {
      setSavingSection(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/35 backdrop-blur-[2px]" role="dialog" aria-modal="true" aria-labelledby="settings-panel-title">
      <div className="flex h-full w-full max-w-[720px] flex-col border-l border-slate-200 bg-slate-50 shadow-2xl">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-slate-200 bg-white px-4 py-4 sm:px-6 sm:py-5">
          <div className="min-w-0 space-y-1">
            <p className="text-[11px] font-bold uppercase tracking-[0.24em] text-slate-400">System Settings</p>
            <h2 id="settings-panel-title" className="break-words text-xl font-semibold leading-7 text-slate-900">运行与软件配置</h2>
            <p className="max-w-xl break-words text-sm leading-6 text-slate-500">调整后端连接、模型参数与本地软件路径。设置按功能分区，保存时只更新当前区域。</p>
          </div>
          <button aria-label="关闭系统设置" onClick={onClose} className="shrink-0 rounded-xl border border-slate-200 bg-white p-2.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav aria-label="设置分区" className="shrink-0 border-b border-slate-200 bg-white px-4 py-2 sm:px-6">
          <div className="grid grid-cols-2 gap-1.5 sm:flex">
            {[
              ['settings-health', '健康检查'],
              ['settings-client', '客户端'],
              ['settings-llm', 'LLM 与 Python'],
              ['settings-lammps', 'LAMMPS / OVITO'],
            ].map(([target, label]) => (
              <a
                key={target}
                href={`#${target}`}
                className="whitespace-nowrap rounded-lg px-3 py-2 text-center text-xs font-semibold text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
              >
                {label}
              </a>
            ))}
          </div>
        </nav>

        <div className="flex-1 scroll-smooth overflow-y-auto px-4 py-5 sm:px-6">
          <div className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-xs font-semibold text-slate-700">当前后端连接</p>
                <p className="mt-1 break-all font-mono text-xs leading-5 text-slate-500">{settings.apiBaseUrl}</p>
              </div>
              <span className={`w-fit shrink-0 rounded-full px-3 py-1 text-[11px] font-semibold ${connectionStatus === 'ready' ? 'bg-emerald-50 text-emerald-700' : connectionStatus === 'offline' ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'}`}>
                {connectionLabel}
              </span>
            </div>
          </div>

          {feedback ? (
            <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-700">
              {feedback}
            </div>
          ) : null}

          <div className="space-y-5">
            <section id="settings-health" className="scroll-mt-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
              <div className={sectionHeaderClassName()}>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold leading-6 text-slate-900">系统健康检查</h3>
                  <p className="mt-1 break-words text-xs leading-5 text-slate-500">检查配置中心、LLM/视觉、Embedding、RAG、LAMMPS、OVITO、SQLite、Artifact 与 Benchmark。</p>
                </div>
                <button
                  onClick={() => void getSystemDiagnostics(settings).then((nextDiagnostics) => {
                    setDiagnostics(nextDiagnostics)
                    setFeedback('系统健康检查已完成。')
                  }).catch((error) => {
                    setFeedback(error instanceof Error ? error.message : '系统健康检查失败。')
                  })}
                  className="inline-flex shrink-0 items-center justify-center gap-2 self-start whitespace-nowrap rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                  data-testid="system-health-check-button"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  立即检查
                </button>
              </div>
              {loading && !diagnostics ? (
                <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> 正在检测本地环境…</div>
              ) : diagnostics ? (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="system-health-summary">
                    <div className={`rounded-2xl border px-3 py-3 ${diagnosticPill(diagnostics.overall_status)}`}>
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-70">Overall</p>
                      <p className="mt-1 text-sm font-bold">{diagnosticLabel(diagnostics.overall_status)}</p>
                    </div>
                    <div className="rounded-2xl border border-emerald-100 bg-emerald-50 px-3 py-3 text-emerald-700">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-70">OK</p>
                      <p className="mt-1 text-sm font-bold">{diagnosticSummary.ok}/{diagnosticSummary.total}</p>
                    </div>
                    <div className="rounded-2xl border border-amber-100 bg-amber-50 px-3 py-3 text-amber-700">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-70">Warn</p>
                      <p className="mt-1 text-sm font-bold">{diagnosticSummary.warning}</p>
                    </div>
                    <div className="rounded-2xl border border-rose-100 bg-rose-50 px-3 py-3 text-rose-700">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-70">Error</p>
                      <p className="mt-1 text-sm font-bold">{diagnosticSummary.error}</p>
                    </div>
                  </div>
                  {diagnostics.checks.map((check) => (
                    <div key={check.name} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3" data-testid="system-health-check-card">
                      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <div className="flex min-w-0 items-start gap-2">
                            {check.status === 'ok' ? (
                              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                            ) : check.status === 'warning' || check.status === 'error' ? (
                              <AlertTriangle className={`mt-0.5 h-4 w-4 shrink-0 ${check.status === 'error' ? 'text-rose-600' : 'text-amber-600'}`} />
                            ) : (
                              <Wrench className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
                            )}
                            <p className="min-w-0 break-words text-sm font-semibold leading-5 text-slate-800">{check.name}</p>
                          </div>
                          <p className="mt-2 break-words text-xs leading-5 text-slate-600">{check.summary}</p>
                        </div>
                        <span className={`w-fit shrink-0 rounded-full border px-3 py-1 text-[11px] font-semibold ${diagnosticPill(check.status)}`}>
                          {check.status}
                        </span>
                      </div>
                      {Object.keys(check.details || {}).length > 0 ? (
                        <details className="mt-3 rounded-xl border border-slate-200 bg-white p-3 text-[11px] text-slate-500">
                          <summary className="cursor-pointer font-semibold text-slate-500">查看诊断细节</summary>
                          <div className="mt-3 grid gap-2">
                          {Object.entries(check.details).map(([key, value]) => (
                            <div key={key} className="grid min-w-0 gap-1 sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-3">
                              <span className="break-words font-semibold uppercase tracking-[0.12em] text-slate-400">{key}</span>
                              <span className="min-w-0 break-all leading-5 text-slate-600">{compactValue(value)}</span>
                            </div>
                          ))}
                          </div>
                        </details>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </section>

            <section id="settings-client" className="scroll-mt-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
              <div className={sectionHeaderClassName()}>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold leading-6 text-slate-900">客户端连接</h3>
                  <p className="mt-1 break-words text-xs leading-5 text-slate-500">这些设置只保存在浏览器本地，适合切换机器或后端地址。</p>
                </div>
                <button
                  onClick={() => void handleSaveClient()}
                  disabled={savingSection !== null}
                  className={sectionActionClassName('slate')}
                >
                  {savingSection === 'client' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  保存客户端
                </button>
              </div>
              <div className="space-y-4">
                <SettingField label="API Base URL" description="前端要连接的后端地址。Windows 部署时可改成本机 IP 或局域网地址。">
                  <input
                    className={inputClassName()}
                    value={localSettings.apiBaseUrl}
                    onChange={(event) => setLocalSettings((current) => ({ ...current, apiBaseUrl: event.target.value }))}
                    placeholder="http://127.0.0.1:8000"
                  />
                </SettingField>
                <SettingField label="请求超时（毫秒）" description="长任务如 LAMMPS 可适当调大。">
                  <input
                    className={inputClassName()}
                    type="number"
                    min={1000}
                    step={1000}
                    value={localSettings.requestTimeoutMs}
                    onChange={(event) => setLocalSettings((current) => ({ ...current, requestTimeoutMs: Number(event.target.value) || 0 }))}
                  />
                </SettingField>
              </div>
            </section>

            <section id="settings-llm" className="scroll-mt-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
              <div className={sectionHeaderClassName()}>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold leading-6 text-slate-900">LLM 与 Python</h3>
                  <p className="mt-1 break-words text-xs leading-5 text-slate-500">这里的修改会实时映射到后端运行时配置。</p>
                </div>
                <button
                  onClick={() => void handleSaveLlm()}
                  disabled={savingSection !== null || !llmConfig}
                  className={sectionActionClassName('indigo')}
                >
                  {savingSection === 'llm' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  保存 LLM
                </button>
              </div>
              {loading && !llmConfig ? (
                <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> 正在加载 LLM 配置…</div>
              ) : llmConfig ? (
                <div className="space-y-4">
                  <label className={checkboxLabelClassName()}>
                    <span>启用 LLM</span>
                    <input type="checkbox" checked={llmConfig.llm_enabled} onChange={(event) => setLlmConfig({ ...llmConfig, llm_enabled: event.target.checked })} />
                  </label>
                  <label className={checkboxLabelClassName()}>
                    <span>所有 agent 必须真实调用 LLM</span>
                    <input type="checkbox" checked={llmConfig.require_llm_for_agents} onChange={(event) => setLlmConfig({ ...llmConfig, require_llm_for_agents: event.target.checked })} />
                  </label>
                  <label className={checkboxLabelClassName()}>
                    <span>关闭模型思考模式</span>
                    <input type="checkbox" checked={!llmConfig.llm_enable_thinking} onChange={(event) => setLlmConfig({ ...llmConfig, llm_enable_thinking: !event.target.checked })} />
                  </label>
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
                    <p className="mb-2 text-xs font-semibold text-slate-700">模型能力声明</p>
                    <p className="mb-3 text-[11px] leading-5 text-slate-500">健康检查按这里的声明判断能力，不会再因为仅配置了 API Key 就误判视觉或原生 embedding 可用。</p>
                    <div className="grid gap-2">
                      <label className={checkboxLabelClassName()}>
                        <span>支持文本对话</span>
                        <input type="checkbox" checked={llmConfig.llm_supports_chat} onChange={(event) => setLlmConfig({ ...llmConfig, llm_supports_chat: event.target.checked })} />
                      </label>
                      <label className={checkboxLabelClassName()}>
                        <span>支持图片 / 视觉输入</span>
                        <input type="checkbox" checked={llmConfig.llm_supports_vision} onChange={(event) => setLlmConfig({ ...llmConfig, llm_supports_vision: event.target.checked })} />
                      </label>
                      <label className={checkboxLabelClassName()}>
                        <span>聊天模型原生支持 embedding</span>
                        <input type="checkbox" checked={llmConfig.llm_supports_embedding} onChange={(event) => setLlmConfig({ ...llmConfig, llm_supports_embedding: event.target.checked })} />
                      </label>
                    </div>
                  </div>
                  <SettingField label="Python 可执行文件" description="相图 wrapper 会通过这个 Python 路径执行。Windows 上可改为 venv 中的 python.exe。">
                    <input className={inputClassName()} value={llmConfig.python_executable} onChange={(event) => setLlmConfig({ ...llmConfig, python_executable: event.target.value })} />
                  </SettingField>
                  <SettingField label="LLM Base URL">
                    <input className={inputClassName()} value={llmConfig.llm_api_base_url} onChange={(event) => setLlmConfig({ ...llmConfig, llm_api_base_url: event.target.value })} />
                  </SettingField>
                  <SettingField label="LLM Model">
                    <input className={inputClassName()} value={llmConfig.llm_model} onChange={(event) => setLlmConfig({ ...llmConfig, llm_model: event.target.value })} />
                  </SettingField>
                  <SettingField label="API Key" description={llmConfig.api_key_set ? `当前已设置：${llmConfig.api_key_masked}` : '当前未设置 API Key'}>
                    <input className={inputClassName()} type="password" value={apiKeyDraft} onChange={(event) => setApiKeyDraft(event.target.value)} placeholder="留空表示不修改" />
                  </SettingField>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <SettingField label="请求超时（秒）">
                      <input className={inputClassName()} type="number" value={llmConfig.llm_request_timeout_seconds} onChange={(event) => setLlmConfig({ ...llmConfig, llm_request_timeout_seconds: Number(event.target.value) || 0 })} />
                    </SettingField>
                    <SettingField label="最大 Token">
                      <input className={inputClassName()} type="number" value={llmConfig.llm_max_tokens} onChange={(event) => setLlmConfig({ ...llmConfig, llm_max_tokens: Number(event.target.value) || 0 })} />
                    </SettingField>
                    <SettingField label="最大重试次数">
                      <input className={inputClassName()} type="number" value={llmConfig.llm_request_max_retries} onChange={(event) => setLlmConfig({ ...llmConfig, llm_request_max_retries: Number(event.target.value) || 0 })} />
                    </SettingField>
                    <SettingField label="重试退避（秒）">
                      <input className={inputClassName()} type="number" step="0.1" value={llmConfig.llm_retry_backoff_seconds} onChange={(event) => setLlmConfig({ ...llmConfig, llm_retry_backoff_seconds: Number(event.target.value) || 0 })} />
                    </SettingField>
                  </div>
                </div>
              ) : null}
            </section>

            <section id="settings-lammps" className="scroll-mt-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
              <div className={sectionHeaderClassName()}>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold leading-6 text-slate-900">LAMMPS / OVITO 路径</h3>
                  <p className="mt-1 break-words text-xs leading-5 text-slate-500">在 Windows 或不同机器上切换可执行文件和资源目录。</p>
                </div>
                <button
                  onClick={() => void handleSaveLammps()}
                  disabled={savingSection !== null || !lammpsConfig}
                  className={sectionActionClassName('emerald')}
                >
                  {savingSection === 'lammps' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  保存运行时路径
                </button>
              </div>
              {loading && !lammpsConfig ? (
                <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> 正在加载 LAMMPS 配置…</div>
              ) : lammpsConfig ? (
                <div className="space-y-4">
                  <SettingField label="LAMMPS 可执行文件">
                    <input className={inputClassName()} value={lammpsConfig.lammps_command} onChange={(event) => setLammpsConfig({ ...lammpsConfig, lammps_command: event.target.value })} />
                    <p className="break-words text-xs leading-5 text-slate-500">检测状态：{lammpsConfig.lammps_command_exists ? '已找到' : '未找到'}</p>
                  </SettingField>
                  <SettingField label="势函数目录">
                    <input className={inputClassName()} value={lammpsConfig.potentials_dir} onChange={(event) => setLammpsConfig({ ...lammpsConfig, potentials_dir: event.target.value })} />
                    <p className="break-words text-xs leading-5 text-slate-500">检测状态：{lammpsConfig.potentials_dir_exists ? '已找到' : '未找到'}</p>
                  </SettingField>
                  <SettingField label="OVITO 路径" description="可以填 OVITO 可执行文件路径，或让后端自动检测。Windows 上建议显式指定。">
                    <input className={inputClassName()} value={lammpsConfig.ovito_location} onChange={(event) => setLammpsConfig({ ...lammpsConfig, ovito_location: event.target.value })} />
                    <p className="break-all text-xs leading-5 text-slate-500">当前后端识别：{lammpsConfig.ovito_available ? `${lammpsConfig.ovito_backend} · ${lammpsConfig.ovito_location}` : '未检测到 OVITO'}</p>
                  </SettingField>
                  <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-xs leading-5 text-emerald-800">
                    默认执行策略：所有普通 LAMMPS 请求都使用真实本地运行。执行环境异常时返回诊断，不会静默生成 mock 或 synthetic 结果。
                  </div>
                  <details className="group overflow-hidden rounded-xl border border-slate-200 bg-slate-50/70">
                    <summary className="cursor-pointer list-none px-3 py-2.5 text-xs font-semibold text-slate-600 [&::-webkit-details-marker]:hidden">
                      开发与演示模式（默认关闭）
                    </summary>
                    <div className="grid gap-3 border-t border-slate-200 p-3 sm:grid-cols-2">
                      <label className={checkboxLabelClassName()}>
                        <span>仅在请求明确要求 Mock 时允许 fallback</span>
                        <input type="checkbox" checked={lammpsConfig.allow_mock_fallback} onChange={(event) => setLammpsConfig({ ...lammpsConfig, allow_mock_fallback: event.target.checked })} />
                      </label>
                      <label className={checkboxLabelClassName()}>
                        <span>强制 mock（仅自动化测试）</span>
                        <input type="checkbox" checked={lammpsConfig.force_mock} onChange={(event) => setLammpsConfig({ ...lammpsConfig, force_mock: event.target.checked })} />
                      </label>
                    </div>
                  </details>
                  <SettingField label="最大重试次数">
                    <input className={inputClassName()} type="number" min={0} value={lammpsConfig.max_retries} onChange={(event) => setLammpsConfig({ ...lammpsConfig, max_retries: Number(event.target.value) || 0 })} />
                  </SettingField>
                </div>
              ) : null}
            </section>
          </div>
        </div>

        <div className="shrink-0 border-t border-slate-200 bg-white px-4 py-3 sm:px-6 sm:py-4">
          <button
            onClick={() => void Promise.all([onRefreshConnection(), getLlmConfig(settings), getLammpsConfig(settings), getSystemDiagnostics(settings)]).then(([_, nextLlmConfig, nextLammpsConfig, nextDiagnostics]) => {
              setLlmConfig(nextLlmConfig)
              setLammpsConfig(nextLammpsConfig)
              setDiagnostics(nextDiagnostics)
              setFeedback('配置与环境诊断已重新加载。')
            }).catch((error) => {
              setFeedback(error instanceof Error ? error.message : '配置刷新失败。')
            })}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 sm:w-auto"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新加载当前配置
          </button>
        </div>
      </div>
    </div>
  )
}
