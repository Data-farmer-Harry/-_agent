from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.tools.mcp_client import ExternalMcpClient, ExternalMcpServerConfig, load_external_mcp_configs
from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


def _normalize_tool_name(prefix: str, tool_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name.strip())
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix.strip()).strip(".")
    return f"{safe_prefix}.{safe_name}" if safe_prefix else safe_name


def _risk_from_config(config: ExternalMcpServerConfig) -> ToolRisk:
    try:
        return ToolRisk(config.risk)
    except ValueError:
        return ToolRisk.NETWORK


def _parse_mcp_content(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content", [])
    parsed_items: list[Any] = []
    text_items: list[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text = str(item.get("text") or "")
                text_items.append(text)
                try:
                    parsed_items.append(json.loads(text))
                except Exception:
                    parsed_items.append(text)
            else:
                parsed_items.append(item)
    return {
        "raw_result": result,
        "content_text": "\n".join(text_items),
        "parsed_content": parsed_items,
        "is_error": bool(result.get("isError")),
    }


def _handler_for(config: ExternalMcpServerConfig, external_tool_name: str):
    def handler(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        client = ExternalMcpClient(config)
        result = client.call_tool(external_tool_name, arguments)
        payload = _parse_mcp_content(result)
        is_error = bool(payload.get("is_error"))
        return ToolResult(
            tool_name=_normalize_tool_name(config.resolved_tool_prefix, external_tool_name),
            success=not is_error,
            summary=(
                f"外部 MCP 工具 {config.name}/{external_tool_name} 已返回结果。"
                if not is_error
                else f"外部 MCP 工具 {config.name}/{external_tool_name} 返回错误。"
            ),
            output={
                "server": config.name,
                "external_tool_name": external_tool_name,
                **payload,
            },
            metadata={
                "source": "external_mcp",
                "server": config.name,
                "external_tool_name": external_tool_name,
            },
            error=str(payload.get("content_text") or "") if is_error else "",
        )

    return handler


def register_external_mcp_tools(
    registry: ToolRegistry,
    *,
    config_path: Path | None = None,
    strict: bool | None = None,
) -> list[dict[str, Any]]:
    strict_mode = strict if strict is not None else os.environ.get("PHASE_DIAGRAM_EXTERNAL_MCP_STRICT", "").lower() in {"1", "true", "yes"}
    registrations: list[dict[str, Any]] = []
    for config in load_external_mcp_configs(config_path):
        if not config.enabled:
            continue
        try:
            client = ExternalMcpClient(config)
            tools = client.list_tools()
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                external_name = str(tool.get("name") or "").strip()
                if not external_name:
                    continue
                internal_name = _normalize_tool_name(config.resolved_tool_prefix, external_name)
                if registry.has(internal_name):
                    registrations.append({"server": config.name, "tool": external_name, "registered": False, "reason": "duplicate"})
                    continue
                input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
                registry.register(
                    ToolSpec(
                        name=internal_name,
                        description=str(tool.get("description") or f"External MCP tool {config.name}/{external_name}"),
                        input_schema=input_schema,
                        risk=_risk_from_config(config),
                        read_only=config.read_only,
                        output_kind="json",
                        auto_execute=config.read_only,
                        metadata={
                            "source": "external_mcp",
                            "server": config.name,
                            "external_tool_name": external_name,
                            "transport": config.transport,
                            "tool_prefix": config.resolved_tool_prefix,
                        },
                    ),
                    _handler_for(config, external_name),
                )
                registrations.append({"server": config.name, "tool": external_name, "registered": True, "internal_name": internal_name})
        except Exception as exc:  # noqa: BLE001 - optional external tools must not break local startup.
            registrations.append({"server": config.name, "registered": False, "error": str(exc)})
            if strict_mode:
                raise
    return registrations
