import type { DiagramRequest } from '../../types/api'

export const defaultPhaseDiagramRequest: DiagramRequest = {
  system_name: 'Fe-C',
  diagram_type: 'binary',
  temperature_min: 300,
  temperature_max: 1800,
  pressure: 101325,
  step_size: 50,
  notes: '请生成一张结构完整、带分区与边界标注的相图。',
}
