import { useEffect, useMemo, useRef } from 'react'
import type { PlanStep, TaskRoute, ToolObservation } from '../../types/api'

interface TracePanelProps {
  runId: string
  route: TaskRoute | null
  planSteps: PlanStep[]
  timeline: ToolObservation[]
  status: string
  terminationReason: string
  isLoading: boolean
}

function stepStatusClass(status: string): string {
  return `status-${status}`
}

export function TracePanel({ runId, route, planSteps, timeline, status, terminationReason, isLoading }: TracePanelProps) {
  const timelineRef = useRef<HTMLUListElement | null>(null)

  useEffect(() => {
    const element = timelineRef.current
    if (element) {
      element.scrollTop = element.scrollHeight
    }
  }, [timeline.length])

  const summary = useMemo(
    () => [
      { label: 'Run ID', value: runId || '待分配' },
      { label: 'Route', value: route?.name || '待路由' },
      { label: 'Workspace', value: route?.workspace_id || '待选择' },
      { label: 'Tool', value: route?.selected_tool || '待选择' },
      { label: 'Intent', value: route?.intent || '待决策' },
      { label: 'Decision', value: route?.decision_source || '待决策' },
      { label: 'Deliverable', value: route?.deliverable || '待声明' },
      { label: 'Inputs', value: route?.input_channels?.length ? route.input_channels.join(' / ') : '待声明' },
      { label: '状态', value: status || '待执行' },
      { label: 'Plan', value: `${planSteps.length} steps` },
      { label: 'Trace', value: `${timeline.length} events` },
      { label: '终止原因', value: terminationReason || '—' },
    ],
    [
      planSteps.length,
      route?.deliverable,
      route?.decision_source,
      route?.input_channels,
      route?.intent,
      route?.name,
      route?.selected_tool,
      route?.workspace_id,
      runId,
      status,
      terminationReason,
      timeline.length,
    ],
  )

  return (
    <section className="panel trace-panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>Tool 调用面板</h2>
          <p>这里专门展示 agent 当前选中的 route、计划步骤和 tool 时间线，方便你确认它到底调用了什么，而不是只看最后一张图。</p>
        </div>
        {isLoading ? <span className="status-live">实时更新中…</span> : null}
      </div>

      <div className="summary-grid">
        {summary.map((item) => (
          <div key={item.label} className="summary-card">
            <span className="label">{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>

      {route?.reason ? (
        <details className="route-reason">
          <summary>Route reason</summary>
          <p>{route.reason}</p>
          {typeof route.decision_confidence === 'number' ? <p>Decision confidence: {route.decision_confidence.toFixed(2)}</p> : null}
        </details>
      ) : null}

      <div className="section-block">
        <div className="section-heading">
          <h3>计划步骤</h3>
          <span className="badge">{planSteps.length} steps</span>
        </div>
        {planSteps.length ? (
          <ul className="stack-list">
            {planSteps.map((step) => (
              <li key={step.index} className={`stack-item ${stepStatusClass(step.status)}`}>
                <div className="item-topline">
                  <strong>
                    Step {step.index} · {step.tool_name}
                  </strong>
                  <span className="badge">{step.status}</span>
                </div>
                <div className="item-meta">
                  {step.stage ? <span className="capability-pill">stage: {step.stage}</span> : null}
                  {step.retryable ? <span className="capability-pill">retryable</span> : null}
                  {typeof step.metadata?.workspace_id === 'string' ? (
                    <span className="capability-pill">{step.metadata.workspace_id}</span>
                  ) : null}
                </div>
                <p>{step.description || '无描述。'}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-text">暂无计划步骤。</p>
        )}
      </div>

      <div className="section-block">
        <div className="section-heading">
          <h3>Tool 时间线</h3>
          <span className="badge">{timeline.length} events</span>
        </div>
        {timeline.length ? (
          <ul ref={timelineRef} className="stack-list timeline-scroll">
            {timeline.map((item, index) => (
              <li key={`${item.step_index}-${item.tool_name}-${index}`} className="stack-item">
                <div className="item-topline">
                  <strong>
                    Step {item.step_index} · {item.tool_name}
                  </strong>
                  <span className={`badge ${item.success ? 'status-completed' : 'status-failed'}`}>
                    {item.success ? 'completed' : 'failed'}
                  </span>
                </div>
                {item.state_delta && Object.keys(item.state_delta).length ? (
                  <div className="item-meta">
                    {Object.entries(item.state_delta).map(([key, value]) => (
                      <span key={`${item.tool_name}-${key}`} className="capability-pill">
                        {key}: {String(value)}
                      </span>
                    ))}
                  </div>
                ) : null}
                <p>{item.summary}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-text">运行开始后会在这里持续显示 tool 调用过程。</p>
        )}
      </div>
    </section>
  )
}
