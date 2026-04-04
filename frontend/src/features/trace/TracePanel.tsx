import type { PlanStep, RecognitionResult, TaskRoute, ToolObservation } from '../../types/api'

interface TracePanelProps {
  runId: string
  route: TaskRoute | null
  planSteps: PlanStep[]
  timeline: ToolObservation[]
  status: string
  runStatus: string
  terminationReason: string
  isLoading: boolean
  responseMetadata: Record<string, unknown>
  summary: Record<string, unknown>
  recognitionResult: RecognitionResult | null
  canCancel: boolean
  onCancel: () => void
}

function readReview(responseMetadata: Record<string, unknown>): { summary: string; passed: boolean | null; issues: string[] } {
  const review = (responseMetadata.review as Record<string, unknown> | undefined) || {}
  return {
    summary: typeof review.summary === 'string' ? review.summary : '',
    passed: typeof review.passed === 'boolean' ? review.passed : null,
    issues: Array.isArray(review.issues) ? review.issues.map(String) : [],
  }
}

function readMetrics(summary: Record<string, unknown>): Record<string, unknown> {
  const metrics = summary.metrics
  return metrics && typeof metrics === 'object' ? (metrics as Record<string, unknown>) : {}
}

export function TracePanel({
  runId,
  route,
  planSteps,
  timeline,
  status,
  runStatus,
  terminationReason,
  isLoading,
  responseMetadata,
  summary,
  recognitionResult,
  canCancel,
  onCancel,
}: TracePanelProps) {
  const review = readReview(responseMetadata)
  const routeName = route?.name || 'awaiting'
  const toolInvoked = routeName !== 'conversation.answer' && routeName !== 'awaiting'
  const computeDomain = route?.compute_domain || 'none'
  const metrics = readMetrics(summary)
  const modeLabel =
    routeName === 'recognition.analyze'
      ? 'recognition'
      : routeName === 'phase_diagram.generate'
        ? 'local python'
        : routeName === 'lammps.generate'
          ? 'local lammps'
        : routeName === 'mixed.request'
          ? 'recognition + local python'
          : 'dialog only'

  return (
    <section className="inspector-panel">
      <div className="inspector-panel-header">
        <div>
          <h3>Agent Trace</h3>
          <p>普通问答保持对话模式。只有当 agent 选择相图生成时，这里才会展开本地 Python 和自检链路。</p>
        </div>
        {isLoading ? <span className="status-live">running</span> : null}
      </div>

      <div className="trace-summary-grid">
        <div className="trace-summary-card">
          <span>Route</span>
          <strong>{route?.name || 'awaiting'}</strong>
        </div>
        <div className="trace-summary-card">
          <span>Run ID</span>
          <strong>{runId || 'pending'}</strong>
        </div>
        <div className="trace-summary-card">
          <span>Status</span>
          <strong>{status}</strong>
        </div>
        <div className="trace-summary-card">
          <span>Run Status</span>
          <strong>{runStatus || 'draft'}</strong>
        </div>
        <div className="trace-summary-card">
          <span>Tool Mode</span>
          <strong>{modeLabel}</strong>
        </div>
        <div className="trace-summary-card">
          <span>Domain</span>
          <strong>{computeDomain}</strong>
        </div>
      </div>

      {route?.reason ? <div className="trace-note">{route.reason}</div> : null}
      {canCancel ? (
        <button type="button" className="trace-cancel-button" onClick={onCancel}>
          停止当前运行
        </button>
      ) : null}

      {toolInvoked ? (
        <>
          <div className="trace-block">
            <div className="trace-block-header">
              <h4>Plan Steps</h4>
              <span>{planSteps.length}</span>
            </div>
            {planSteps.length ? (
              <ul className="trace-list">
                {planSteps.map((step) => (
                  <li key={step.index} className={`trace-item trace-item-${step.status}`}>
                    <div className="trace-item-topline">
                      <strong>
                        {step.index}. {step.tool_name}
                      </strong>
                      <span>{step.status}</span>
                    </div>
                    <p>{step.description || '—'}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-text">等待 agent 生成执行计划。</p>
            )}
          </div>

          <div className="trace-block">
            <div className="trace-block-header">
              <h4>Tool Timeline</h4>
              <span>{timeline.length}</span>
            </div>
            {timeline.length ? (
              <ul className="trace-list">
                {timeline.map((item, index) => (
                  <li key={`${item.step_index}-${item.tool_name}-${index}`} className={`trace-item ${item.success ? 'trace-item-completed' : 'trace-item-failed'}`}>
                    <div className="trace-item-topline">
                      <strong>{item.tool_name}</strong>
                      <span>{item.success ? 'ok' : 'failed'}</span>
                    </div>
                    <p>{item.summary}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-text">运行开始后会在这里显示每一步的调用结果。</p>
            )}
          </div>

          {recognitionResult ? (
            <div className="trace-block">
              <div className="trace-block-header">
                <h4>Recognition</h4>
                <span>{recognitionResult.confidence ? recognitionResult.confidence.toFixed(2) : 'n/a'}</span>
              </div>
              <p className="trace-note">{recognitionResult.raw_summary || '已返回结构化识别结果。'}</p>
              <ul className="issue-list">
                <li>system: {recognitionResult.system || 'unknown'}</li>
                <li>x-axis: {recognitionResult.x_axis.label || 'unknown'}</li>
                <li>y-axis: {recognitionResult.y_axis.label || 'unknown'}</li>
                <li>phases: {recognitionResult.phases.length ? recognitionResult.phases.join('、') : 'none'}</li>
              </ul>
            </div>
          ) : null}

          {routeName !== 'recognition.analyze' ? (
            <div className="trace-block">
              <div className="trace-block-header">
                <h4>LLM Review</h4>
                <span>{review.passed === null ? 'pending' : review.passed ? 'passed' : 'failed'}</span>
              </div>
              <p className="trace-note">{review.summary || '结果自检完成后，这里会显示 LLM 的审查结论。'}</p>
              {review.issues.length ? (
                <ul className="issue-list">
                  {review.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {Object.keys(metrics).length ? (
            <div className="trace-block">
              <div className="trace-block-header">
                <h4>Metrics</h4>
                <span>{Object.keys(metrics).length}</span>
              </div>
              <ul className="issue-list">
                {Object.entries(metrics).map(([key, value]) => (
                  <li key={key}>
                    {key}: {typeof value === 'number' ? value.toFixed(3) : String(value)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : (
        <div className="trace-block">
          <div className="trace-block-header">
            <h4>Dialog Mode</h4>
            <span>no tool call</span>
          </div>
          <p className="trace-note">这一轮由 LLM 直接回答，没有进入相图生成、本地 Python 执行或 LangGraph 自检循环。</p>
        </div>
      )}

      {terminationReason ? <div className="trace-footer">termination: {terminationReason}</div> : null}
    </section>
  )
}
