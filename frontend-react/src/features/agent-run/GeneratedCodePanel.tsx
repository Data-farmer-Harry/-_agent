interface GeneratedCodePanelProps {
  generatedCode: string
}

export function GeneratedCodePanel({ generatedCode }: GeneratedCodePanelProps) {
  const lineCount = generatedCode ? generatedCode.split(/\r?\n/).length : 0

  return (
    <section className="panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>生成代码</h2>
          <p>展示当前 run 的代码产物，方便核对 agent 生成策略、修复路径和最终执行上下文。</p>
        </div>
        <span className="badge">{generatedCode ? `${lineCount} lines` : 'waiting'}</span>
      </div>
      {generatedCode ? (
        <pre className="code-block generated-code-block">{generatedCode}</pre>
      ) : (
        <div className="empty-state code-empty-state">
          <p>暂无生成代码。提交任务后，这里会展示当前 run 的 Python 输出。</p>
        </div>
      )}
    </section>
  )
}
