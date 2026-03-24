from __future__ import annotations

from dataclasses import dataclass

from app.schemas import AgentCatalogResponse, AgentRunRequest, TaskRouteName, ToolCatalogEntry, WorkspaceId, WorkspaceSummary
from app.services.agent_manifest import (
    LAMMPS_RESERVED_TOOLS,
    get_route_definition,
    list_route_names,
    list_workspace_definitions,
)
from app.services.tool_registry import ToolRegistry


PHASE_DIAGRAM_KEYWORDS = (
    "phase diagram",
    "相图",
    "binodal",
    "eutectic",
    "coexistence",
)

LAMMPS_KEYWORDS = (
    "lammps",
    "molecular dynamics",
    "md simulation",
    "atomistic",
    "potentials",
)


@dataclass(frozen=True)
class RouteDecision:
    name: TaskRouteName
    workspace_id: WorkspaceId
    reason: str
    selected_tool: str | None
    available_tools: list[str]
    reserved_tools: list[str]
    entry_tool: str | None
    input_channels: list[str]
    deliverable: str
    narrative: str


class AgentCatalogService:
    def infer_workspace_id(self, request: AgentRunRequest) -> WorkspaceId:
        if request.workspace_hint in {"phase_diagram", "lammps"}:
            return request.workspace_hint

        text = " ".join(
            value
            for value in (
                request.task_type_hint or "",
                request.user_input,
                request.diagram_request.system_name if request.diagram_request else "",
                request.diagram_request.notes if request.diagram_request else "",
                request.image_diagram_request.system_name if request.image_diagram_request else "",
                request.image_diagram_request.chart_title if request.image_diagram_request else "",
                request.image_diagram_request.notes if request.image_diagram_request else "",
                request.html_redraw_request.system_name if request.html_redraw_request else "",
                request.html_redraw_request.chart_title if request.html_redraw_request else "",
                request.html_redraw_request.notes if request.html_redraw_request else "",
            )
            if value
        ).lower()

        if request.diagram_request is not None or request.image_diagram_request is not None or request.html_redraw_request is not None or any(keyword in text for keyword in PHASE_DIAGRAM_KEYWORDS):
            return "phase_diagram"
        if any(keyword in text for keyword in LAMMPS_KEYWORDS):
            return "lammps"
        return "generic"

    def build_catalog(self, tool_registry: ToolRegistry) -> AgentCatalogResponse:
        registered_tools = {tool.name: tool for tool in tool_registry.describe_tools()}
        workspaces: list[WorkspaceSummary] = []

        for workspace_definition in list_workspace_definitions():
            available_tools = [
                tool_name
                for tool_name in registered_tools
                if registered_tools[tool_name].workspace_id == workspace_definition.id and registered_tools[tool_name].status == "active"
            ]
            reserved_tools = list(workspace_definition.reserved_tools)
            status = workspace_definition.status
            if workspace_definition.id == "lammps" and not available_tools:
                status = "reserved"
            workspaces.append(
                WorkspaceSummary(
                    id=workspace_definition.id,
                    title=workspace_definition.title,
                    description=workspace_definition.description,
                    status=status,
                    available_tools=available_tools,
                    reserved_tools=reserved_tools,
                    default_route=workspace_definition.default_route,
                    supported_routes=list(workspace_definition.supported_routes),
                )
            )

        tools = list(registered_tools.values())
        for tool_name in LAMMPS_RESERVED_TOOLS:
            if tool_name in registered_tools:
                continue
            tools.append(
                ToolCatalogEntry(
                    name=tool_name,
                    description="Reserved tool slot for the future LAMMPS agent.",
                    workspace_id="lammps",
                    status="reserved",
                    supports_routes=["lammps.generate", "lammps.repair"],
                    tags=["reserved", "lammps"],
                )
            )

        return AgentCatalogResponse(
            workspaces=workspaces,
            tools=tools,
            supported_routes=sorted(list_route_names()),
        )

    def build_route_decision(self, route_name: TaskRouteName, reason: str | None = None) -> RouteDecision:
        definition = get_route_definition(route_name)
        narrative = definition.description
        return RouteDecision(
            name=definition.name,
            workspace_id=definition.workspace_id,
            reason=reason or definition.default_reason,
            selected_tool=definition.entry_tool,
            available_tools=list(definition.available_tools),
            reserved_tools=list(definition.reserved_tools),
            entry_tool=definition.entry_tool,
            input_channels=list(definition.input_channels),
            deliverable=definition.deliverable,
            narrative=narrative,
        )

    def decide_route(self, request: AgentRunRequest) -> RouteDecision:
        workspace_id = self.infer_workspace_id(request)
        task_type_hint = (request.task_type_hint or "").strip()

        if task_type_hint in list_route_names():
            hinted_definition = get_route_definition(task_type_hint)
            if hinted_definition.workspace_id == "phase_diagram":
                return self.build_route_decision(task_type_hint, "Using explicit phase-diagram task_type_hint from the request.")
            if hinted_definition.workspace_id == "lammps":
                return self.build_route_decision(
                    task_type_hint,
                    "Using explicit LAMMPS task_type_hint from the request; the stub router is available while execution tools remain reserved.",
                )
            return self.build_route_decision(task_type_hint, "Using explicit task_type_hint from the request.")

        if workspace_id == "phase_diagram":
            if request.html_redraw_request is not None and request.image_diagram_request is None and request.diagram_request is None:
                return self.build_route_decision("phase_diagram.redraw_html")
            if request.image_diagram_request is not None:
                return self.build_route_decision("phase_diagram.recognize")
            return self.build_route_decision("phase_diagram.generate")

        if workspace_id == "lammps":
            return self.build_route_decision("lammps.generate")

        return self.build_route_decision("generic.unknown")
