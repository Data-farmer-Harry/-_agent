from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.schemas import ArtifactKind, ArtifactRef, TaskRouteName, ToolCatalogEntry, WorkspaceId, WorkspaceStatus


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    workspace_id: WorkspaceId = "generic"
    status: WorkspaceStatus = "active"
    supports_routes: tuple[TaskRouteName, ...] = ()
    tags: tuple[str, ...] = ()
    produces_artifacts: tuple[ArtifactKind, ...] = ()
    consumes: tuple[str, ...] = ()


@dataclass
class ToolExecutionResult:
    success: bool
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    state_delta: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    name: str
    description: str
    workspace_id: WorkspaceId = "generic"
    status: WorkspaceStatus = "active"
    supports_routes: tuple[TaskRouteName, ...] = ()
    tags: tuple[str, ...] = ()
    produces_artifacts: tuple[ArtifactKind, ...] = ()
    consumes: tuple[str, ...] = ()

    def describe(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=self.description,
            workspace_id=self.workspace_id,
            status=self.status,
            supports_routes=self.supports_routes,
            tags=self.tags,
            produces_artifacts=self.produces_artifacts,
            consumes=self.consumes,
        )

    def catalog_entry(self) -> ToolCatalogEntry:
        descriptor = self.describe()
        return ToolCatalogEntry(
            name=descriptor.name,
            description=descriptor.description,
            workspace_id=descriptor.workspace_id,
            status=descriptor.status,
            supports_routes=list(descriptor.supports_routes),
            tags=list(descriptor.tags),
            produces_artifacts=list(descriptor.produces_artifacts),
            consumes=list(descriptor.consumes),
        )

    @abstractmethod
    def run(self, input_data: dict[str, Any], context: dict[str, Any]) -> ToolExecutionResult:
        raise NotImplementedError
