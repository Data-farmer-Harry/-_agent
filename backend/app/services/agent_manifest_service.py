from __future__ import annotations

from app.schemas import AgentManifestResponse, RouteBlueprint, RouteBlueprintStep
from app.services.agent_catalog import AgentCatalogService
from app.services.agent_manifest import list_route_definitions
from app.services.tool_registry import ToolRegistry


class AgentManifestService:
    def __init__(self, catalog_service: AgentCatalogService) -> None:
        self.catalog_service = catalog_service

    def build_manifest(self, tool_registry: ToolRegistry) -> AgentManifestResponse:
        catalog = self.catalog_service.build_catalog(tool_registry)
        routes = sorted(list_route_definitions(), key=lambda definition: (definition.workspace_id, definition.name))

        return AgentManifestResponse(
            workspaces=catalog.workspaces,
            routes=[
                RouteBlueprint(
                    name=route.name,
                    workspace_id=route.workspace_id,
                    entry_tool=route.entry_tool,
                    description=route.description,
                    default_reason=route.default_reason,
                    input_channels=list(route.input_channels),
                    deliverable=route.deliverable,
                    available_tools=list(route.available_tools),
                    reserved_tools=list(route.reserved_tools),
                    failure_strategy=route.failure_strategy,
                    sample_prompts=list(route.sample_prompts),
                    steps=[
                        RouteBlueprintStep(
                            tool_name=step.tool_name,
                            stage=step.stage,
                            description=step.description,
                            retryable=step.retryable,
                            input_overrides=dict(step.input_overrides),
                        )
                        for step in route.step_templates
                    ],
                )
                for route in routes
            ],
            tools=sorted(catalog.tools, key=lambda tool: (tool.workspace_id, tool.name)),
        )
