import type { AgentCatalogResponse, TaskRouteName, WorkspaceStatus } from '../types/api'

export type WorkspaceId = 'phase_diagram' | 'lammps' | 'generic'

export interface WorkspaceDefinition {
  id: WorkspaceId
  title: string
  description: string
  available: boolean
  status: WorkspaceStatus
  defaultRoute: TaskRouteName | null
  availableTools: string[]
  reservedTools: string[]
}

export const fallbackWorkspaces: WorkspaceDefinition[] = [
  {
    id: 'phase_diagram',
    title: '相图工作区',
    description: '复用现有 FastAPI agent 接口，处理相图生成、图片校准识别与执行。',
    available: true,
    status: 'active',
    defaultRoute: 'phase_diagram.generate',
    availableTools: [
      'phase_diagram_result_review',
      'phase_diagram_codegen',
      'phase_diagram_image_parse',
      'phase_diagram_image_render',
      'phase_diagram_repair',
      'python_execute',
      'load_latest_html_artifact',
    ],
    reservedTools: [],
  },
  {
    id: 'lammps',
    title: 'LAMMPS 工作区',
    description: '预留给后续材料仿真 agent，沿用同一套结果区、Trace 区与运行会话。',
    available: false,
    status: 'reserved',
    defaultRoute: 'lammps.generate',
    availableTools: [],
    reservedTools: ['lammps_command_router', 'lammps_codegen', 'lammps_execute', 'lammps_repair'],
  },
  {
    id: 'generic',
    title: '通用工作区',
    description: '用于承接尚未映射到具体 tool 的请求。',
    available: false,
    status: 'disabled',
    defaultRoute: 'generic.unknown',
    availableTools: [],
    reservedTools: [],
  },
]

function withFallbackMetadata(workspace: Partial<WorkspaceDefinition> & Pick<WorkspaceDefinition, 'id'>): WorkspaceDefinition {
  const fallback = fallbackWorkspaces.find((item) => item.id === workspace.id)
  return {
    id: workspace.id,
    title: workspace.title || fallback?.title || workspace.id,
    description: workspace.description || fallback?.description || '',
    available: workspace.available ?? fallback?.available ?? false,
    status: workspace.status || fallback?.status || 'disabled',
    defaultRoute: workspace.defaultRoute ?? fallback?.defaultRoute ?? null,
    availableTools: workspace.availableTools || fallback?.availableTools || [],
    reservedTools: workspace.reservedTools || fallback?.reservedTools || [],
  }
}

export function buildWorkspaceDefinitions(catalog: AgentCatalogResponse | null): WorkspaceDefinition[] {
  if (!catalog?.workspaces?.length) {
    return fallbackWorkspaces
  }

  const definitions = catalog.workspaces.map((workspace) =>
    withFallbackMetadata({
      id: workspace.id,
      title: workspace.title,
      description: workspace.description,
      available: workspace.status === 'active',
      status: workspace.status,
      defaultRoute: workspace.default_route,
      availableTools: workspace.available_tools,
      reservedTools: workspace.reserved_tools,
    }),
  )

  for (const fallback of fallbackWorkspaces) {
    if (!definitions.find((item) => item.id === fallback.id)) {
      definitions.push(fallback)
    }
  }

  return definitions
}
