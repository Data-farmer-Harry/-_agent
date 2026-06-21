from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from app.config import settings
from app.core.bm25 import build_bm25_index, score_bm25
from app.rag.query_rewrite import rewrite_thermo_query
from app.rag.reranker import rerank_texts
from app.rag.sqlite_vector_store import get_vector_store
from app.thermo.rag_index import TOKEN_PATTERN, build_thermo_card_index
from app.thermo.rag_models import ThermoRagCandidateRecord
from app.thermo.rag_vector import build_embedding_with_backend, effective_embedding_backend
from app.utils.constants import normalize_system_key


def _tokenize_query(text: str) -> tuple[str, ...]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text or "")]
    return tuple(dict.fromkeys(token for token in tokens if token))


def _bm25_tokens_for_query(query: str) -> tuple[str, ...]:
    normalized_query = normalize_system_key(query)
    tokens: list[str] = []
    tokens.extend(_tokenize_query(query))
    if normalized_query:
        tokens.append(normalized_query)
    for token in _tokenize_query(query):
        tokens.append(f"component:{token}")
        tokens.append(f"phase:{token}")
        tokens.append(f"tag:{token}")
    return tuple(token for token in tokens if token)


def _match_terms(query: str, aliases: Iterable[str]) -> tuple[str, ...]:
    lowered_query = (query or "").lower()
    matched: list[str] = []
    for alias in aliases:
        alias_lower = alias.lower()
        if not alias_lower:
            continue
        if any(ord(character) > 127 for character in alias_lower):
            if alias_lower in lowered_query:
                matched.append(alias)
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(alias_lower)}(?![a-z0-9])"
        if re.search(pattern, lowered_query):
            matched.append(alias)
    return tuple(dict.fromkeys(matched))


def search_thermo_cards(
    query: str,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
) -> tuple[ThermoRagCandidateRecord, ...]:
    query_rewrite = rewrite_thermo_query(query)
    search_text = query_rewrite.search_query
    normalized_queries = {key for key in query_rewrite.normalized_system_keys if key}
    original_normalized_query = normalize_system_key(query)
    if original_normalized_query:
        normalized_queries.add(original_normalized_query)
    query_tokens = set(_tokenize_query(search_text))
    minimum_score = settings.thermo_rag_min_score if min_score is None else min_score
    candidate_limit = settings.thermo_rag_top_k if top_k is None else top_k
    documents = build_thermo_card_index()
    embedding_backend = documents[0].embedding_backend if documents else effective_embedding_backend()
    query_vector, query_backend = build_embedding_with_backend(search_text, backend=embedding_backend)
    if query_backend != embedding_backend:
        documents = build_thermo_card_index(query_backend)
        embedding_backend = documents[0].embedding_backend if documents else query_backend
        query_vector, query_backend = build_embedding_with_backend(search_text, backend=embedding_backend)
    candidates: list[ThermoRagCandidateRecord] = []
    bm25_index = build_bm25_index(document.bm25_tokens for document in documents)
    query_bm25_tokens = _bm25_tokens_for_query(search_text)
    vector_scores = {
        hit.document_id: hit.similarity
        for hit in get_vector_store().search("thermo_rag", query_vector, top_k=len(documents))
    }

    for document_index, document in enumerate(documents):
        score = 0.0
        lexical_score = 0.0
        bm25_score = 0.0
        vector_score = 0.0
        reasons: list[str] = []
        matched_terms: list[str] = []

        if normalized_queries.intersection(document.normalized_names):
            lexical_score += 1.0
            reasons.append("exact_system_or_alias_match")
            matched_terms.append(document.card.system_name)

        name_matches = _match_terms(search_text, document.card.all_names())
        if name_matches:
            lexical_score += 0.84
            reasons.append("name_text_match")
            matched_terms.extend(name_matches)

        component_matches = _match_terms(search_text, document.component_aliases)
        if component_matches:
            component_bonus = 0.28 * min(len(component_matches), len(document.card.components))
            lexical_score += component_bonus
            reasons.append("component_alias_match")
            matched_terms.extend(component_matches)

        phase_matches = _match_terms(search_text, document.phase_aliases)
        if phase_matches:
            lexical_score += min(0.18, 0.08 * len(phase_matches))
            reasons.append("phase_match")
            matched_terms.extend(phase_matches)

        tag_matches = _match_terms(search_text, document.tag_aliases)
        if tag_matches:
            lexical_score += min(0.12, 0.05 * len(tag_matches))
            reasons.append("tag_match")
            matched_terms.extend(tag_matches)

        if query_tokens and document.tokens:
            overlap = query_tokens.intersection(document.tokens)
            if overlap:
                overlap_score = min(0.26, 0.09 * len(overlap))
                lexical_score += overlap_score
                reasons.append("lexical_overlap")
                matched_terms.extend(sorted(overlap))

        bm25_raw_score = score_bm25(query_bm25_tokens, bm25_index.documents[document_index], bm25_index)
        bm25_score = settings.thermo_rag_bm25_weight * bm25_raw_score
        if bm25_score > 0:
            reasons.append("bm25_sparse_match")

        similarity = vector_scores.get(document.card.system_name, 0.0)
        if similarity >= settings.thermo_rag_vector_min_similarity:
            vector_score = settings.thermo_rag_vector_weight * similarity
            reasons.append("vector_similarity")

        if query_rewrite.changed and reasons:
            reasons.append("query_rewrite")

        score = lexical_score + bm25_score + vector_score

        if score < minimum_score:
            continue

        strategy = reasons[0] if reasons else "weak_match"
        candidates.append(
            ThermoRagCandidateRecord(
                card=document.card,
                score=score,
                lexical_score=lexical_score,
                bm25_score=bm25_score,
                vector_score=vector_score,
                selection_strategy=strategy,
                match_reasons=tuple(dict.fromkeys(reasons)),
                matched_terms=tuple(dict.fromkeys(matched_terms)),
                embedding_backend=document.embedding_backend,
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.card.system_name))
    pool_limit = candidate_limit
    if settings.rag_reranker_enabled:
        pool_limit = max(candidate_limit, max(2, settings.rag_reranker_candidate_pool))
    pool = candidates[:pool_limit]
    document_text = {document.card.system_name: document.text for document in documents}
    rerank = rerank_texts(
        query_rewrite.rerank_query,
        [document_text.get(candidate.card.system_name, candidate.card.summary) for candidate in pool],
    )
    reranked: list[ThermoRagCandidateRecord] = []
    for item in rerank.items[:candidate_limit]:
        candidate = pool[item.index]
        reasons = candidate.match_reasons
        if rerank.used_remote:
            reasons = tuple(dict.fromkeys((*reasons, "remote_rerank")))
        reranked.append(
            replace(
                candidate,
                match_reasons=reasons,
                rerank_score=(
                    round(item.relevance_score, 6) if item.relevance_score is not None else None
                ),
                reranker_backend=rerank.backend,
                original_rank=item.index + 1,
            )
        )
    return tuple(reranked)


def select_thermo_card(
    query: str,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
    auto_select_threshold: float | None = None,
    auto_select_margin: float | None = None,
) -> tuple[dict[str, object] | None, dict[str, object]]:
    threshold = settings.thermo_rag_auto_select_threshold if auto_select_threshold is None else auto_select_threshold
    margin = settings.thermo_rag_auto_select_margin if auto_select_margin is None else auto_select_margin
    lexical_minimum = settings.thermo_rag_min_score if min_score is None else min_score
    documents = build_thermo_card_index()
    embedding_backend = documents[0].embedding_backend if documents else effective_embedding_backend()
    candidates = search_thermo_cards(query, top_k=top_k, min_score=min_score)
    payload_candidates = [candidate.public_payload() for candidate in candidates]

    if not candidates:
        return None, {
            "matched": False,
            "query": query,
            "selection_strategy": "none",
            "candidates": payload_candidates,
            "threshold": threshold,
            "margin": margin,
            "embedding_backend": embedding_backend,
            "recommended_embedding_model": settings.thermo_rag_embedding_model,
        }

    top_candidate = candidates[0]
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    lexical_gate_passed = top_candidate.lexical_score >= lexical_minimum
    auto_selected = (
        lexical_gate_passed
        and top_candidate.score >= threshold
        and (top_candidate.score - second_score) >= margin
    )

    retrieval = {
        "matched": auto_selected,
        "query": query,
        "selection_strategy": "rag_auto_select" if auto_selected else "rag_candidates_only",
        "selected_system_name": top_candidate.card.system_name if auto_selected else None,
        "top_score": round(top_candidate.score, 3),
        "top_lexical_score": round(top_candidate.lexical_score, 3),
        "top_bm25_score": round(top_candidate.bm25_score, 3),
        "top_vector_score": round(top_candidate.vector_score, 3),
        "score_margin": round(top_candidate.score - second_score, 3),
        "lexical_gate_passed": lexical_gate_passed,
        "threshold": threshold,
        "margin": margin,
        "candidates": payload_candidates,
        "embedding_backend": embedding_backend,
        "recommended_embedding_model": settings.thermo_rag_embedding_model,
    }
    if not auto_selected:
        return None, retrieval
    return top_candidate.card.public_payload(), retrieval
