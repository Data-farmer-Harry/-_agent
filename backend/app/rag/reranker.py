from __future__ import annotations

import json
import socket
import ssl
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib import error, request as urllib_request

from app.config import settings


@dataclass(frozen=True)
class RerankItem:
    index: int
    relevance_score: float | None = None


@dataclass(frozen=True)
class RerankResult:
    items: tuple[RerankItem, ...]
    backend: str
    model: str
    used_remote: bool
    error: str = ""


def _fallback_items(document_count: int) -> tuple[RerankItem, ...]:
    return tuple(RerankItem(index=index) for index in range(document_count))


def _api_base_url() -> str:
    return (
        settings.rag_reranker_api_base_url
        or settings.materials_rag_embedding_api_base_url
        or settings.thermo_rag_embedding_api_base_url
        or settings.llm_api_base_url
    ).rstrip("/")


def _api_key() -> str:
    return (
        settings.rag_reranker_api_key
        or settings.materials_rag_embedding_api_key
        or settings.thermo_rag_embedding_api_key
        or settings.llm_api_key
    )


def reranker_available() -> bool:
    return bool(
        settings.rag_reranker_enabled
        and settings.rag_reranker_model.strip()
        and _api_base_url()
        and _api_key()
    )


def reranker_signature() -> str:
    if not settings.rag_reranker_enabled:
        return "disabled"
    return f"openai_compatible:{settings.rag_reranker_model}:{_api_base_url()}"


def _normalize_documents(documents: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for document in documents:
        compact = " ".join((document or "").split())
        normalized.append(compact[:4000])
    return tuple(normalized)


@lru_cache(maxsize=256)
def _rerank_remote_cached(
    signature: str,
    query: str,
    documents: tuple[str, ...],
) -> tuple[RerankItem, ...]:
    _ = signature
    endpoint = f"{_api_base_url()}/rerank"
    payload = {
        "model": settings.rag_reranker_model,
        "query": query,
        "documents": list(documents),
        "top_n": len(documents),
    }
    req = urllib_request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout = max(3, min(settings.rag_reranker_timeout_seconds, 180))
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Reranker HTTP error {exc.code}: {detail[:600]}") from exc
    except (error.URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionResetError, OSError) as exc:
        raise RuntimeError(f"Reranker request failed: {exc}") from exc

    raw_results = parsed.get("results")
    if not isinstance(raw_results, list):
        raise RuntimeError("Reranker response missing results list.")

    items: list[RerankItem] = []
    seen: set[int] = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
            relevance_score = float(raw.get("relevance_score"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(documents) or index in seen:
            continue
        seen.add(index)
        items.append(RerankItem(index=index, relevance_score=relevance_score))
    if not items:
        raise RuntimeError("Reranker response contained no valid results.")
    items.extend(RerankItem(index=index) for index in range(len(documents)) if index not in seen)
    return tuple(items)


def rerank_texts(query: str, documents: list[str] | tuple[str, ...]) -> RerankResult:
    normalized_documents = _normalize_documents(tuple(documents))
    if len(normalized_documents) < 2:
        return RerankResult(
            items=_fallback_items(len(normalized_documents)),
            backend="not_needed",
            model=settings.rag_reranker_model,
            used_remote=False,
        )
    if not reranker_available():
        return RerankResult(
            items=_fallback_items(len(normalized_documents)),
            backend="disabled" if not settings.rag_reranker_enabled else "unconfigured",
            model=settings.rag_reranker_model,
            used_remote=False,
        )

    max_attempts = max(1, settings.llm_request_max_retries + 1)
    last_error = ""
    for attempt in range(max_attempts):
        try:
            items = _rerank_remote_cached(
                reranker_signature(),
                " ".join((query or "").split()),
                normalized_documents,
            )
            return RerankResult(
                items=items,
                backend="openrouter" if "openrouter.ai" in _api_base_url().lower() else "openai_compatible",
                model=settings.rag_reranker_model,
                used_remote=True,
            )
        except RuntimeError as exc:
            last_error = str(exc)
            if attempt < max_attempts - 1:
                time.sleep(max(0.0, settings.llm_retry_backoff_seconds) * (attempt + 1))

    return RerankResult(
        items=_fallback_items(len(normalized_documents)),
        backend="fallback",
        model=settings.rag_reranker_model,
        used_remote=False,
        error=last_error,
    )


def clear_reranker_cache() -> None:
    _rerank_remote_cached.cache_clear()
