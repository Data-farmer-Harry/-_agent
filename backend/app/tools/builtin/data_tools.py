from __future__ import annotations

import csv
import json
import re
from io import StringIO
from pathlib import Path
from typing import Any

from app.tools.builtin.file_tools import _decode_data_url, _resolve_safe_path
from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


def _load_text(arguments: dict[str, Any], context: ToolContext) -> tuple[str, dict[str, Any]]:
    if text := str(arguments.get("text") or "").strip():
        return text, {"source": "inline_text", "format": str(arguments.get("format") or "auto")}
    if path_value := str(arguments.get("path") or "").strip():
        path = _resolve_safe_path(path_value, context)
        return path.read_text(encoding="utf-8", errors="replace"), {
            "source": "path",
            "path": str(path),
            "format": path.suffix.lower().lstrip(".") or path.name.lower(),
            "size_bytes": path.stat().st_size,
        }
    assets = context.uploaded_assets
    if not assets:
        raise ValueError("data.profile 需要 path、text 或上传文件。")
    asset = assets[0]
    text = _decode_data_url(asset.data_url).decode("utf-8", errors="replace")
    return text, {"source": "uploaded_asset", "name": asset.name, "format": Path(asset.name).suffix.lower().lstrip(".")}


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 8),
        "first": values[0],
        "last": values[-1],
        "delta": round(values[-1] - values[0], 8),
    }


def _profile_rows(columns: list[str], rows: list[list[str]], *, max_columns: int = 60) -> dict[str, Any]:
    numeric: dict[str, list[float]] = {column: [] for column in columns[:max_columns]}
    for row in rows:
        for index, column in enumerate(columns[:max_columns]):
            if index >= len(row):
                continue
            value = _to_float(row[index])
            if value is not None:
                numeric[column].append(value)
    return {
        "columns": columns,
        "row_count": len(rows),
        "numeric_columns": {
            column: _numeric_summary(values)
            for column, values in numeric.items()
            if values
        },
        "preview_rows": rows[:5],
    }


def _profile_csv(text: str, *, max_rows: int) -> dict[str, Any]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(StringIO(text), dialect)
    rows = [row for _, row in zip(range(max_rows + 1), reader)]
    if not rows:
        return {"type": "csv", "row_count": 0, "columns": []}
    columns = [value.strip() or f"column_{index + 1}" for index, value in enumerate(rows[0])]
    return {"type": "csv", **_profile_rows(columns, rows[1:], max_columns=80)}


def _profile_json(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if isinstance(payload, list):
        keys: list[str] = []
        for item in payload[:50]:
            if isinstance(item, dict):
                for key in item:
                    if key not in keys:
                        keys.append(key)
        return {
            "type": "json",
            "top_level": "array",
            "length": len(payload),
            "object_keys": keys[:80],
            "preview": payload[:3],
        }
    if isinstance(payload, dict):
        return {
            "type": "json",
            "top_level": "object",
            "keys": list(payload.keys())[:80],
            "preview": {key: payload[key] for key in list(payload.keys())[:10]},
        }
    return {"type": "json", "top_level": type(payload).__name__, "preview": payload}


def _profile_lammps_thermo(text: str, *, max_rows: int) -> dict[str, Any]:
    headers: list[str] = []
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split()
        if len(fields) >= 2 and fields[0].lower() in {"step", "time"} and any(any(ch.isalpha() for ch in field) for field in fields):
            headers = fields
            rows = []
            continue
        if headers and len(fields) >= min(2, len(headers)):
            if _to_float(fields[0]) is not None:
                rows.append(fields[: len(headers)])
                if len(rows) >= max_rows:
                    break
    if not headers:
        raise ValueError("未识别到 LAMMPS thermo 表头。")
    alerts = [line.strip() for line in text.splitlines() if "ERROR:" in line or "WARNING:" in line][:20]
    return {
        "type": "lammps_thermo",
        **_profile_rows(headers, rows),
        "alerts": alerts,
    }


def _profile_whitespace_table(text: str, *, max_rows: int) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {"type": "text", "row_count": 0}
    header_index = 0
    for index, line in enumerate(lines[:20]):
        fields = line.split()
        if len(fields) >= 2 and any(re.search(r"[A-Za-z_]", field) for field in fields):
            header_index = index
            break
    columns = lines[header_index].split()
    rows = [line.split() for line in lines[header_index + 1 : header_index + 1 + max_rows] if line.split()]
    return {"type": "whitespace_table", **_profile_rows(columns, rows)}


def _data_profile(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    text, metadata = _load_text(arguments, context)
    max_rows = max(10, min(100_000, int(arguments.get("max_rows", 20_000))))
    requested_format = str(arguments.get("format") or metadata.get("format") or "auto").lower()
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if requested_format in {"log", "lammps", "lammps_log"} or "Step" in text[:2000] and "Temp" in text[:2000]:
        profile = _profile_lammps_thermo(text, max_rows=max_rows)
    elif requested_format == "json" or text.lstrip().startswith(("{", "[")):
        profile = _profile_json(text)
    elif requested_format == "csv" or "," in first_line:
        profile = _profile_csv(text, max_rows=max_rows)
    else:
        profile = _profile_whitespace_table(text, max_rows=max_rows)
    numeric_count = len(profile.get("numeric_columns", {})) if isinstance(profile.get("numeric_columns"), dict) else 0
    return ToolResult(
        tool_name="data.profile",
        success=True,
        summary=f"数据概况完成：type={profile.get('type')}，数值列 {numeric_count} 个。",
        output={
            "metadata": metadata,
            "char_count": len(text),
            "line_count": len(text.splitlines()),
            "profile": profile,
        },
    )


def register_data_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="data.profile",
            description="Profile CSV, JSON, whitespace numeric tables, and LAMMPS thermo logs with row counts, columns, numeric min/max/mean/first/last/delta, and alerts.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                    "uploaded_asset": {"type": "string"},
                    "format": {"type": "string", "enum": ["auto", "csv", "json", "log", "lammps_log", "table"]},
                    "max_rows": {"type": "integer", "minimum": 10, "maximum": 100000, "default": 20000},
                },
            },
            risk=ToolRisk.WORKSPACE_READ,
            read_only=True,
        ),
        _data_profile,
    )
