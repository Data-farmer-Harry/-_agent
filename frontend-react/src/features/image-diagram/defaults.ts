import type { ImageDiagramRequest } from '../../types/api'

export const defaultImageDiagramRequest: ImageDiagramRequest = {
  image_data_url: '',
  filename: '',
  system_name: '',
  chart_title: '',
  diagram_type: 'binary',
  x_axis: {
    label: 'Composition',
    minimum: 0,
    maximum: 1,
  },
  y_axis: {
    label: 'Temperature (°C)',
    minimum: 0,
    maximum: 1600,
  },
  notes: '请优先识别标题、坐标轴与清晰可见的相区标签；不确定时保持保守。',
}
