from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.tools.models import ToolCall, ToolDecision
from app.tools.policy_rules import build_learned_rule_calls, extract_tool_policy_features
from app.tools.registry import ToolRegistry


def _target_structure_format(message: str) -> str:
    lowered = message.lower()
    if "lammps" in lowered or "data" in lowered:
        return "lammps_data"
    if "poscar" in lowered or "vasp" in lowered:
        return "poscar"
    if "xyz" in lowered:
        return "xyz"
    if "cif" in lowered:
        return "cif"
    return "lammps_data"


def _source_structure_format(message: str) -> str:
    lowered = message.lower()
    if "poscar" in lowered or "contcar" in lowered:
        return "poscar"
    if ".xyz" in lowered or " xyz" in lowered:
        return "xyz"
    if ".data" in lowered or "lammps data" in lowered:
        return "lammps_data"
    if ".cif" in lowered or "cif" in lowered:
        return "cif"
    return "auto"


def _top_k(message: str, default: int = 5) -> int:
    match = re.search(r"(?:top[_\s-]?k|前|找|查)\s*(\d{1,2})", message, flags=re.IGNORECASE)
    if not match:
        return default
    return max(1, min(10, int(match.group(1))))


@dataclass
class ToolRouter:
    registry: ToolRegistry

    def decide(self, state: dict[str, Any]) -> ToolDecision:
        request = state["request"]
        route = state.get("route")
        compute_domain = getattr(route, "compute_domain", state.get("compute_domain", "none"))
        has_compute_result = bool(state.get("phase_diagram_result") or state.get("lammps_result"))
        features = extract_tool_policy_features(
            request.message,
            has_uploaded_assets=bool(state.get("uploaded_assets")),
            has_last_run=bool(state.get("last_run_context") and state.get("last_run_context").run_id),
        )
        available_tools = {spec.name for spec in self.registry.list_specs()}
        calls: list[ToolCall] = build_learned_rule_calls(features, available_tools)

        # Keep ordinary explanations cheap and tool-free. Compute requests should
        # not be hijacked by generic tools unless the user explicitly asks for an
        # add-on artifact such as a report.
        allow_generic_tools = str(compute_domain) == "none" or has_compute_result

        if allow_generic_tools and features.explicit_workspace_search and "workspace.search" in available_tools:
            calls.append(
                ToolCall(
                    tool_name="workspace.search",
                    arguments={"query": features.extracted_path or features.extracted_query, "top_k": _top_k(request.message, default=20)},
                    reason="用户显式要求在项目工作区查找文件或代码内容。",
                    confidence=0.86,
                )
            )

        if allow_generic_tools and features.explicit_file_read and "file.read" in available_tools:
            args: dict[str, Any] = {"max_chars": 12000}
            if features.extracted_path:
                args["path"] = features.extracted_path
            calls.append(
                ToolCall(
                    tool_name="file.read",
                    arguments=args,
                    reason="用户显式要求读取/解析文件或上传内容。",
                    confidence=0.88,
                )
            )

        if allow_generic_tools and features.explicit_data_profile and "data.profile" in available_tools:
            args = {"max_rows": 20000}
            if features.extracted_path:
                args["path"] = features.extracted_path
            else:
                args["text"] = request.message
            calls.append(
                ToolCall(
                    tool_name="data.profile",
                    arguments=args,
                    reason="用户显式要求对数据表、CSV 或 LAMMPS thermo log 做概况统计。",
                    confidence=0.84,
                )
            )

        if allow_generic_tools and features.explicit_structure_conversion and "structure.convert" in available_tools:
            args = {
                "source_format": _source_structure_format(request.message),
                "target_format": _target_structure_format(request.message),
            }
            if features.extracted_path:
                args["path"] = features.extracted_path
            calls.append(
                ToolCall(
                    tool_name="structure.convert",
                    arguments=args,
                    reason="用户显式要求材料结构格式转换。",
                    confidence=0.86,
                )
            )

        if allow_generic_tools and features.explicit_physics_check and "physics.check" in available_tools:
            calls.append(
                ToolCall(
                    tool_name="physics.check",
                    arguments={"text": request.message},
                    reason="用户显式要求单位换算或物理参数合理性检查。",
                    confidence=0.82,
                )
            )

        if features.explicit_report and "report.generate" in available_tools:
            calls.append(
                ToolCall(
                    tool_name="report.generate",
                    arguments={"title": "MatterPilot 任务报告"},
                    reason="用户显式要求生成报告/实验记录。",
                    confidence=0.9,
                )
            )

        if allow_generic_tools and features.explicit_literature_search and "literature.search" in available_tools:
            calls.append(
                ToolCall(
                    tool_name="literature.search",
                    arguments={"query": features.extracted_query, "top_k": _top_k(request.message)},
                    reason="用户显式要求文献/论文/引用检索。",
                    confidence=0.84,
                )
            )

        deduped: list[ToolCall] = []
        seen: set[str] = set()
        for call in calls:
            if call.tool_name in seen:
                continue
            if call.tool_name not in available_tools:
                continue
            seen.add(call.tool_name)
            deduped.append(call)

        if not deduped:
            return ToolDecision(
                need_tool=False,
                selected_calls=[],
                allowed_tools=sorted(available_tools),
                confidence=0.72,
                reason="未检测到明确工具触发意图，保持普通 agent 对话路径。",
            )

        requires_confirmation = any(call.requires_confirmation for call in deduped)
        return ToolDecision(
            need_tool=True,
            selected_calls=deduped,
            allowed_tools=sorted({call.tool_name for call in deduped}),
            auto_execute=not requires_confirmation,
            requires_confirmation=requires_confirmation,
            confidence=max(call.confidence for call in deduped),
            reason="; ".join(call.reason for call in deduped),
        )
