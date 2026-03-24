import type { ChangeEvent } from 'react'
import type { DiagramRequest, DiagramType } from '../../types/api'

interface PhaseDiagramFormProps {
  value: DiagramRequest
  disabled?: boolean
  canSubmit?: boolean
  onChange: (next: DiagramRequest) => void
  onSubmit?: () => void
}

function toNumber(value: string, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function PhaseDiagramForm({ value, disabled = false, canSubmit = true, onChange, onSubmit }: PhaseDiagramFormProps) {
  const update = <K extends keyof DiagramRequest>(key: K, nextValue: DiagramRequest[K]) => {
    const next = { ...value, [key]: nextValue }

    if (key === 'temperature_min' && next.temperature_max <= next.temperature_min) {
      next.temperature_max = next.temperature_min + Math.max(next.step_size, 1)
    }

    if (key === 'temperature_max' && next.temperature_max <= next.temperature_min) {
      next.temperature_min = Math.max(0, next.temperature_max - Math.max(next.step_size, 1))
    }

    if (key === 'step_size') {
      next.step_size = Math.max(1, next.step_size)
      if (next.temperature_max <= next.temperature_min) {
        next.temperature_max = next.temperature_min + next.step_size
      }
    }

    onChange(next)
  }

  const updateText = (key: 'system_name' | 'notes') => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    update(key, event.target.value)
  }

  const updateNumber = (key: 'temperature_min' | 'temperature_max' | 'pressure' | 'step_size') => (event: ChangeEvent<HTMLInputElement>) => {
    update(key, toNumber(event.target.value, value[key]))
  }

  const updateDiagramType = (diagramType: DiagramType) => update('diagram_type', diagramType)

  return (
    <section className="panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>结构化生成输入</h2>
          <p>从明确的材料体系和约束条件出发，直接触发 route、codegen、执行与 artifact 输出。</p>
        </div>
        {onSubmit ? (
          <button className="primary-button panel-action-button" type="button" disabled={disabled || !canSubmit} onClick={onSubmit}>
            {disabled ? '处理中…' : '生成相图页面'}
          </button>
        ) : null}
      </div>

      <div className="mode-note-card">
        <span className="label">Recommended Flow</span>
        <strong>体系参数 / Tool 选择 / 页面生成</strong>
        <p>适合新任务探索、可控参数扫描，以及向面试官展示 agent 如何从结构化输入落到执行结果。</p>
      </div>

      <label className="field">
        <span>材料体系</span>
        <input
          value={value.system_name}
          type="text"
          placeholder="例如：Fe-C 二元相图"
          onChange={updateText('system_name')}
          disabled={disabled}
        />
      </label>

      <label className="field">
        <span>附加说明</span>
        <textarea
          value={value.notes}
          rows={4}
          placeholder="例如：优先展示温度-成分关系，保留后续 pycalphad 接口"
          onChange={updateText('notes')}
          disabled={disabled}
        />
      </label>

      <div className="field">
        <span>相图类型</span>
        <div className="segmented">
          <button
            type="button"
            className={`segment ${value.diagram_type === 'binary' ? 'active' : ''}`}
            onClick={() => updateDiagramType('binary')}
            disabled={disabled}
          >
            Binary
          </button>
          <button
            type="button"
            className={`segment ${value.diagram_type === 'ternary' ? 'active' : ''}`}
            onClick={() => updateDiagramType('ternary')}
            disabled={disabled}
          >
            Ternary
          </button>
        </div>
      </div>

      <div className="field-grid two-columns">
        <label className="field">
          <span>温度下限 (K)</span>
          <input
            value={value.temperature_min}
            type="number"
            min="0"
            max="4000"
            step="10"
            onChange={updateNumber('temperature_min')}
            disabled={disabled}
          />
          <input
            value={value.temperature_min}
            type="range"
            min="0"
            max="3500"
            step="10"
            onChange={updateNumber('temperature_min')}
            disabled={disabled}
          />
        </label>

        <label className="field">
          <span>温度上限 (K)</span>
          <input
            value={value.temperature_max}
            type="number"
            min="100"
            max="4000"
            step="10"
            onChange={updateNumber('temperature_max')}
            disabled={disabled}
          />
          <input
            value={value.temperature_max}
            type="range"
            min="100"
            max="4000"
            step="10"
            onChange={updateNumber('temperature_max')}
            disabled={disabled}
          />
        </label>

        <label className="field">
          <span>压力 (Pa)</span>
          <input
            value={value.pressure}
            type="number"
            min="0"
            step="1000"
            onChange={updateNumber('pressure')}
            disabled={disabled}
          />
        </label>

        <label className="field">
          <span>步长</span>
          <input
            value={value.step_size}
            type="number"
            min="1"
            max="500"
            step="1"
            onChange={updateNumber('step_size')}
            disabled={disabled}
          />
          <input
            value={value.step_size}
            type="range"
            min="1"
            max="250"
            step="1"
            onChange={updateNumber('step_size')}
            disabled={disabled}
          />
        </label>
      </div>
    </section>
  )
}
