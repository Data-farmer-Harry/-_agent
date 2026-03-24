import type { ChangeEvent } from 'react'
import type { ImageDiagramRequest } from '../../types/api'

interface PhaseDiagramImageFormProps {
  value: ImageDiagramRequest
  disabled?: boolean
  canSubmit: boolean
  onChange: (next: ImageDiagramRequest) => void
  onSubmit: () => void
}

function parseNumber(value: string, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('图片读取失败。'))
    reader.readAsDataURL(file)
  })
}

export function PhaseDiagramImageForm({ value, disabled = false, canSubmit, onChange, onSubmit }: PhaseDiagramImageFormProps) {
  const update = <K extends keyof ImageDiagramRequest>(key: K, nextValue: ImageDiagramRequest[K]) => {
    onChange({ ...value, [key]: nextValue })
  }

  const updateAxis = (axisKey: 'x_axis' | 'y_axis', field: 'label' | 'minimum' | 'maximum') => (event: ChangeEvent<HTMLInputElement>) => {
    const currentAxis = value[axisKey]
    const nextAxis =
      field === 'label'
        ? { ...currentAxis, [field]: event.target.value }
        : { ...currentAxis, [field]: parseNumber(event.target.value, currentAxis[field]) }
    onChange({ ...value, [axisKey]: nextAxis })
  }

  const handleImageFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }

    try {
      const imageDataUrl = await readFileAsDataUrl(file)
      onChange({
        ...value,
        filename: file.name,
        image_data_url: imageDataUrl,
        chart_title: value.chart_title || file.name.replace(/\.[^.]+$/, ''),
      })
    } catch {
      onChange({ ...value, filename: '', image_data_url: '' })
    }
  }

  return (
    <section className="panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>截图识别输入</h2>
          <p>上传相图截图后，先校准真实坐标轴，再保守叠加多模态识别到的标题、边界和标签。</p>
        </div>
        <button className="primary-button panel-action-button" type="button" disabled={disabled || !canSubmit} onClick={onSubmit}>
          {disabled ? '处理中…' : '识别截图并重建页面'}
        </button>
      </div>

      <div className="mode-note-card mode-note-card-image">
        <span className="label">Recommended Flow</span>
        <strong>截图上传 / 坐标校准 / 结果重建</strong>
        <p>适合论文图复原、历史实验图转可交互页面，以及演示多模态 agent 如何与确定性渲染协同工作。</p>
      </div>

      <div className="field-grid">
        <label className="field field-emphasis">
          <span>相图截图</span>
          <input type="file" accept="image/*" onChange={handleImageFile} disabled={disabled} />
        </label>
      </div>

      <div className="field-grid two-columns">
        <label className="field">
          <span>文件名</span>
          <input type="text" value={value.filename} readOnly placeholder="上传后自动填充" disabled />
        </label>
        <label className="field">
          <span>体系名称</span>
          <input type="text" value={value.system_name} onChange={(event) => update('system_name', event.target.value)} placeholder="例如：Fe-C / Al-Cu" disabled={disabled} />
        </label>
        <label className="field">
          <span>页面标题</span>
          <input type="text" value={value.chart_title} onChange={(event) => update('chart_title', event.target.value)} placeholder="例如：Fe-C 相图截图重建" disabled={disabled} />
        </label>
        <label className="field">
          <span>图类型</span>
          <input type="text" value={value.diagram_type} readOnly disabled />
        </label>
      </div>

      <div className="field-grid two-columns">
        <label className="field">
          <span>X 轴标签</span>
          <input type="text" value={value.x_axis.label} onChange={updateAxis('x_axis', 'label')} disabled={disabled} />
        </label>
        <label className="field">
          <span>Y 轴标签</span>
          <input type="text" value={value.y_axis.label} onChange={updateAxis('y_axis', 'label')} disabled={disabled} />
        </label>
        <label className="field">
          <span>X 轴最小值</span>
          <input type="number" value={value.x_axis.minimum} onChange={updateAxis('x_axis', 'minimum')} disabled={disabled} />
        </label>
        <label className="field">
          <span>X 轴最大值</span>
          <input type="number" value={value.x_axis.maximum} onChange={updateAxis('x_axis', 'maximum')} disabled={disabled} />
        </label>
        <label className="field">
          <span>Y 轴最小值</span>
          <input type="number" value={value.y_axis.minimum} onChange={updateAxis('y_axis', 'minimum')} disabled={disabled} />
        </label>
        <label className="field">
          <span>Y 轴最大值</span>
          <input type="number" value={value.y_axis.maximum} onChange={updateAxis('y_axis', 'maximum')} disabled={disabled} />
        </label>
      </div>

      <label className="field">
        <span>识别说明</span>
        <textarea
          rows={4}
          value={value.notes}
          onChange={(event) => update('notes', event.target.value)}
          placeholder="例如：优先识别温度轴、标题和主要相区标签，不清楚的地方不要乱猜。"
          disabled={disabled}
        />
      </label>
    </section>
  )
}
