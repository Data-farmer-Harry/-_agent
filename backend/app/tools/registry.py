from __future__ import annotations

from dataclasses import dataclass, field

from app.tools.models import ToolHandler, ToolSpec


@dataclass
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass
class ToolRegistry:
    _tools: dict[str, RegisteredTool] = field(default_factory=dict)

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_specs(self) -> list[ToolSpec]:
        return [item.spec for item in self._tools.values()]

    def public_catalog(self) -> list[dict[str, object]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "risk": spec.risk.value,
                "read_only": spec.read_only,
                "output_kind": spec.output_kind,
                "auto_execute": spec.auto_execute,
                "metadata": spec.metadata,
            }
            for spec in self.list_specs()
        ]


def build_default_tool_registry() -> ToolRegistry:
    from app.tools.builtin.data_tools import register_data_tools
    from app.tools.builtin.file_tools import register_file_tools
    from app.tools.builtin.literature_tools import register_literature_tools
    from app.tools.builtin.physics_tools import register_physics_tools
    from app.tools.builtin.report_tools import register_report_tools
    from app.tools.builtin.structure_tools import register_structure_tools
    from app.tools.builtin.workspace_tools import register_workspace_tools

    registry = ToolRegistry()
    register_workspace_tools(registry)
    register_file_tools(registry)
    register_data_tools(registry)
    register_structure_tools(registry)
    register_physics_tools(registry)
    register_report_tools(registry)
    register_literature_tools(registry)
    try:
        from app.tools.mcp_adapter import register_external_mcp_tools

        register_external_mcp_tools(registry)
    except Exception:
        # External MCP servers are optional and must never break local tools.
        # Tests can still exercise the adapter directly in strict mode.
        pass
    return registry
