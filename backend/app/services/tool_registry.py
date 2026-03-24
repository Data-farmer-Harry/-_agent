from __future__ import annotations

from app.schemas import ToolCatalogEntry
from app.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def list_for_workspace(self, workspace_id: str) -> list[BaseTool]:
        return [tool for tool in self._tools.values() if tool.workspace_id == workspace_id]

    def describe_tools(self) -> list[ToolCatalogEntry]:
        return [tool.catalog_entry() for tool in self._tools.values()]
