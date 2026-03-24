import type { AgentManifestResponse, TaskRouteName, WorkspaceId } from '../../types/api'

interface AgentBlueprintPanelProps {
  manifest: AgentManifestResponse | null
  activeWorkspaceId: WorkspaceId
  activeRouteName?: TaskRouteName
  manifestStatus: string
}

function joinValues(values: string[]): string {
  return values.length ? values.join(' / ') : '—'
}

export function AgentBlueprintPanel({
  manifest,
  activeWorkspaceId,
  activeRouteName,
  manifestStatus,
}: AgentBlueprintPanelProps) {
  const workspaceRoutes = manifest?.routes.filter((route) => route.workspace_id === activeWorkspaceId) ?? []
  const workspaceTools = manifest?.tools.filter((tool) => tool.workspace_id === activeWorkspaceId) ?? []
  const activeToolCount = workspaceTools.filter((tool) => tool.status === 'active').length
  const reservedToolCount = workspaceTools.filter((tool) => tool.status === 'reserved').length

  return (
    <section className="panel blueprint-panel">
      <div className="panel-header panel-header-inline">
        <div>
          <h2>Agent 系统蓝图</h2>
          <p>直接读取后端 manifest，把 route、step、tool contract 和失败恢复策略可视化，方便科研组日常协作，也方便面试时讲清系统设计。</p>
        </div>
        <span className="badge">{manifest ? 'manifest live' : 'manifest unavailable'}</span>
      </div>

      <div className="summary-grid blueprint-summary-grid">
        <div className="summary-card">
          <span className="label">Active Workspace</span>
          <strong>{activeWorkspaceId}</strong>
          <p>{manifestStatus}</p>
        </div>
        <div className="summary-card">
          <span className="label">Routes</span>
          <strong>{workspaceRoutes.length}</strong>
          <p>当前工作区已声明的执行蓝图</p>
        </div>
        <div className="summary-card">
          <span className="label">Active Tools</span>
          <strong>{activeToolCount}</strong>
          <p>可被 runtime 直接调用的工具</p>
        </div>
        <div className="summary-card">
          <span className="label">Reserved Tools</span>
          <strong>{reservedToolCount}</strong>
          <p>已占位、等待接入的扩展节点</p>
        </div>
      </div>

      <div className="blueprint-grid">
        <div className="blueprint-route-grid">
          {workspaceRoutes.length ? (
            workspaceRoutes.map((route) => (
              <article
                key={route.name}
                className={`blueprint-route-card ${route.name === activeRouteName ? 'blueprint-route-card-active' : ''}`}
              >
                <div className="panel-header panel-header-inline">
                  <div>
                    <h3>{route.name}</h3>
                    <p>{route.description}</p>
                  </div>
                  <span className="badge">{route.deliverable}</span>
                </div>

                <div className="capability-pill-row">
                  <span className="capability-pill">entry: {route.entry_tool || 'none'}</span>
                  <span className="capability-pill">inputs: {joinValues(route.input_channels)}</span>
                  <span className="capability-pill">tools: {route.available_tools.length}</span>
                </div>

                <div className="blueprint-step-list">
                  {route.steps.length ? (
                    route.steps.map((step) => (
                      <div key={`${route.name}-${step.tool_name}-${step.stage}`} className="blueprint-step-card">
                        <div className="item-topline">
                          <strong>{step.tool_name}</strong>
                          <span className="badge">{step.stage || 'stage'}</span>
                        </div>
                        <p>{step.description}</p>
                        {step.retryable ? <span className="badge">retryable</span> : null}
                      </div>
                    ))
                  ) : (
                    <div className="empty-state blueprint-empty-state">
                      <p>当前 route 还没有可执行的 step。</p>
                    </div>
                  )}
                </div>

                <div className="blueprint-note-block">
                  <span className="label">Failure Strategy</span>
                  <p>{route.failure_strategy || '当前 route 没有额外声明恢复策略。'}</p>
                </div>

                {route.sample_prompts.length ? (
                  <div className="blueprint-note-block">
                    <span className="label">Sample Prompt</span>
                    <p>{route.sample_prompts[0]}</p>
                  </div>
                ) : null}
              </article>
            ))
          ) : (
            <div className="empty-state blueprint-empty-state">
              <p>manifest 已加载，但当前工作区还没有 route 蓝图。</p>
            </div>
          )}
        </div>

        <section className="blueprint-tool-panel">
          <div className="panel-header panel-header-inline">
            <div>
              <h3>Tool Contract</h3>
              <p>这里显示工具输入、产物和 route 归属，便于说明为什么这个项目是受治理的 agent，而不是单次 prompt 调用。</p>
            </div>
            <span className="badge">{workspaceTools.length} tools</span>
          </div>

          <div className="blueprint-tool-list">
            {workspaceTools.length ? (
              workspaceTools.map((tool) => (
                <article key={tool.name} className="blueprint-tool-card">
                  <div className="item-topline">
                    <strong>{tool.name}</strong>
                    <span className={`badge ${tool.status === 'reserved' ? '' : 'status-completed'}`}>{tool.status}</span>
                  </div>
                  <p>{tool.description}</p>
                  <div className="capability-pill-row">
                    {tool.supports_routes.map((routeName) => (
                      <span key={`${tool.name}-${routeName}`} className="capability-pill">
                        {routeName}
                      </span>
                    ))}
                  </div>
                  <div className="tool-contract-grid">
                    <div className="tool-contract-card">
                      <span className="label">Consumes</span>
                      <strong>{joinValues(tool.consumes)}</strong>
                    </div>
                    <div className="tool-contract-card">
                      <span className="label">Artifacts</span>
                      <strong>{joinValues(tool.produces_artifacts)}</strong>
                    </div>
                  </div>
                </article>
              ))
            ) : (
              <div className="empty-state blueprint-empty-state">
                <p>当前工作区没有可展示的 tool contract。</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </section>
  )
}
