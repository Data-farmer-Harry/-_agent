from __future__ import annotations

import base64
import csv
import json
import re
from io import StringIO
from pathlib import Path
from typing import Any

from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".in",
    ".lmp",
    ".dump",
    ".data",
    ".cif",
    ".xyz",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_safe_path(path_value: str, context: ToolContext) -> Path:
    path = Path(path_value).expanduser()
    candidates = [path]
    if not path.is_absolute() and context.project_root is not None:
        candidates.append(context.project_root / path)
        candidates.append(context.project_root / "backend" / path)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and any(_is_within(resolved, root) for root in context.allowed_roots):
            return resolved
    raise ValueError("file.read 只能读取项目工作区或 backend/outputs 内存在的文件。")


def _decode_data_url(data_url: str) -> bytes:
    _, _, payload = data_url.partition(",")
    if not payload:
        return b""
    return base64.b64decode(payload)


def _read_uploaded_text(context: ToolContext, *, asset_name: str = "") -> tuple[str, dict[str, Any]]:
    assets = context.uploaded_assets
    if asset_name:
        assets = [asset for asset in assets if asset.name == asset_name or asset.asset_id == asset_name]
    if not assets:
        raise ValueError("没有找到可读取的上传文件。")
    asset = assets[0]
    raw = _decode_data_url(asset.data_url)
    text = raw.decode("utf-8", errors="replace")
    return text, {
        "source": "uploaded_asset",
        "asset_id": asset.asset_id,
        "name": asset.name,
        "media_type": asset.media_type,
        "size_bytes": asset.size_bytes,
    }


def _read_path_text(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency branch
            raise ValueError("PDF 解析需要可用的 pypdf 依赖；当前环境未检测到。") from exc
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages[:30]]
        return "\n\n".join(pages), {"source": "path", "path": str(path), "format": "pdf", "page_count": len(reader.pages)}

    if suffix not in TEXT_SUFFIXES and path.name.upper() not in {"POSCAR", "CONTCAR"}:
        raise ValueError(f"暂不支持读取该文件类型：{suffix or path.name}")
    return path.read_text(encoding="utf-8", errors="replace"), {
        "source": "path",
        "path": str(path),
        "format": suffix.lstrip(".") or path.name.lower(),
        "size_bytes": path.stat().st_size,
    }


def _summarize_csv(text: str) -> dict[str, Any]:
    reader = csv.reader(StringIO(text))
    rows = []
    for index, row in enumerate(reader):
        rows.append(row)
        if index >= 10:
            break
    if not rows:
        return {"type": "csv", "columns": [], "preview_rows": []}
    return {"type": "csv", "columns": rows[0], "preview_rows": rows[1:6], "preview_row_count": max(0, len(rows) - 1)}


def _summarize_json(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if isinstance(payload, dict):
        return {"type": "json", "top_level_type": "object", "keys": list(payload.keys())[:30]}
    if isinstance(payload, list):
        return {"type": "json", "top_level_type": "array", "length": len(payload), "first_item_type": type(payload[0]).__name__ if payload else ""}
    return {"type": "json", "top_level_type": type(payload).__name__}


def _summarize_lammps_log(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    headers: list[str] = []
    thermo_rows = 0
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and fields[0].lower() in {"step", "time"}:
            headers = fields
            continue
        if headers and fields and re.match(r"^-?\d+(?:\.\d+)?$", fields[0]):
            thermo_rows += 1
    errors = [line.strip() for line in lines if "ERROR:" in line or "WARNING:" in line][:10]
    return {"type": "lammps_log", "thermo_headers": headers, "thermo_row_count": thermo_rows, "alerts": errors}


def _file_read(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    max_chars = max(1000, min(60000, int(arguments.get("max_chars", 12000))))
    path_value = str(arguments.get("path") or "").strip()
    asset_name = str(arguments.get("uploaded_asset") or "").strip()
    if path_value:
        path = _resolve_safe_path(path_value, context)
        text, metadata = _read_path_text(path)
    else:
        text, metadata = _read_uploaded_text(context, asset_name=asset_name)

    preview = text[:max_chars]
    truncated = len(text) > max_chars
    summary_payload: dict[str, Any] = {
        "line_count": len(text.splitlines()),
        "char_count": len(text),
        "truncated": truncated,
    }
    source_format = str(metadata.get("format") or "").lower()
    try:
        if source_format == "csv":
            summary_payload["structured_preview"] = _summarize_csv(text)
        elif source_format == "json":
            summary_payload["structured_preview"] = _summarize_json(text)
        elif source_format in {"log", "lmp", "in", "dump", "data"} or "lammps" in text[:1000].lower():
            summary_payload["structured_preview"] = _summarize_lammps_log(text)
    except Exception as exc:  # noqa: BLE001
        summary_payload["structured_preview_error"] = str(exc)

    return ToolResult(
        tool_name="file.read",
        success=True,
        summary=f"已读取文件内容：{metadata.get('name') or metadata.get('path') or 'uploaded_asset'}。",
        output={
            "metadata": metadata,
            "preview": preview,
            **summary_payload,
        },
        metadata={"truncated": truncated},
    )


def register_file_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="file.read",
            description="Read and lightly summarize a workspace/uploaded text-like file, including logs, CSV, JSON, CIF, POSCAR, XYZ, LAMMPS input/data/dump files, and PDF when pypdf is available.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path inside the project workspace or backend outputs."},
                    "uploaded_asset": {"type": "string", "description": "Optional uploaded asset id/name when no path is provided."},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 60000, "default": 12000},
                },
            },
            risk=ToolRisk.WORKSPACE_READ,
            read_only=True,
        ),
        _file_read,
    )
