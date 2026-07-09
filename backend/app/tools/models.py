from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from app.core.artifacts import ArtifactService
from app.state import ArtifactRef, UploadedAsset


JSONDict = dict[str, Any]


class ToolRisk(str, Enum):
    SAFE = "safe"
    WORKSPACE_READ = "workspace_read"
    NETWORK = "network"
    WRITE_ARTIFACT = "write_artifact"
    REQUIRES_CONFIRMATION = "requires_confirmation"


class ToolCallStatus(str, Enum):
    PLANNED = "planned"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: JSONDict
    risk: ToolRisk = ToolRisk.SAFE
    read_only: bool = True
    output_kind: str = "json"
    auto_execute: bool = True
    metadata: JSONDict = field(default_factory=dict)


@dataclass
class ToolCall:
    tool_name: str
    arguments: JSONDict = field(default_factory=dict)
    reason: str = ""
    auto_execute: bool = True
    requires_confirmation: bool = False
    confidence: float = 0.0


@dataclass
class ToolDecision:
    need_tool: bool
    selected_calls: list[ToolCall] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)
    auto_execute: bool = True
    requires_confirmation: bool = False
    confidence: float = 0.0
    reason: str = ""
    source: str = "tool_policy_rules"

    def model_dump(self) -> JSONDict:
        return {
            "need_tool": self.need_tool,
            "selected_calls": [
                {
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                    "reason": call.reason,
                    "auto_execute": call.auto_execute,
                    "requires_confirmation": call.requires_confirmation,
                    "confidence": call.confidence,
                }
                for call in self.selected_calls
            ],
            "allowed_tools": self.allowed_tools,
            "blocked_tools": self.blocked_tools,
            "auto_execute": self.auto_execute,
            "requires_confirmation": self.requires_confirmation,
            "confidence": self.confidence,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass
class ToolContext:
    run_id: str
    conversation_id: str
    request_message: str
    artifact_service: ArtifactService
    uploaded_assets: list[UploadedAsset] = field(default_factory=list)
    last_run_context: Any | None = None
    state: JSONDict = field(default_factory=dict)
    project_root: Path | None = None
    allowed_roots: list[Path] = field(default_factory=list)


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    summary: str
    output: JSONDict = field(default_factory=dict)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: JSONDict = field(default_factory=dict)
    error: str = ""

    def model_dump(self) -> JSONDict:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "summary": self.summary,
            "output": self.output,
            "artifacts": [artifact.model_dump(mode="json") for artifact in self.artifacts],
            "metadata": self.metadata,
            "error": self.error,
        }


ToolHandler = Callable[[JSONDict, ToolContext], ToolResult]
