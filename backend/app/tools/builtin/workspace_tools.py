from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".log",
    ".csv",
    ".cif",
    ".xyz",
    ".data",
    ".in",
}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_search_root(path_value: str, context: ToolContext) -> Path:
    if not context.project_root:
        raise ValueError("workspace.search requires a project root.")
    if not path_value:
        return context.project_root
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = context.project_root / path
    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"搜索目录不存在：{resolved}")
    if not any(_is_within(resolved, root) for root in context.allowed_roots):
        raise ValueError("workspace.search 只能搜索项目工作区或 backend/outputs。")
    return resolved


def _iter_candidate_files(root: Path, *, max_files: int = 4000):
    if root.is_file():
        yield root
        return
    count = 0
    for path in root.rglob("*"):
        if count >= max_files:
            break
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        count += 1
        yield path


def _safe_text(path: Path, *, max_bytes: int = 2_000_000) -> str:
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name.upper() not in {"POSCAR", "CONTCAR"}:
        return ""
    try:
        if path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _snippet(text: str, query: str, *, line_limit: int = 3) -> list[dict[str, Any]]:
    lowered_query = query.lower()
    snippets: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if lowered_query in line.lower():
            preview = line.strip()
            if len(preview) > 240:
                preview = preview[:239] + "…"
            snippets.append({"line": line_number, "text": preview})
            if len(snippets) >= line_limit:
                break
    return snippets


def _workspace_search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = str(arguments.get("query") or context.request_message or "").strip()
    if not query:
        raise ValueError("workspace.search 需要非空 query。")
    root = _resolve_search_root(str(arguments.get("path") or ""), context)
    top_k = max(1, min(50, int(arguments.get("top_k", 20))))
    include_content = bool(arguments.get("include_content", True))
    lowered_query = query.lower()
    matches: list[dict[str, Any]] = []
    scanned_files = 0
    for path in _iter_candidate_files(root):
        scanned_files += 1
        relative = str(path.relative_to(context.project_root)) if context.project_root and _is_within(path, context.project_root) else str(path)
        score = 0
        reasons: list[str] = []
        snippets: list[dict[str, Any]] = []
        if lowered_query in path.name.lower() or lowered_query in relative.lower():
            score += 3
            reasons.append("path_match")
        if include_content:
            text = _safe_text(path)
            if text:
                snippets = _snippet(text, query)
                if snippets:
                    score += 2 + len(snippets)
                    reasons.append("content_match")
        if score <= 0:
            continue
        matches.append(
            {
                "path": str(path),
                "relative_path": relative,
                "name": path.name,
                "score": score,
                "reasons": reasons,
                "snippets": snippets,
            }
        )
    matches.sort(key=lambda item: (-int(item["score"]), item["relative_path"]))
    selected = matches[:top_k]
    return ToolResult(
        tool_name="workspace.search",
        success=True,
        summary=f"已在工作区搜索 {query!r}，返回 {len(selected)} / {len(matches)} 个匹配。",
        output={
            "query": query,
            "root": str(root),
            "scanned_files": scanned_files,
            "match_count": len(matches),
            "matches": selected,
        },
    )


def register_workspace_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="workspace.search",
            description="Search file names and text snippets inside the project workspace or backend outputs without leaving allowed roots.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "description": "Optional workspace-relative subdirectory or file."},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    "include_content": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
            risk=ToolRisk.WORKSPACE_READ,
            read_only=True,
        ),
        _workspace_search,
    )
