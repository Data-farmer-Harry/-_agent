interface ExecutionLogsPanelProps {
  stdout: string
  stderr: string
}

export function ExecutionLogsPanel({ stdout, stderr }: ExecutionLogsPanelProps) {
  const stdoutLineCount = stdout ? stdout.split(/\r?\n/).length : 0
  const stderrLineCount = stderr ? stderr.split(/\r?\n/).length : 0

  return (
    <section className="panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>执行日志</h2>
          <p>把 stdout 和 stderr 分开陈列，方便在演示中快速解释执行成功、失败与修复线索。</p>
        </div>
      </div>

      <div className="field-grid two-columns">
        <div className="field">
          <div className="panel-subhead">
            <span>stderr</span>
            <span className={`badge ${stderr ? 'status-failed' : ''}`}>{stderr ? `${stderrLineCount} lines` : 'clean'}</span>
          </div>
          <pre className="code-block log-block">{stderr || '暂无错误输出。'}</pre>
        </div>

        <div className="field">
          <div className="panel-subhead">
            <span>stdout</span>
            <span className="badge">{stdout ? `${stdoutLineCount} lines` : 'idle'}</span>
          </div>
          <pre className="code-block log-block">{stdout || '暂无标准输出。'}</pre>
        </div>
      </div>
    </section>
  )
}
