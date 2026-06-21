from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import ssl
import time
from threading import Lock
from urllib import error, request as urllib_request

from app.config import settings


_ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9_+\-./]{2,}", flags=re.IGNORECASE)
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")
_REMOTE_BACKEND_FAILURES: dict[str, bool] = {}
_REMOTE_BACKEND_LOCK = Lock()


def _feature_hash(feature: str, *, dimensions: int) -> int:
    digest = hashlib.sha1(feature.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % dimensions


def _char_ngrams(text: str) -> list[str]:
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())
    grams: list[str] = []
    for size in (2, 3, 4):
        if len(compact) < size:
            continue
        grams.extend(compact[index : index + size] for index in range(len(compact) - size + 1))
    return grams


def _token_features(text: str) -> list[str]:
    lowered = text.lower()
    ascii_tokens = [token.lower() for token in _ASCII_TOKEN_PATTERN.findall(lowered)]
    cjk_chunks = _CJK_PATTERN.findall(text)
    cjk_ngrams: list[str] = []
    for chunk in cjk_chunks:
        cjk_ngrams.append(chunk)
        for size in (2, 3):
            if len(chunk) < size:
                continue
            cjk_ngrams.extend(chunk[index : index + size] for index in range(len(chunk) - size + 1))
    return ascii_tokens + cjk_ngrams + _char_ngrams(text)


def local_hash_embedding(text: str, *, dimensions: int | None = None) -> tuple[float, ...]:
    dims = max(dimensions or settings.materials_rag_embedding_dimensions, 32)
    features = _token_features(text)
    if not features:
        return tuple(0.0 for _ in range(dims))

    vector = [0.0] * dims
    for feature in features:
        slot = _feature_hash(feature, dimensions=dims)
        sign = -1.0 if _feature_hash(f"{feature}:sign", dimensions=2) == 0 else 1.0
        vector[slot] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return tuple(0.0 for _ in range(dims))
    return tuple(value / norm for value in vector)


def _normalize_backend_name(preferred_backend: str | None = None) -> str:
    backend = (preferred_backend or settings.materials_rag_embedding_backend).strip().lower()
    if backend in {"", "planned", "disabled", "none"}:
        return "disabled"
    if backend in {"llm_api", "openai_compatible", "local_hash"}:
        return backend
    return "local_hash"


def configured_embedding_backend() -> str:
    return _normalize_backend_name()


def _embedding_base_url() -> str:
    base = (settings.materials_rag_embedding_api_base_url or settings.llm_api_base_url).rstrip("/")
    if "coding.dashscope.aliyuncs.com/v1" in base:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return base


def _embedding_api_key() -> str:
    if settings.materials_rag_embedding_api_base_url:
        return settings.materials_rag_embedding_api_key
    return settings.materials_rag_embedding_api_key or settings.llm_api_key


def _base_url_supports_embeddings() -> bool:
    base = _embedding_base_url().lower()
    if "api.deepseek.com" in base:
        return False
    return True


def embedding_signature(preferred_backend: str | None = None) -> str:
    backend = _normalize_backend_name(preferred_backend)
    dimensions = max(settings.materials_rag_embedding_dimensions, 32)
    if backend in {"disabled", "local_hash"}:
        return f"{backend}:{dimensions}"
    return f"{backend}:{settings.materials_rag_embedding_model}:{dimensions}:{_embedding_base_url()}"


def effective_embedding_backend(preferred_backend: str | None = None) -> str:
    configured = _normalize_backend_name(preferred_backend)
    if configured == "disabled":
        return "disabled"
    if configured == "local_hash":
        return "local_hash"
    if not (settings.llm_enabled and _embedding_base_url() and _embedding_api_key()):
        return "local_hash"
    if not _base_url_supports_embeddings():
        return "local_hash"
    signature = embedding_signature(configured)
    with _REMOTE_BACKEND_LOCK:
        if _REMOTE_BACKEND_FAILURES.get(signature):
            return "local_hash"
    return configured


def _normalize_remote_vector(raw: object) -> tuple[float, ...]:
    if not isinstance(raw, list):
        raise RuntimeError("Embedding API returned a non-list embedding payload.")
    try:
        values = [float(item) for item in raw]
    except (TypeError, ValueError) as exc:  # noqa: PERF203
        raise RuntimeError("Embedding API returned non-numeric values.") from exc
    if not values:
        return tuple()

    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        return tuple(0.0 for _ in values)
    return tuple(value / norm for value in values)


def _mark_remote_backend_failed(backend: str) -> None:
    signature = embedding_signature(backend)
    with _REMOTE_BACKEND_LOCK:
        _REMOTE_BACKEND_FAILURES[signature] = True


def _clear_remote_backend_failure(backend: str) -> None:
    signature = embedding_signature(backend)
    with _REMOTE_BACKEND_LOCK:
        _REMOTE_BACKEND_FAILURES.pop(signature, None)


def _fetch_remote_embeddings(texts: list[str]) -> tuple[tuple[float, ...], ...]:
    endpoint = f"{_embedding_base_url()}/embeddings"
    payload = {
        "model": settings.materials_rag_embedding_model,
        "input": texts,
        "encoding_format": "float",
    }
    if settings.materials_rag_embedding_dimensions > 0:
        payload["dimensions"] = settings.materials_rag_embedding_dimensions
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_embedding_api_key()}",
        },
        method="POST",
    )
    timeout = max(3, min(settings.llm_request_timeout_seconds, 20))
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Embedding HTTP error {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionResetError) as exc:
        raise RuntimeError(f"Embedding request failed: {exc}") from exc

    parsed = json.loads(body)
    raw_data = parsed.get("data")
    if not isinstance(raw_data, list):
        raise RuntimeError("Embedding API response missing data list.")

    vectors: list[tuple[float, ...]] = []
    for item in raw_data:
        if not isinstance(item, dict):
            raise RuntimeError("Embedding API returned malformed item.")
        vectors.append(_normalize_remote_vector(item.get("embedding")))
    if len(vectors) != len(texts):
        raise RuntimeError("Embedding API returned an unexpected number of vectors.")
    return tuple(vectors)


def build_embeddings(
    texts: list[str],
    *,
    backend: str | None = None,
) -> tuple[tuple[tuple[float, ...], ...], str]:
    resolved_backend = effective_embedding_backend(backend)
    if resolved_backend == "disabled":
        return tuple(tuple() for _ in texts), resolved_backend
    if resolved_backend == "local_hash":
        return tuple(local_hash_embedding(text) for text in texts), resolved_backend

    batch_size = max(1, settings.materials_rag_embedding_api_batch_size)
    vectors: list[tuple[float, ...]] = []
    max_attempts = max(1, settings.llm_request_max_retries + 1)
    try:
        for index in range(0, len(texts), batch_size):
            chunk = texts[index : index + batch_size]
            last_error: RuntimeError | None = None
            for attempt in range(max_attempts):
                try:
                    vectors.extend(_fetch_remote_embeddings(chunk))
                    last_error = None
                    break
                except RuntimeError as exc:
                    last_error = exc
                    if attempt < max_attempts - 1:
                        time.sleep(max(0.0, settings.llm_retry_backoff_seconds) * (attempt + 1))
            if last_error is not None:
                raise last_error
    except RuntimeError:
        _mark_remote_backend_failed(resolved_backend)
        return tuple(local_hash_embedding(text) for text in texts), "local_hash"

    _clear_remote_backend_failure(resolved_backend)
    return tuple(vectors), resolved_backend


def build_embedding_with_backend(
    text: str,
    *,
    backend: str | None = None,
) -> tuple[tuple[float, ...], str]:
    vectors, resolved_backend = build_embeddings([text], backend=backend)
    return (vectors[0] if vectors else tuple()), resolved_backend


def build_embedding(text: str) -> tuple[float, ...]:
    vector, _ = build_embedding_with_backend(text)
    return vector


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))
