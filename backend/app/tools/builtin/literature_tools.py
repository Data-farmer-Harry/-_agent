from __future__ import annotations

from typing import Any

import requests

from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


def _join_people(people: list[dict[str, Any]], *, limit: int = 4) -> str:
    names: list[str] = []
    for person in people[:limit]:
        given = str(person.get("given") or "").strip()
        family = str(person.get("family") or "").strip()
        name = " ".join(part for part in [given, family] if part)
        if name:
            names.append(name)
    if len(people) > limit:
        names.append("et al.")
    return ", ".join(names)


def _year_from_item(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "created", "issued"):
        parts = item.get(key, {}).get("date-parts") if isinstance(item.get(key), dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _literature_search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = str(arguments.get("query") or context.request_message or "").strip()
    if not query:
        raise ValueError("literature.search 需要非空 query。")
    top_k = max(1, min(10, int(arguments.get("top_k", 5))))
    timeout = max(3.0, min(20.0, float(arguments.get("timeout_seconds", 8.0))))
    params = {
        "query": query,
        "rows": top_k,
        "select": "DOI,title,author,issued,published,published-print,published-online,created,container-title,URL,type,is-referenced-by-count",
    }
    response = requests.get("https://api.crossref.org/works", params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    items = payload.get("message", {}).get("items", [])
    results: list[dict[str, Any]] = []
    for item in items:
        titles = item.get("title") if isinstance(item.get("title"), list) else []
        containers = item.get("container-title") if isinstance(item.get("container-title"), list) else []
        doi = str(item.get("DOI") or "").strip()
        url = str(item.get("URL") or "").strip()
        results.append(
            {
                "title": titles[0] if titles else "",
                "authors": _join_people(item.get("author", []) if isinstance(item.get("author"), list) else []),
                "year": _year_from_item(item),
                "venue": containers[0] if containers else "",
                "doi": doi,
                "url": url or (f"https://doi.org/{doi}" if doi else ""),
                "type": item.get("type", ""),
                "referenced_by_count": item.get("is-referenced-by-count"),
            }
        )
    return ToolResult(
        tool_name="literature.search",
        success=True,
        summary=f"已通过 Crossref 检索到 {len(results)} 条文献候选。",
        output={
            "query": query,
            "provider": "crossref",
            "results": results,
            "note": "Crossref 返回的是候选元数据；正式引用前仍建议核对 DOI、期刊页码和原文内容。",
        },
    )


def register_literature_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="literature.search",
            description="Search Crossref for literature metadata when the user explicitly asks for papers, citations, DOI, or literature review leads.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "timeout_seconds": {"type": "number", "minimum": 3, "maximum": 20, "default": 8},
                },
                "required": ["query"],
            },
            risk=ToolRisk.NETWORK,
            read_only=True,
        ),
        _literature_search,
    )
