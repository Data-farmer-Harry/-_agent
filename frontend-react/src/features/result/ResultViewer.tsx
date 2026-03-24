import type { ArtifactRef, DeliverableKind } from '../../types/api'

interface ResultViewerProps {
  htmlContent: string
  isLoading: boolean
  sourceImageUrl?: string
  activeMode: 'generate' | 'image'
  routeName?: string
  selectedTool?: string
  deliverable?: DeliverableKind
  artifacts?: ArtifactRef[]
  responseMetadata?: Record<string, unknown>
  stdout?: string
}

function readRecognitionSummary(responseMetadata: Record<string, unknown> | undefined): { summary: string; jsonPreview: string } {
  const imageSpec = (responseMetadata?.image_spec as Record<string, unknown> | undefined) || {}
  const summary = typeof imageSpec.summary === 'string' ? imageSpec.summary : ''
  return {
    summary,
    jsonPreview: Object.keys(imageSpec).length ? JSON.stringify(imageSpec, null, 2) : '',
  }
}

export function ResultViewer({
  htmlContent,
  isLoading,
  sourceImageUrl = '',
  activeMode,
  routeName = '',
  selectedTool = '',
  deliverable = 'none',
  artifacts = [],
  responseMetadata,
  stdout = '',
}: ResultViewerProps) {
  const hasSourceImage = sourceImageUrl.trim().length > 0
  const modeLabel = activeMode === 'image' ? '截图识别重建' : '结构化生成'
  const resultLabel = hasSourceImage ? '双视图结果舞台' : 'HTML 结果舞台'
  const recognition = readRecognitionSummary(responseMetadata)
  const textArtifact = artifacts.find((artifact) => artifact.kind === 'text' && artifact.content)
  const jsonArtifact = artifacts.find((artifact) => artifact.kind === 'json' && artifact.content)

  return (
    <section className="panel viewer-panel">
      <div className="viewer-header panel-header-inline">
        <div>
          <h2>{resultLabel}</h2>
          <p>结果区优先服务演示和核对：图片模式保留原图证据与重建页对照，生成模式则聚焦最终 HTML artifact。</p>
        </div>
        <div className="viewer-toolbar">
          <span className="viewer-toolbar-pill">{modeLabel}</span>
          {routeName ? <span className="viewer-toolbar-pill">{routeName}</span> : null}
          {selectedTool ? <span className="viewer-toolbar-pill">{selectedTool}</span> : null}
          {isLoading ? <span className="status-live">正在生成并执行…</span> : null}
        </div>
      </div>

      {routeName === 'phase_diagram.recognize' && hasSourceImage ? (
        <div className="result-compare-grid">
          <div className="image-preview-shell">
            <div className="viewer-pane-header">
              <strong>上传原图</strong>
              <span>识别输入</span>
            </div>
            <div className="image-preview-frame">
              <img src={sourceImageUrl} alt="Uploaded phase diagram" className="image-preview" />
            </div>
          </div>

          <div className="panel recognition-panel">
            <div className="viewer-pane-header">
              <strong>识别结果</strong>
              <span>{deliverable}</span>
            </div>
            <p>{recognition.summary || stdout || '运行完成后，这里会显示识别摘要和结构化结果。'}</p>
            <pre className="artifact-preview">{jsonArtifact?.content || recognition.jsonPreview || '{}'}</pre>
          </div>
        </div>
      ) : hasSourceImage ? (
        <div className="result-compare-grid">
          <div className="image-preview-shell">
            <div className="viewer-pane-header">
              <strong>上传原图</strong>
              <span>校准参考</span>
            </div>
            <div className="image-preview-frame">
              <img src={sourceImageUrl} alt="Uploaded phase diagram" className="image-preview" />
            </div>
          </div>

          {htmlContent ? (
            <div className="iframe-shell">
              <div className="viewer-pane-header">
                <strong>生成页面</strong>
                <span>交互坐标轴</span>
              </div>
              <iframe srcDoc={htmlContent} title="Agent Result" sandbox="allow-scripts allow-same-origin" />
            </div>
          ) : (
            <div className="empty-state viewer-empty-state">
              <p>图片已加载。提交识别任务后，右侧会生成带温度轴的交互页面。</p>
            </div>
          )}
        </div>
      ) : htmlContent ? (
        <div className="iframe-shell">
          <iframe srcDoc={htmlContent} title="Agent Result" sandbox="allow-scripts allow-same-origin" />
        </div>
      ) : textArtifact?.content ? (
        <div className="panel recognition-panel">
          <div className="viewer-pane-header">
            <strong>文本结果</strong>
            <span>{deliverable}</span>
          </div>
          <pre className="artifact-preview">{textArtifact.content}</pre>
        </div>
      ) : (
        <div className="empty-state viewer-empty-state">
          <p>暂无结果。请先在左侧工作区提交任务，或上传相图截图进入图片识别模式。</p>
        </div>
      )}
    </section>
  )
}
