import { useEffect, useMemo, useState } from 'react'
import { buildWorkspaceDefinitions, fallbackWorkspaces } from './workspaces'
import { GeneratedCodePanel } from '../features/agent-run/GeneratedCodePanel'
import { AgentConversationPanel } from '../features/chat/AgentConversationPanel'
import { useAgentChat } from '../features/chat/useAgentChat'
import { defaultImageDiagramRequest } from '../features/image-diagram/defaults'
import { defaultPhaseDiagramRequest } from '../features/phase-diagram/defaults'
import { AgentReviewPanel } from '../features/review/AgentReviewPanel'
import { ResultViewer } from '../features/result/ResultViewer'
import { getAgentCatalog } from '../services/api'
import { SettingsPanel } from '../features/settings/SettingsPanel'
import { useLocalSettings } from '../features/settings/useLocalSettings'
import { TracePanel } from '../features/trace/TracePanel'
import type { AgentCatalogResponse, AgentChatRequest, DiagramRequest, ImageDiagramRequest } from '../types/api'

function truncateText(value: string, maximum = 96): string {
  const normalized = value.trim()
  if (!normalized) {
    return '未填写'
  }
  return normalized.length > maximum ? `${normalized.slice(0, maximum - 1)}…` : normalized
}

export function AgentWorkbench() {
  const { settings, updateSettings, resetSettings, apiConnection } = useLocalSettings()
  const { state, loadLatestResult, sendMessage } = useAgentChat(settings)
  const [phaseDraft, setPhaseDraft] = useState<DiagramRequest>(defaultPhaseDiagramRequest)
  const [imageDraft, setImageDraft] = useState<ImageDiagramRequest>(defaultImageDiagramRequest)
  const [draftMessage, setDraftMessage] = useState('请生成一张 Fe-Cu 二元相图，温度范围 300-1800K，突出液相线和两固相区。')
  const [catalog, setCatalog] = useState<AgentCatalogResponse | null>(null)
  const [catalogStatus, setCatalogStatus] = useState('正在加载 workspace 状态…')

  useEffect(() => {
    if (apiConnection.status !== 'ready') {
      return
    }
    void loadLatestResult()
  }, [apiConnection.status, loadLatestResult])

  useEffect(() => {
    let cancelled = false

    const loadCatalog = async () => {
      if (apiConnection.status !== 'ready') {
        setCatalog(null)
        setCatalogStatus(apiConnection.message)
        return
      }

      try {
        const nextCatalog = await getAgentCatalog(settings)
        if (cancelled) {
          return
        }
        setCatalog(nextCatalog)
        setCatalogStatus('workspace 状态已同步')
      } catch {
        if (cancelled) {
          return
        }
        setCatalog(null)
        setCatalogStatus('workspace 状态加载失败，已使用本地默认值')
      }
    }

    void loadCatalog()
    return () => {
      cancelled = true
    }
  }, [apiConnection.message, apiConnection.status, settings])

  const workspaceDefinitions = useMemo(() => buildWorkspaceDefinitions(catalog), [catalog])
  const phaseWorkspace = workspaceDefinitions.find((workspace) => workspace.id === 'phase_diagram') ?? fallbackWorkspaces[0]
  const lammpsWorkspace = workspaceDefinitions.find((workspace) => workspace.id === 'lammps') ?? fallbackWorkspaces[1]
  const selectedTool = state.route?.selected_tool || '等待选择'
  const routeName = state.route?.name || '等待路由'
  const routeIntent = state.route?.intent || '等待决策'
  const sourceImageUrl = state.sourceImageDataUrl || imageDraft.image_data_url
  const agentBackendReady = apiConnection.status === 'ready'

  const handleSend = () => {
    if (!agentBackendReady) {
      return
    }

    const payload: AgentChatRequest = {
      message: draftMessage.trim() || '请识别这张相图并生成页面。',
      system_name: imageDraft.image_data_url ? imageDraft.system_name : phaseDraft.system_name,
      chart_title: imageDraft.chart_title,
      diagram_type: phaseDraft.diagram_type,
      temperature_min: phaseDraft.temperature_min,
      temperature_max: phaseDraft.temperature_max,
      pressure: phaseDraft.pressure,
      step_size: phaseDraft.step_size,
      notes: phaseDraft.notes,
      image_data_url: imageDraft.image_data_url || undefined,
      filename: imageDraft.filename,
      x_axis: imageDraft.image_data_url ? imageDraft.x_axis : undefined,
      y_axis: imageDraft.image_data_url ? imageDraft.y_axis : undefined,
    }

    void sendMessage(payload)
    setDraftMessage('')
  }

  const statusTone = state.status === 'error' ? 'status-chip-danger' : state.status === 'completed' ? 'status-chip-success' : 'status-chip-active'
  const connectionTone =
    apiConnection.status === 'ready'
      ? 'status-chip-success'
      : apiConnection.status === 'resolving'
        ? 'status-chip-active'
        : 'status-chip-danger'

  const stageSummaryCards = [
    { label: 'Route', value: routeName, hint: state.route?.reason || '等待 agent 判断任务类型' },
    { label: 'Intent', value: routeIntent, hint: state.route?.decision_source || '等待 decision source' },
    { label: 'Run ID', value: state.runId || '待分配', hint: state.terminationReason || '尚未开始运行' },
    { label: 'Workspace', value: state.route?.workspace_id || phaseWorkspace.id, hint: catalogStatus },
    { label: 'Selected Tool', value: selectedTool, hint: state.route?.deliverable || '等待 deliverable' },
  ]

  return (
    <main className="app-shell agent-workbench-shell">
      <header className="panel agent-header-panel">
        <div className="agent-header-copy">
          <span className="hero-eyebrow">Materials Research Agent</span>
          <h1>材料科研 Agent 控制台</h1>
          <p>这里不是固定 workflow 页面，而是一个面向科研成员的对话式 agent。你用自然语言提出任务后，它会先判断这次该走 Python 计算、多模态识别、还是 HTML 重绘，再决定调用哪个 tool。</p>
        </div>

        <div className="hero-meta">
          <span className={`status-chip ${statusTone}`}>{state.status || 'idle'}</span>
          <span className={`status-chip ${connectionTone}`}>{apiConnection.status}</span>
          <span className="status-chip status-chip-muted">{routeName}</span>
          <span className="status-chip status-chip-muted">{selectedTool}</span>
          <span className="status-chip status-chip-muted status-chip-wide">{settings.apiBaseUrl}</span>
        </div>
      </header>

      <div className="agent-main-grid">
        <section className="agent-left-column">
          <AgentConversationPanel
            messages={state.messages}
            draftMessage={draftMessage}
            phaseDraft={phaseDraft}
            imageDraft={imageDraft}
            disabled={state.isLoading || !agentBackendReady}
            connectionMessage={apiConnection.message}
            connectionStatus={apiConnection.status}
            onDraftMessageChange={setDraftMessage}
            onPhaseDraftChange={setPhaseDraft}
            onImageDraftChange={setImageDraft}
            onSend={handleSend}
            onLoadLatestResult={() => void loadLatestResult()}
          />

          <TracePanel
            runId={state.runId}
            route={state.route}
            planSteps={state.planSteps}
            timeline={state.timeline}
            status={state.statusMessage}
            terminationReason={state.terminationReason}
            isLoading={state.isLoading}
          />

          <details className="route-reason">
            <summary>本地设置</summary>
            <SettingsPanel settings={settings} onChange={updateSettings} onReset={resetSettings} disabled={state.isLoading} />
          </details>
        </section>

        <section className="agent-right-column">
          <section className="panel result-stage-panel">
            <div className="panel-header panel-header-inline">
              <div>
                <h2>结果舞台</h2>
                <p>右侧专注展示 agent 的最终产物。如果你拖入了截图，这里会保留原图与重建页对照，为后续相图识别功能继续扩展留足空间。</p>
              </div>
              <span className="status-live">{sourceImageUrl ? '图片模式' : '生成模式'}</span>
            </div>

            <div className="summary-grid review-summary-grid">
              {stageSummaryCards.map((card) => (
                <div key={card.label} className="summary-card">
                  <span className="label">{card.label}</span>
                  <strong>{card.value}</strong>
                  <p>{truncateText(card.hint, 80)}</p>
                </div>
              ))}
            </div>

            <div className="workspace-strip">
              <span className={`capability-pill ${phaseWorkspace.available ? 'capability-pill-live' : ''}`}>{phaseWorkspace.title}</span>
              <span className="capability-pill">{phaseWorkspace.defaultRoute || 'phase_diagram.generate'}</span>
              <span className="capability-pill">{lammpsWorkspace.title}</span>
              <span className="capability-pill">{lammpsWorkspace.status}</span>
            </div>
          </section>

          <ResultViewer
            htmlContent={state.htmlContent}
            isLoading={state.isLoading}
            sourceImageUrl={sourceImageUrl}
            activeMode={sourceImageUrl ? 'image' : 'generate'}
            routeName={routeName}
            selectedTool={selectedTool}
            deliverable={state.route?.deliverable}
            artifacts={state.artifacts}
            responseMetadata={state.responseMetadata}
            stdout={state.stdout}
          />

          <AgentReviewPanel
            passed={state.review.passed}
            summary={state.review.summary}
            confidence={state.review.confidence}
            issues={state.review.issues}
            mode={state.review.mode}
          />

          <GeneratedCodePanel generatedCode={state.generatedCode} />
        </section>
      </div>
    </main>
  )
}
