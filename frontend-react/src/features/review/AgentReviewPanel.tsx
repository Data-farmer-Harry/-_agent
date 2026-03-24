interface AgentReviewPanelProps {
  passed: boolean | null
  summary: string
  confidence: number | null
  issues: string[]
  mode: string
}

export function AgentReviewPanel({ passed, summary, confidence, issues, mode }: AgentReviewPanelProps) {
  const statusLabel = passed === null ? 'waiting' : passed ? 'passed' : 'needs attention'
  const statusClass = passed === null ? '' : passed ? 'status-completed' : 'status-failed'

  return (
    <section className="panel review-panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>Agent 自检</h2>
          <p>这里显示生成后自检的结论，帮助你快速判断结果页是否值得继续相信、继续修，还是需要重新给约束。</p>
        </div>
        <span className={`badge ${statusClass}`}>{statusLabel}</span>
      </div>

      <div className="summary-grid review-summary-grid">
        <div className="summary-card">
          <span className="label">Review Mode</span>
          <strong>{mode || 'pending'}</strong>
        </div>
        <div className="summary-card">
          <span className="label">Confidence</span>
          <strong>{confidence !== null ? confidence.toFixed(2) : '—'}</strong>
        </div>
      </div>

      <div className="review-note">
        <strong>{summary || '执行完成后，agent 会在这里给出对代码和结果页的一次自检判断。'}</strong>
      </div>

      {issues.length ? (
        <ul className="review-issue-list">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : (
        <p className="empty-text">当前没有额外的自检问题。</p>
      )}
    </section>
  )
}
