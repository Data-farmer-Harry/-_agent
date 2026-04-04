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
    <label className="block space-y-2">
      <div className="space-y-1">
        <p className="text-xs font-semibold text-slate-700">{label}</p>
        {description ? <p className="text-[11px] leading-5 text-slate-500">{description}</p> : null}
      </div>
      {children}
    </label>
  )
}

function inputClassName() {
  return 'w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100'
}

function checkboxLabelClassName() {
  return 'flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700'
}

function diagnosticPill(status: 'ok' | 'warning' | 'error' | 'unknown') {
  if (status === 'ok') return 'bg-emerald-50 text-emerald-700 border-emerald-100'
  if (status === 'warning') return 'bg-amber-50 text-amber-700 border-amber-100'
  if (status === 'error') return 'bg-rose-50 text-rose-700 border-rose-100'
  return 'bg-slate-100 text-slate-600 border-slate-200'
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
    <div className="absolute inset-0 z-50 flex justify-end bg-slate-950/30 backdrop-blur-[2px]">
      <div className="flex h-full w-[min(560px,100%)] flex-col border-l border-slate-200 bg-slate-50 shadow-2xl">
        <div className="flex items-start justify-between border-b border-slate-200 bg-white px-6 py-5">
          <div className="space-y-1">
            <p className="text-[11px] font-bold uppercase tracking-[0.24em] text-slate-400">System Settings</p>
            <h2 className="text-xl font-semibold text-slate-900">运行与软件配置</h2>
            <p className="text-sm text-slate-500">在这里调整后端连接、LLM 参数和本地软件路径，适配不同系统环境。</p>
          </div>
          <button onClick={onClose} className="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:border-slate-300 hover:text-slate-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="mb-4 rounded-2xl border border-slate-200 bg-white px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-slate-700">当前后端连接</p>
                <p className="text-xs text-slate-500">{settings.apiBaseUrl}</p>
              </div>
              <span className={`rounded-full px-3 py-1 text-[11px] font-semibold ${connectionStatus === 'ready' ? 'bg-emerald-50 text-emerald-700' : connectionStatus === 'offline' ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'}`}>
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
            <section className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">环境诊断</h3>
                  <p className="text-xs text-slate-500">用于快速检查 LLM、Python、LAMMPS、OVITO 和热力学数据库是否真的可用。</p>
                </div>
                {diagnostics ? (
                  <span className={`rounded-full border px-3 py-1 text-[11px] font-semibold ${diagnosticPill(diagnostics.overall_status)}`}>
                    {diagnostics.overall_status === 'ok' ? '诊断通过' : diagnostics.overall_status === 'warning' ? '存在警告' : '存在阻塞问题'}
                  </span>
                ) : null}
              </div>
              {loading && !diagnostics ? (
                <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> 正在检测本地环境…</div>
              ) : diagnostics ? (
                <div className="space-y-3">
                  {diagnostics.checks.map((check) => (
                    <div key={check.name} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            {check.status === 'ok' ? (
                              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                            ) : check.status === 'warning' || check.status === 'error' ? (
                              <AlertTriangle className={`h-4 w-4 ${check.status === 'error' ? 'text-rose-600' : 'text-amber-600'}`} />
                            ) : (
                              <Wrench className="h-4 w-4 text-slate-500" />
                            )}
                            <p className="text-sm font-semibold text-slate-800">{check.name}</p>
                          </div>
                          <p className="mt-2 text-xs leading-5 text-slate-600">{check.summary}</p>
                        </div>
                        <span className={`shrink-0 rounded-full border px-3 py-1 text-[11px] font-semibold ${diagnosticPill(check.status)}`}>
                          {check.status}
                        </span>
                      </div>
                      {Object.keys(check.details || {}).length > 0 ? (
                        <div className="mt-3 grid gap-2 rounded-xl border border-slate-200 bg-white p-3 text-[11px] text-slate-500">
                          {Object.entries(check.details).map(([key, value]) => (
                            <div key={key} className="grid grid-cols-[120px_minmax(0,1fr)] gap-3">
                              <span className="font-semibold uppercase tracking-[0.16em] text-slate-400">{key}</span>
                              <span className="break-all text-slate-600">
                                {Array.isArray(value) ? value.join(', ') : typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">客户端连接</h3>
                  <p className="text-xs text-slate-500">这些设置只保存在浏览器本地，适合切换不同机器或后端地址。</p>
                </div>
                <button
                  onClick={() => void handleSaveClient()}
                  disabled={savingSection !== null}
                  className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-3 py-2 text-xs font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
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

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">LLM 与 Python</h3>
                  <p className="text-xs text-slate-500">这里的修改会实时映射到后端运行时配置。</p>
                </div>
                <button
                  onClick={() => void handleSaveLlm()}
                  disabled={savingSection !== null || !llmConfig}
                  className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-60"
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
                  <div className="grid grid-cols-2 gap-4">
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

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">LAMMPS / OVITO 路径</h3>
                  <p className="text-xs text-slate-500">适合在 Windows 或不同机器上切换可执行文件和资源目录。</p>
                </div>
                <button
                  onClick={() => void handleSaveLammps()}
                  disabled={savingSection !== null || !lammpsConfig}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-60"
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
                    <p className="text-[11px] text-slate-500">检测状态：{lammpsConfig.lammps_command_exists ? '已找到' : '未找到'}</p>
                  </SettingField>
                  <SettingField label="势函数目录">
                    <input className={inputClassName()} value={lammpsConfig.potentials_dir} onChange={(event) => setLammpsConfig({ ...lammpsConfig, potentials_dir: event.target.value })} />
                    <p className="text-[11px] text-slate-500">检测状态：{lammpsConfig.potentials_dir_exists ? '已找到' : '未找到'}</p>
                  </SettingField>
                  <SettingField label="OVITO 路径" description="可以填 OVITO 可执行文件路径，或让后端自动检测。Windows 上建议显式指定。">
                    <input className={inputClassName()} value={lammpsConfig.ovito_location} onChange={(event) => setLammpsConfig({ ...lammpsConfig, ovito_location: event.target.value })} />
                    <p className="text-[11px] text-slate-500">当前后端识别：{lammpsConfig.ovito_available ? `${lammpsConfig.ovito_backend} · ${lammpsConfig.ovito_location}` : '未检测到 OVITO'}</p>
                  </SettingField>
                  <div className="grid grid-cols-2 gap-4">
                    <label className={checkboxLabelClassName()}>
                      <span>允许 mock fallback</span>
                      <input type="checkbox" checked={lammpsConfig.allow_mock_fallback} onChange={(event) => setLammpsConfig({ ...lammpsConfig, allow_mock_fallback: event.target.checked })} />
                    </label>
                    <label className={checkboxLabelClassName()}>
                      <span>强制 mock 模式</span>
                      <input type="checkbox" checked={lammpsConfig.force_mock} onChange={(event) => setLammpsConfig({ ...lammpsConfig, force_mock: event.target.checked })} />
                    </label>
                  </div>
                  <SettingField label="最大重试次数">
                    <input className={inputClassName()} type="number" min={0} value={lammpsConfig.max_retries} onChange={(event) => setLammpsConfig({ ...lammpsConfig, max_retries: Number(event.target.value) || 0 })} />
                  </SettingField>
                </div>
              ) : null}
            </section>
          </div>
        </div>

        <div className="border-t border-slate-200 bg-white px-6 py-4">
          <button
            onClick={() => void Promise.all([onRefreshConnection(), getLlmConfig(settings), getLammpsConfig(settings), getSystemDiagnostics(settings)]).then(([_, nextLlmConfig, nextLammpsConfig, nextDiagnostics]) => {
              setLlmConfig(nextLlmConfig)
              setLammpsConfig(nextLammpsConfig)
              setDiagnostics(nextDiagnostics)
              setFeedback('配置与环境诊断已重新加载。')
            }).catch((error) => {
              setFeedback(error instanceof Error ? error.message : '配置刷新失败。')
            })}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新加载当前配置
          </button>
        </div>
      </div>
    </div>
  )
}
