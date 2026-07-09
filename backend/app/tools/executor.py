from __future__ import annotations

from dataclasses import dataclass

from app.config import PROJECT_ROOT, settings
from app.tools.models import ToolCall, ToolContext, ToolResult
from app.tools.registry import ToolRegistry


@dataclass
class ToolExecutor:
    registry: ToolRegistry

    def build_context(self, state: dict, artifact_service) -> ToolContext:  # noqa: ANN001
        return ToolContext(
            run_id=state["run_id"],
            conversation_id=state.get("conversation_id", "default"),
            request_message=state["request"].message,
            artifact_service=artifact_service,
            uploaded_assets=state.get("uploaded_assets", []),
            last_run_context=state.get("last_run_context"),
            state=state,
            project_root=PROJECT_ROOT,
            allowed_roots=[PROJECT_ROOT, settings.tmp_dir],
        )

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        try:
            registered = self.registry.get(call.tool_name)
            if call.requires_confirmation and call.auto_execute:
                return ToolResult(
                    tool_name=call.tool_name,
                    success=False,
                    summary="该工具调用需要用户确认，已跳过自动执行。",
                    output={"requires_confirmation": True, "arguments": call.arguments},
                    error="requires_confirmation",
                )
            result = registered.handler(call.arguments, context)
            result.metadata = {
                **result.metadata,
                "tool_policy": {
                    "reason": call.reason,
                    "confidence": call.confidence,
                    "auto_execute": call.auto_execute,
                    "requires_confirmation": call.requires_confirmation,
                },
                "risk": registered.spec.risk.value,
            }
            return result
        except Exception as exc:  # noqa: BLE001 - tool failures must become traceable observations.
            return ToolResult(
                tool_name=call.tool_name,
                success=False,
                summary=f"{call.tool_name} 执行失败：{exc}",
                output={"arguments": call.arguments},
                error=str(exc),
            )
