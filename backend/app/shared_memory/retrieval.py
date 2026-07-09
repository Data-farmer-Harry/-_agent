from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable

from app.core.bm25 import build_bm25_index, score_bm25
from app.shared_memory.canonicalization import canonicalize_free_text, canonicalize_key_text
from app.shared_memory.models import MemoryItem, MemoryRetrievalCandidate, MemoryRetrievalResult, MemoryScope


_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_TOKEN_RE = re.compile(r"[a-z0-9_+\-.]+|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")
_NUMERIC_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
_LAMMPS_COMMANDS = {
    "angle_coeff",
    "angle_style",
    "atom_style",
    "bond_coeff",
    "bond_style",
    "boundary",
    "compute",
    "create_atoms",
    "create_box",
    "delete_atoms",
    "dihedral_coeff",
    "dihedral_style",
    "dump",
    "fix",
    "group",
    "improper_coeff",
    "improper_style",
    "include",
    "lattice",
    "mass",
    "minimize",
    "neigh_modify",
    "neighbor",
    "pair_coeff",
    "pair_style",
    "read_data",
    "region",
    "reset_timestep",
    "run",
    "set",
    "thermo",
    "thermo_style",
    "timestep",
    "units",
    "unfix",
    "undump",
    "variable",
    "velocity",
    "write_data",
    "write_restart",
}
_NONCOMPRESSIBLE_SOURCE_MARKERS = (
    ".in",
    ".log",
    ".json",
    "input_script",
    "input.in",
    "lammps_input",
    "json_patch",
    "quality_report",
    "stderr",
    "stdout",
    "traceback",
)
_DOMAIN_EXPANSIONS = {
    "lammps": ("molecular dynamics", "md", "pair_style", "thermo", "dump", "fix", "units"),
    "md": ("molecular dynamics", "lammps"),
    "rdf": ("radial distribution function", "compute rdf", "structure analysis"),
    "msd": ("mean squared displacement", "compute msd", "diffusion"),
    "温度": ("temperature", "temp", "target_temperature"),
    "压力": ("pressure", "press"),
    "势函数": ("potential", "pair_style", "pair_coeff"),
    "扩散": ("diffusion", "msd", "mean squared displacement"),
    "相图": ("phase diagram", "calphad", "pycalphad", "thermo"),
    "ovito": ("trajectory", "visualization", "dump"),
}


@dataclass(frozen=True)
class MemoryQueryRewrite:
    original_query: str
    search_query: str
    expansion_terms: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.search_query != self.original_query or bool(self.expansion_terms)


def rewrite_memory_query(query: str) -> MemoryQueryRewrite:
    original = " ".join((query or "").split())
    canonical = canonicalize_free_text(original)
    terms: list[str] = []
    for token in tokenize_memory_text(f"{original} {canonical}"):
        terms.extend(_DOMAIN_EXPANSIONS.get(token, ()))
    if canonical and canonical != original.lower():
        terms.append(canonical)
    expansion_terms = _dedupe(terms)[:32]
    if expansion_terms:
        search_query = f"{original}\nshared memory rewrite terms: {'; '.join(expansion_terms)}"
    else:
        search_query = original
    return MemoryQueryRewrite(original_query=original, search_query=search_query, expansion_terms=expansion_terms)


def retrieve_from_items(
    *,
    query: str,
    scope: MemoryScope,
    items: list[MemoryItem],
    top_k: int = 6,
    prompt_budget_bytes: int = 12_288,
    vector_cache: Callable[..., dict[str, Any]] | None = None,
    dense_scores: dict[str, float] | None = None,
    dense_backend: str = "deterministic_dense_fallback",
) -> MemoryRetrievalResult:
    rewrite = rewrite_memory_query(query)
    query_tokens = bm25_tokens_for_query(rewrite.search_query)
    query_embedding = cached_or_deterministic_vector(
        tokens=("query", *query_tokens),
        vector_cache=vector_cache,
        embedding_id=embedding_id_for_tokens(("query", *query_tokens)),
        metadata={"kind": "query"},
    )
    query_vector = query_embedding["vector"]
    indexed_tokens = [bm25_tokens_for_item(item) for item in items]
    bm25_index = build_bm25_index(indexed_tokens)
    ranked: list[MemoryRetrievalCandidate] = []
    for item, item_tokens, document_stats in zip(items, indexed_tokens, bm25_index.documents):
        forced = is_forced_retention(item)
        bm25_score = score_bm25(query_tokens, document_stats, bm25_index)
        item_embedding_tokens = embedding_tokens_for_item(item)
        item_embedding = cached_or_deterministic_vector(
            tokens=item_embedding_tokens,
            vector_cache=vector_cache,
            embedding_id=item.embedding_id or embedding_id_for_tokens(item_embedding_tokens),
            metadata={"kind": "memory_item", "memory_id": item.memory_id},
        )
        sqlite_vec_score = dense_scores.get(item.memory_id) if dense_scores is not None else None
        dense_score = (
            max(0.0, min(1.0, float(sqlite_vec_score)))
            if sqlite_vec_score is not None
            else cosine_similarity(query_vector, item_embedding["vector"])
        )
        lexical_score, lexical_reasons = lexical_score_item(rewrite.search_query, item)
        score = bm25_score + lexical_score + (1.25 * dense_score) + authority_boost(item)
        reasons = []
        if forced:
            score += 10_000.0
            reasons.append("forced_locked_fact")
        if bm25_score > 0:
            reasons.append(f"bm25:{bm25_score:.3f}")
        if dense_score >= 0.05:
            if sqlite_vec_score is not None:
                reasons.append(f"r1_sqlite_vec:{dense_score:.3f}")
            else:
                reasons.append(f"r1_dense_fallback:{dense_score:.3f}")
        if vector_cache is not None:
            cache_state = "hit" if item_embedding["cache_hit"] else "miss"
            reasons.append(f"embedding_cache:item_{cache_state}")
        reasons.extend(lexical_reasons)
        if rewrite.changed and reasons:
            reasons.append("query_rewrite")
        if score <= 0 and not forced:
            continue
        ranked.append(MemoryRetrievalCandidate(item=item, score=round(score, 6), reasons=_dedupe_list(reasons)))
    ranked.sort(key=lambda candidate: (-candidate.score, -_safe_timestamp(candidate.item.updated_at), candidate.item.memory_id))
    reranked = rerank_with_mmr(ranked, rewrite.search_query)
    estimated_before_compression = sum(candidate_payload_size(candidate) for candidate in reranked)
    compressed = [compress_candidate_for_prompt(candidate, rewrite.search_query) for candidate in reranked]
    return pack_prompt_budget(
        query=query,
        rewritten_query=rewrite.search_query,
        expansion_terms=list(rewrite.expansion_terms),
        scope_filter=scope.visible_scope_keys(),
        ranked=compressed,
        top_k=top_k,
        prompt_budget_bytes=prompt_budget_bytes,
        estimated_before_bytes=estimated_before_compression,
        retrieval_backend=(
            "metadata_bm25_sqlite_vec_dense_cache_r2_mmr_textrank_r3"
            if dense_backend == "sqlite_vec" and dense_scores is not None
            else
            "metadata_bm25_persistent_dense_cache_r2_mmr_textrank_r3"
            if vector_cache is not None
            else "metadata_bm25_dense_fallback_r2_mmr_textrank_r3"
        ),
    )


def pack_prompt_budget(
    *,
    query: str,
    rewritten_query: str,
    expansion_terms: list[str],
    scope_filter: list[tuple[str, str]],
    ranked: list[MemoryRetrievalCandidate],
    top_k: int,
    prompt_budget_bytes: int,
    estimated_before_bytes: int | None = None,
    retrieval_backend: str = "bm25_query_rewrite_r2_mmr_textrank_fallback",
) -> MemoryRetrievalResult:
    selected: list[MemoryRetrievalCandidate] = []
    dropped: list[str] = []
    dropped_reasons: dict[str, str] = {}
    forced_ids: list[str] = []
    used_bytes = 0
    non_forced_selected = 0
    estimated_before = (
        estimated_before_bytes
        if estimated_before_bytes is not None
        else sum(candidate_payload_size(candidate) for candidate in ranked)
    )
    for candidate in ranked:
        item_id = candidate.item.memory_id
        forced = "forced_locked_fact" in candidate.reasons
        payload_size = candidate_payload_size(candidate)
        if forced:
            selected.append(candidate)
            forced_ids.append(item_id)
            used_bytes += payload_size
            continue
        if non_forced_selected >= max(1, top_k):
            dropped.append(item_id)
            dropped_reasons[item_id] = "top_k"
            continue
        if used_bytes + payload_size > prompt_budget_bytes:
            dropped.append(item_id)
            dropped_reasons[item_id] = "prompt_budget"
            continue
        selected.append(candidate)
        used_bytes += payload_size
        non_forced_selected += 1
    return MemoryRetrievalResult(
        query=query,
        rewritten_query=rewritten_query,
        expansion_terms=expansion_terms,
        scope_filter=scope_filter,
        candidates=selected,
        selected_item_ids=[candidate.item.memory_id for candidate in selected],
        dropped_item_ids=dropped,
        dropped_reasons=dropped_reasons,
        forced_retention_ids=forced_ids,
        prompt_budget_bytes=prompt_budget_bytes,
        estimated_before_bytes=estimated_before,
        estimated_after_bytes=used_bytes,
        retrieval_backend=retrieval_backend,
    )


def is_forced_retention(item: MemoryItem) -> bool:
    metadata = item.metadata or {}
    if metadata.get("locked") is True or metadata.get("locked_fact") is True:
        return True
    if item.authority == "user" and item.item_type in {"constraint", "preference"}:
        return True
    return False


def rerank_with_mmr(
    ranked: list[MemoryRetrievalCandidate],
    query: str,
    *,
    lambda_relevance: float = 0.68,
) -> list[MemoryRetrievalCandidate]:
    """Second-stage deterministic reranker using MMR-style diversity.

    This is intentionally dependency-free. It does not replace the future dense
    reranker; it gives the current agent a safer R2 behavior by preventing a
    small prompt budget from being filled with near-duplicate memories.
    """

    if len(ranked) <= 2:
        return [_with_reason(candidate, "r2_mmr:skipped_small_pool") for candidate in ranked]
    forced = [_with_reason(candidate, "r2_mmr:forced_locked_fact") for candidate in ranked if "forced_locked_fact" in candidate.reasons]
    pool = [candidate for candidate in ranked if "forced_locked_fact" not in candidate.reasons]
    if len(pool) <= 1:
        return [*forced, *[_with_reason(candidate, "r2_mmr:skipped_small_pool") for candidate in pool]]

    max_relevance = max((max(candidate.score, 0.0) for candidate in pool), default=0.0) or 1.0
    selected: list[MemoryRetrievalCandidate] = []
    selected_tokens: list[set[str]] = []
    remaining = list(pool)
    while remaining:
        best_index = 0
        best_tuple: tuple[float, float, float, str] | None = None
        for index, candidate in enumerate(remaining):
            tokens = candidate_token_set(candidate)
            relevance = max(candidate.score, 0.0) / max_relevance
            diversity_penalty = max((_jaccard(tokens, other) for other in selected_tokens), default=0.0)
            mmr = lambda_relevance * relevance - (1.0 - lambda_relevance) * diversity_penalty
            rank_tuple = (mmr, relevance, _safe_timestamp(candidate.item.updated_at), candidate.item.memory_id)
            if best_tuple is None or rank_tuple > best_tuple:
                best_tuple = rank_tuple
                best_index = index
        chosen = remaining.pop(best_index)
        chosen_tokens = candidate_token_set(chosen)
        selected_tokens.append(chosen_tokens)
        mmr_score = best_tuple[0] if best_tuple is not None else 0.0
        selected.append(_with_reason(chosen, f"r2_mmr:{mmr_score:.3f}"))
    return [*forced, *selected]


def candidate_token_set(candidate: MemoryRetrievalCandidate) -> set[str]:
    item = candidate.item
    parts = [
        item.item_type,
        item.authority,
        item.subject,
        item.predicate,
        str(item.value),
        item.unit,
        item.text,
        item.normalized_text,
        " ".join(item.source_refs),
    ]
    return set(tokenize_memory_text(" ".join(part for part in parts if part)))


def compress_candidate_for_prompt(
    candidate: MemoryRetrievalCandidate,
    query: str,
    *,
    threshold_chars: int = 720,
    max_chars: int = 900,
) -> MemoryRetrievalCandidate:
    item = candidate.item
    text = str(item.text or "")
    protected_reason = compression_protection_reason(item)
    if protected_reason:
        protected_item = item.model_copy(
            update={
                "metadata": {
                    **(item.metadata or {}),
                    "context_compression": _compression_metadata(
                        item,
                        method="preserve_original",
                        protected=True,
                        reason=protected_reason,
                        compressed_text=text,
                    ),
                }
            }
        )
        return candidate.model_copy(
            update={
                "item": protected_item,
                "reasons": _dedupe_list([*candidate.reasons, f"r2_protected:{protected_reason}"]),
            }
        )
    if len(text.strip()) < threshold_chars:
        return candidate

    summary = textrank_summarize_text(text, query=query, max_chars=max_chars)
    if not summary or len(summary) >= max(80, int(len(text) * 0.9)):
        return candidate
    compressed_text = f"L2 summary: {summary}"
    compressed_item = item.model_copy(
        update={
            "text": compressed_text,
            "normalized_text": canonicalize_free_text(summary),
            "metadata": {
                **(item.metadata or {}),
                "context_compression": _compression_metadata(
                    item,
                    method="textrank_v1",
                    protected=False,
                    reason="ordinary_long_text",
                    compressed_text=compressed_text,
                ),
            },
        }
    )
    return candidate.model_copy(
        update={
            "item": compressed_item,
            "reasons": _dedupe_list([*candidate.reasons, "r2_textrank_compressed"]),
        }
    )


def compression_protection_reason(item: MemoryItem) -> str:
    text = str(item.text or "").strip()
    if not text:
        return "empty_text"
    if is_forced_retention(item):
        return "locked_or_user_constraint"
    metadata = item.metadata or {}
    if metadata.get("compression") in {"none", "preserve", "raw"} or metadata.get("non_compressible") is True:
        return "metadata_non_compressible"
    source_blob = " ".join([*item.source_refs, *[f"{key}:{value}" for key, value in metadata.items()]]).casefold()
    if any(marker in source_blob for marker in _NONCOMPRESSIBLE_SOURCE_MARKERS):
        return "source_non_compressible"
    stripped = text.lstrip()
    if stripped.startswith(("```", "{", "[")) and _looks_like_json_or_code(stripped):
        return "json_or_code"
    if "ERROR:" in text or "Traceback " in text:
        return "log_or_error"
    if _looks_like_lammps_script(text):
        return "lammps_script"
    if _looks_like_numeric_table(text):
        return "numeric_table"
    return ""


def textrank_summarize_text(
    text: str,
    *,
    query: str,
    max_chars: int = 900,
    max_sentences: int = 5,
) -> str:
    sentences = split_sentences(text)
    if len(sentences) < 3:
        return _truncate_text(" ".join(sentences) or text, max_chars)
    token_sets = [set(tokenize_memory_text(sentence)) for sentence in sentences]
    similarities = _sentence_similarity_matrix(token_sets)
    scores = [1.0 for _ in sentences]
    damping = 0.85
    for _ in range(18):
        next_scores = [1.0 - damping for _ in sentences]
        for source_index, row in enumerate(similarities):
            outgoing = sum(row)
            if outgoing <= 0:
                continue
            for target_index, weight in enumerate(row):
                if target_index == source_index or weight <= 0:
                    continue
                next_scores[target_index] += damping * scores[source_index] * (weight / outgoing)
        scores = next_scores
    query_tokens = set(tokenize_memory_text(query))
    ranked_indices = sorted(
        range(len(sentences)),
        key=lambda index: (
            scores[index] + 0.25 * _jaccard(token_sets[index], query_tokens),
            -index,
        ),
        reverse=True,
    )
    selected_indices: list[int] = []
    used_chars = 0
    for index in ranked_indices:
        sentence = sentences[index]
        projected = used_chars + len(sentence) + (1 if selected_indices else 0)
        if selected_indices and projected > max_chars:
            continue
        selected_indices.append(index)
        used_chars = projected
        if len(selected_indices) >= max_sentences:
            break
    if not selected_indices:
        return _truncate_text(sentences[0], max_chars)
    selected_indices.sort()
    return _truncate_text(" ".join(sentences[index] for index in selected_indices), max_chars)


def tokenize_memory_text(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer((text or "").lower()):
        token = match.group(0).strip().lower()
        if not token:
            continue
        tokens.append(token)
        if _CJK_RE.fullmatch(token) and len(token) > 2:
            tokens.extend(token[index : index + 2] for index in range(0, len(token) - 1))
    return _dedupe(tokens)


def bm25_tokens_for_query(query: str) -> tuple[str, ...]:
    tokens = list(tokenize_memory_text(query))
    tokens.extend(tokenize_memory_text(canonicalize_free_text(query)))
    return _dedupe(tokens)


def bm25_tokens_for_item(item: MemoryItem) -> tuple[str, ...]:
    parts = [
        item.item_type,
        item.authority,
        item.subject,
        item.predicate,
        str(item.value),
        item.unit,
        item.text,
        item.normalized_text,
        " ".join(item.source_refs),
        " ".join(f"{key}:{value}" for key, value in sorted((item.metadata or {}).items())),
    ]
    tokens: list[str] = []
    raw_text = "\n".join(part for part in parts if part)
    tokens.extend(tokenize_memory_text(raw_text))
    tokens.extend(tokenize_memory_text(canonicalize_free_text(raw_text)))
    tokens.append(f"type:{item.item_type}")
    tokens.append(f"authority:{item.authority}")
    tokens.append(f"subject:{canonicalize_key_text(item.subject)}")
    tokens.append(f"predicate:{canonicalize_key_text(item.predicate)}")
    return _dedupe(tokens)


def embedding_tokens_for_item(item: MemoryItem) -> tuple[str, ...]:
    """Stable, compact token stream used as the persisted dense-cache key."""

    parts = [
        item.item_type,
        item.authority,
        item.subject,
        item.predicate,
        str(item.value),
        item.unit,
        item.normalized_text,
        item.text,
    ]
    return _dedupe(tokenize_memory_text("\n".join(part for part in parts if part)))


def embedding_id_for_tokens(tokens: tuple[str, ...]) -> str:
    joined = "\n".join(tokens)
    return f"emb-{sha256(joined.encode('utf-8')).hexdigest()[:16]}"


def cached_or_deterministic_vector(
    *,
    tokens: tuple[str, ...],
    vector_cache: Callable[..., dict[str, Any]] | None,
    embedding_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if vector_cache is None:
        return {
            "embedding_id": embedding_id,
            "backend": "deterministic_hash_v1",
            "vector": deterministic_text_vector(tokens),
            "cache_hit": False,
        }
    return vector_cache(
        embedding_id=embedding_id,
        tokens=tokens,
        vector_factory=deterministic_text_vector,
        backend="deterministic_hash_v1",
        dimensions=96,
        metadata=metadata or {},
    )


def deterministic_text_vector(tokens: tuple[str, ...], *, dimensions: int = 96) -> tuple[float, ...]:
    """Dependency-free dense fallback used when real embeddings/sqlite-vec are unavailable.

    The vector is intentionally deterministic and token based. It is not a
    semantic substitute for production embeddings, but it gives R1 a stable
    dense-like score and lets the store reuse a persistent embedding_id without
    downloading any model or extension.
    """

    vector = [0.0] * dimensions
    for token in tokens:
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    value = sum(a * b for a, b in zip(left, right))
    return max(0.0, min(1.0, value))


def lexical_score_item(query: str, item: MemoryItem) -> tuple[float, list[str]]:
    normalized_query = canonicalize_free_text(query)
    subject = canonicalize_key_text(item.subject)
    predicate = canonicalize_key_text(item.predicate)
    item_text = canonicalize_free_text(
        " ".join(
            str(part)
            for part in [item.subject, item.predicate, item.value, item.unit, item.text, item.normalized_text]
            if part not in (None, "")
        )
    )
    score = 0.0
    reasons: list[str] = []
    if subject and subject in normalized_query:
        score += 2.0
        reasons.append("subject")
    if predicate and predicate in normalized_query:
        score += 1.5
        reasons.append("predicate")
    query_tokens = set(tokenize_memory_text(normalized_query))
    item_tokens = set(tokenize_memory_text(item_text))
    overlap = query_tokens.intersection(item_tokens)
    if overlap:
        score += min(2.5, 0.35 * len(overlap))
        reasons.append(f"token_overlap:{len(overlap)}")
    if item.item_type in query_tokens:
        score += 0.4
        reasons.append("item_type")
    return score, reasons


def authority_boost(item: MemoryItem) -> float:
    if item.authority == "user":
        return 0.35
    if item.authority in {"execution", "registry", "validated_document"}:
        return 0.2
    if item.authority == "rag":
        return 0.1
    return 0.0


def candidate_payload_size(candidate: MemoryRetrievalCandidate) -> int:
    return len(candidate.model_dump_json().encode("utf-8"))


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in re.split(r"\n+", text or ""):
        normalized = " ".join(paragraph.split())
        if not normalized:
            continue
        for match in _SENTENCE_RE.finditer(normalized):
            sentence = " ".join(match.group(0).split())
            if len(sentence) >= 18:
                sentences.append(sentence)
    if sentences:
        return sentences
    fallback = " ".join((text or "").split())
    return [fallback] if fallback else []


def _sentence_similarity_matrix(token_sets: list[set[str]]) -> list[list[float]]:
    matrix: list[list[float]] = []
    for left_index, left_tokens in enumerate(token_sets):
        row: list[float] = []
        for right_index, right_tokens in enumerate(token_sets):
            if left_index == right_index:
                row.append(0.0)
            else:
                row.append(_jaccard(left_tokens, right_tokens))
        matrix.append(row)
    return matrix


def _compression_metadata(
    item: MemoryItem,
    *,
    method: str,
    protected: bool,
    reason: str,
    compressed_text: str,
) -> dict[str, object]:
    metadata = item.metadata or {}
    raw_evidence_ids = metadata.get("raw_evidence_ids") if isinstance(metadata.get("raw_evidence_ids"), list) else []
    return {
        "level": "L2",
        "method": method,
        "protected": protected,
        "reason": reason,
        "original_chars": len(item.text or ""),
        "compressed_chars": len(compressed_text or ""),
        "l3": {
            "memory_id": item.memory_id,
            "source_refs": item.source_refs[:8],
            "content_hash": item.content_hash,
            "normalized_hash": item.normalized_hash,
            "raw_evidence_ids": [str(value) for value in raw_evidence_ids[:8]],
        },
    }


def _looks_like_json_or_code(text: str) -> bool:
    if text.startswith("```"):
        return True
    try:
        json.loads(text)
        return True
    except Exception:  # noqa: BLE001 - this is a lightweight protection heuristic.
        return False


def _looks_like_lammps_script(text: str) -> bool:
    command_hits = 0
    meaningful_lines = 0
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        meaningful_lines += 1
        command = line.split(maxsplit=1)[0].lower()
        if command in _LAMMPS_COMMANDS:
            command_hits += 1
    return command_hits >= 3 and command_hits >= math.ceil(max(meaningful_lines, 1) * 0.25)


def _looks_like_numeric_table(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    numeric_rows = 0
    for line in lines:
        if len(_NUMERIC_RE.findall(line)) >= 2:
            numeric_rows += 1
    return numeric_rows >= 3 and numeric_rows >= math.ceil(len(lines) * 0.5)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left.intersection(right))
    if intersection <= 0:
        return 0.0
    return intersection / len(left.union(right))


def _with_reason(candidate: MemoryRetrievalCandidate, reason: str) -> MemoryRetrievalCandidate:
    return candidate.model_copy(update={"reasons": _dedupe_list([*candidate.reasons, reason])})


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)].rstrip() + "…"


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(str(item or "").split())
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        output.append(normalized)
        seen.add(key)
    return tuple(output)


def _dedupe_list(items: Iterable[str]) -> list[str]:
    return list(_dedupe(items))


def _safe_timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
