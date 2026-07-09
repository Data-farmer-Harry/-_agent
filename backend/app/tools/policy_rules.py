from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.tools.models import ToolCall


FILE_PATH_PATTERN = re.compile(
    r"(?P<path>[\w.\-/\u4e00-\u9fff]*(?:\.pdf|\.md|\.txt|\.csv|\.json|\.log|\.dump|\.data|\.in|\.cif|POSCAR|CONTCAR|\.xyz))",
    flags=re.IGNORECASE,
)


@dataclass
class ToolPolicyFeatures:
    message: str
    lowered: str
    has_uploaded_assets: bool = False
    has_last_run: bool = False
    explicit_file_read: bool = False
    explicit_structure_conversion: bool = False
    explicit_physics_check: bool = False
    explicit_report: bool = False
    explicit_literature_search: bool = False
    explicit_workspace_search: bool = False
    explicit_data_profile: bool = False
    extracted_path: str = ""
    extracted_query: str = ""


@dataclass
class LearnedToolRule:
    name: str
    tool_name: str
    include_any: list[str] = field(default_factory=list)
    exclude_any: list[str] = field(default_factory=list)
    confidence: float = 0.8
    auto_execute: bool = True
    requires_confirmation: bool = False
    argument_defaults: dict[str, Any] = field(default_factory=dict)

    def matches(self, message: str) -> bool:
        lowered = message.lower()
        if self.include_any and not any(token.lower() in lowered for token in self.include_any):
            return False
        if self.exclude_any and any(token.lower() in lowered for token in self.exclude_any):
            return False
        return True


def extract_tool_policy_features(message: str, *, has_uploaded_assets: bool = False, has_last_run: bool = False) -> ToolPolicyFeatures:
    lowered = message.lower()
    path_match = FILE_PATH_PATTERN.search(message)
    explicit_file_read = bool(
        path_match
        and any(token in lowered or token in message for token in ("读取", "读一下", "解析", "提取", "总结", "read", "extract", "parse", "打开"))
    )
    if has_uploaded_assets and any(token in lowered or token in message for token in ("上传", "文件", "读取", "解析", "总结", "read", "extract")):
        explicit_file_read = True

    explicit_structure_conversion = any(
        token in lowered or token in message
        for token in (
            "转成lammps",
            "转成 lammps",
            "转换结构",
            "结构转换",
            "cif",
            "poscar",
            "contcar",
            "xyz",
            "lammps data",
            "convert structure",
            "structure convert",
        )
    ) and any(token in lowered or token in message for token in ("转换", "转成", "convert", "导出", "生成"))

    explicit_physics_check = any(
        token in lowered or token in message
        for token in (
            "单位",
            "换算",
            "合理吗",
            "合不合理",
            "检查参数",
            "检查一下参数",
            "物理校验",
            "timestep",
            "time step",
            "pressure",
            "压力",
            "步数",
            "units",
            "unit",
            "physics check",
        )
    ) and not explicit_structure_conversion

    explicit_report = any(
        token in lowered or token in message
        for token in ("报告", "总结成 markdown", "生成markdown", "实验记录", "report", "write-up", "markdown report")
    )

    explicit_literature_search = any(
        token in lowered or token in message
        for token in (
            "查文献",
            "找文献",
            "检索文献",
            "论文",
            "paper",
            "papers",
            "literature",
            "crossref",
            "doi",
            "引用",
            "citation",
        )
    )

    explicit_workspace_search = any(
        token in lowered or token in message
        for token in (
            "搜索文件",
            "查找文件",
            "找文件",
            "搜一下项目",
            "搜索项目",
            "在哪个文件",
            "在哪个代码",
            "搜代码",
            "grep",
            "workspace search",
            "search workspace",
        )
    )

    explicit_data_profile = any(
        token in lowered or token in message
        for token in (
            "统计数据",
            "数据概况",
            "剖析数据",
            "分析数据",
            "分析这个csv",
            "分析这个 csv",
            "分析这个log",
            "分析这个 log",
            "thermo log",
            "thermo表",
            "thermo 表",
            "profile data",
            "data profile",
        )
    )

    return ToolPolicyFeatures(
        message=message,
        lowered=lowered,
        has_uploaded_assets=has_uploaded_assets,
        has_last_run=has_last_run,
        explicit_file_read=explicit_file_read,
        explicit_structure_conversion=explicit_structure_conversion,
        explicit_physics_check=explicit_physics_check,
        explicit_report=explicit_report,
        explicit_literature_search=explicit_literature_search,
        explicit_workspace_search=explicit_workspace_search,
        explicit_data_profile=explicit_data_profile,
        extracted_path=path_match.group("path").strip() if path_match else "",
        extracted_query=message.strip(),
    )


def load_learned_tool_rules() -> list[LearnedToolRule]:
    """Load optional learned/RL tool-routing rules from JSON.

    The JSON path is intentionally externalized so trained policies can be
    dropped into a local env/config without hard-coding experimental rules into
    the core agent. Expected format:

    [
      {
        "name": "read_uploaded_logs",
        "tool_name": "file.read",
        "include_any": ["读取", "log"],
        "exclude_any": ["不要读取"],
        "confidence": 0.92,
        "argument_defaults": {"max_chars": 12000}
      }
    ]
    """

    path_value = os.environ.get("PHASE_DIAGRAM_TOOL_POLICY_RULES_PATH", "").strip()
    if not path_value:
        return []
    path = Path(path_value).expanduser()
    if not path.exists() or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    rules: list[LearnedToolRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        name = str(item.get("name") or tool_name).strip()
        if not tool_name:
            continue
        rules.append(
            LearnedToolRule(
                name=name,
                tool_name=tool_name,
                include_any=[str(token) for token in item.get("include_any", []) if str(token).strip()]
                if isinstance(item.get("include_any"), list)
                else [],
                exclude_any=[str(token) for token in item.get("exclude_any", []) if str(token).strip()]
                if isinstance(item.get("exclude_any"), list)
                else [],
                confidence=float(item.get("confidence", 0.8)),
                auto_execute=bool(item.get("auto_execute", True)),
                requires_confirmation=bool(item.get("requires_confirmation", False)),
                argument_defaults=item.get("argument_defaults", {}) if isinstance(item.get("argument_defaults"), dict) else {},
            )
        )
    return rules


def build_learned_rule_calls(features: ToolPolicyFeatures, available_tools: set[str]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for rule in load_learned_tool_rules():
        if rule.tool_name not in available_tools or not rule.matches(features.message):
            continue
        calls.append(
            ToolCall(
                tool_name=rule.tool_name,
                arguments={**rule.argument_defaults},
                reason=f"learned_policy:{rule.name}",
                auto_execute=rule.auto_execute,
                requires_confirmation=rule.requires_confirmation,
                confidence=rule.confidence,
            )
        )
    return calls
