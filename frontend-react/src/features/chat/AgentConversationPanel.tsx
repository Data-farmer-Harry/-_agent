import { useState, type ChangeEvent, type DragEvent } from 'react'
import type { DiagramRequest, ImageDiagramRequest } from '../../types/api'
import type { ConversationMessage } from './useAgentChat'

interface AgentConversationPanelProps {
  messages: ConversationMessage[]
  draftMessage: string
  phaseDraft: DiagramRequest
  imageDraft: ImageDiagramRequest
  disabled: boolean
  connectionMessage: string
  connectionStatus: 'resolving' | 'ready' | 'agent-unavailable' | 'offline'
  onDraftMessageChange: (value: string) => void
  onPhaseDraftChange: (value: DiagramRequest) => void
  onImageDraftChange: (value: ImageDiagramRequest) => void
  onSend: () => void
  onLoadLatestResult: () => void
}

function toNumber(value: string, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function messageToneClass(tone: ConversationMessage['tone']): string {
  if (tone === 'warning') {
    return 'conversation-bubble-warning'
  }
  if (tone === 'status') {
    return 'conversation-bubble-status'
  }
  return ''
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('图片读取失败。'))
    reader.readAsDataURL(file)
  })
}

export function AgentConversationPanel({
  messages,
  draftMessage,
  phaseDraft,
  imageDraft,
  disabled,
  connectionMessage,
  connectionStatus,
  onDraftMessageChange,
  onPhaseDraftChange,
  onImageDraftChange,
  onSend,
  onLoadLatestResult,
}: AgentConversationPanelProps) {
  const [dragActive, setDragActive] = useState(false)
  const hasAttachment = imageDraft.image_data_url.trim().length > 0
  const canSend =
    connectionStatus === 'ready' &&
    Boolean(draftMessage.trim() || hasAttachment) &&
    phaseDraft.temperature_max > phaseDraft.temperature_min &&
    (!hasAttachment || (imageDraft.x_axis.maximum > imageDraft.x_axis.minimum && imageDraft.y_axis.maximum > imageDraft.y_axis.minimum))
  const connectionToneClass =
    connectionStatus === 'ready'
      ? 'connection-hint-ready'
      : connectionStatus === 'resolving'
        ? 'connection-hint-pending'
        : 'connection-hint-warning'

  const quickPrompts = [
    '请生成 Fe-Cu 二元相图，并给出清晰的液相线和两固相区。',
    '请把这段相图讲解整理成一页适合组会展示的 HTML 页面。',
    '我上传了一张相图截图，请先识别标题、坐标轴和可见相区标签。',
  ]

  const applyFile = async (file: File) => {
    const imageDataUrl = await readFileAsDataUrl(file)
    onImageDraftChange({
      ...imageDraft,
      filename: file.name,
      image_data_url: imageDataUrl,
      chart_title: imageDraft.chart_title || file.name.replace(/\.[^.]+$/, ''),
    })
  }

  const handleFileInput = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }
    try {
      await applyFile(file)
    } catch {
      onImageDraftChange({ ...imageDraft, filename: '', image_data_url: '' })
    }
  }

  const handleDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragActive(false)
    const file = event.dataTransfer.files?.[0]
    if (!file || disabled) {
      return
    }
    try {
      await applyFile(file)
    } catch {
      onImageDraftChange({ ...imageDraft, filename: '', image_data_url: '' })
    }
  }

  return (
    <section className="panel conversation-panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>Agent 对话输入</h2>
          <p>直接告诉 agent 你想做相图生成、相图识别，还是讲解内容重绘。它会先做任务判断，再决定调用 Python、多模态识别或 HTML 重绘工具。</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={onLoadLatestResult} disabled={disabled}>
            最近结果
          </button>
        </div>
      </div>

      <div className="workspace-strip">
        <span className="capability-pill capability-pill-live">Phase Diagram Agent</span>
        <span className="capability-pill">Python Plot Tool</span>
        <span className="capability-pill">Image Parse Ready</span>
        <span className="capability-pill">LAMMPS Coming Next</span>
      </div>

      <div className={`connection-hint ${connectionToneClass}`}>{connectionMessage}</div>

      <div className="conversation-scroll">
        {messages.length ? (
          messages.map((message) => (
            <article
              key={message.id}
              className={`conversation-bubble ${message.role === 'user' ? 'conversation-bubble-user' : 'conversation-bubble-assistant'} ${messageToneClass(message.tone)}`}
            >
              <span className="label">{message.role === 'user' ? '你' : 'Agent'}</span>
              <p>{message.content}</p>
              {message.attachmentName ? <span className="conversation-attachment">附件：{message.attachmentName}</span> : null}
            </article>
          ))
        ) : (
          <div className="empty-state conversation-empty-state">
            <p>从一句自然语言开始，比如“生成 Fe-Cu 相图”，或者直接拖一张相图截图进来。</p>
          </div>
        )}
      </div>

      <div className="quick-prompt-row">
        {quickPrompts.map((prompt) => (
          <button key={prompt} type="button" className="quick-prompt-button" disabled={disabled} onClick={() => onDraftMessageChange(prompt)}>
            {prompt}
          </button>
        ))}
      </div>

      <label className="field">
        <span>生成要求</span>
        <textarea
          value={draftMessage}
          rows={5}
          placeholder="例如：我想生成一张 Fe-Cu 二元相图，温度范围 300-1800K，突出液相线和两固相区。"
          onChange={(event) => onDraftMessageChange(event.target.value)}
          disabled={disabled}
        />
      </label>

      <div
        className={`chat-dropzone ${dragActive ? 'chat-dropzone-active' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled) {
            setDragActive(true)
          }
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
      >
        <div>
          <strong>拖入相图截图</strong>
          <p>这里既可以给识别任务提供原图，也可以给 HTML 重绘任务提供参考图。具体怎么用，由 agent 决定。</p>
        </div>
        <label className="secondary-button upload-button">
          选择图片
          <input type="file" accept="image/*" onChange={handleFileInput} disabled={disabled} />
        </label>
      </div>

      {hasAttachment ? (
        <div className="attachment-strip">
          <span className="capability-pill">{imageDraft.filename || '已附加图片'}</span>
          <button
            type="button"
            className="secondary-button"
            onClick={() => onImageDraftChange({ ...imageDraft, filename: '', image_data_url: '' })}
            disabled={disabled}
          >
            移除图片
          </button>
        </div>
      ) : null}

      <details className="route-reason" open={hasAttachment}>
        <summary>高级约束</summary>
        <div className="field-grid two-columns compact-grid">
          <label className="field">
            <span>体系名称</span>
            <input
              value={hasAttachment ? imageDraft.system_name : phaseDraft.system_name}
              type="text"
              onChange={(event) =>
                hasAttachment
                  ? onImageDraftChange({ ...imageDraft, system_name: event.target.value })
                  : onPhaseDraftChange({ ...phaseDraft, system_name: event.target.value })
              }
              disabled={disabled}
            />
          </label>

          <label className="field">
            <span>图类型</span>
            <input value={phaseDraft.diagram_type} type="text" readOnly disabled />
          </label>

          <label className="field">
            <span>温度下限 (K)</span>
            <input
              value={phaseDraft.temperature_min}
              type="number"
              onChange={(event) => onPhaseDraftChange({ ...phaseDraft, temperature_min: toNumber(event.target.value, phaseDraft.temperature_min) })}
              disabled={disabled}
            />
          </label>

          <label className="field">
            <span>温度上限 (K)</span>
            <input
              value={phaseDraft.temperature_max}
              type="number"
              onChange={(event) => onPhaseDraftChange({ ...phaseDraft, temperature_max: toNumber(event.target.value, phaseDraft.temperature_max) })}
              disabled={disabled}
            />
          </label>

          <label className="field">
            <span>压力 (Pa)</span>
            <input
              value={phaseDraft.pressure}
              type="number"
              onChange={(event) => onPhaseDraftChange({ ...phaseDraft, pressure: toNumber(event.target.value, phaseDraft.pressure) })}
              disabled={disabled}
            />
          </label>

          <label className="field">
            <span>步长</span>
            <input
              value={phaseDraft.step_size}
              type="number"
              onChange={(event) => onPhaseDraftChange({ ...phaseDraft, step_size: toNumber(event.target.value, phaseDraft.step_size) })}
              disabled={disabled}
            />
          </label>

          {hasAttachment ? (
            <>
              <label className="field">
                <span>页面标题</span>
                <input
                  value={imageDraft.chart_title}
                  type="text"
                  onChange={(event) => onImageDraftChange({ ...imageDraft, chart_title: event.target.value })}
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>X 轴标签</span>
                <input
                  value={imageDraft.x_axis.label}
                  type="text"
                  onChange={(event) => onImageDraftChange({ ...imageDraft, x_axis: { ...imageDraft.x_axis, label: event.target.value } })}
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>X 轴最小值</span>
                <input
                  value={imageDraft.x_axis.minimum}
                  type="number"
                  onChange={(event) =>
                    onImageDraftChange({ ...imageDraft, x_axis: { ...imageDraft.x_axis, minimum: toNumber(event.target.value, imageDraft.x_axis.minimum) } })
                  }
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>Y 轴标签</span>
                <input
                  value={imageDraft.y_axis.label}
                  type="text"
                  onChange={(event) => onImageDraftChange({ ...imageDraft, y_axis: { ...imageDraft.y_axis, label: event.target.value } })}
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>X 轴最大值</span>
                <input
                  value={imageDraft.x_axis.maximum}
                  type="number"
                  onChange={(event) =>
                    onImageDraftChange({ ...imageDraft, x_axis: { ...imageDraft.x_axis, maximum: toNumber(event.target.value, imageDraft.x_axis.maximum) } })
                  }
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>Y 轴最小值</span>
                <input
                  value={imageDraft.y_axis.minimum}
                  type="number"
                  onChange={(event) =>
                    onImageDraftChange({ ...imageDraft, y_axis: { ...imageDraft.y_axis, minimum: toNumber(event.target.value, imageDraft.y_axis.minimum) } })
                  }
                  disabled={disabled}
                />
              </label>
              <label className="field">
                <span>Y 轴最大值</span>
                <input
                  value={imageDraft.y_axis.maximum}
                  type="number"
                  onChange={(event) =>
                    onImageDraftChange({ ...imageDraft, y_axis: { ...imageDraft.y_axis, maximum: toNumber(event.target.value, imageDraft.y_axis.maximum) } })
                  }
                  disabled={disabled}
                />
              </label>
            </>
          ) : null}
        </div>
      </details>

      <div className="hero-actions">
        <button className="primary-button" type="button" disabled={disabled || !canSend} onClick={onSend}>
          {connectionStatus !== 'ready' ? '等待 Agent 后端' : disabled ? 'Agent 执行中…' : '让 Agent 开始工作'}
        </button>
      </div>
    </section>
  )
}
