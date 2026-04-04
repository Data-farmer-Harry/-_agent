from __future__ import annotations

import re
from typing import Iterable

from app.config import settings
from app.thermo.rag_index import TOKEN_PATTERN, build_thermo_card_index
from app.thermo.rag_models import ThermoRagCandidateRecord
from app.utils.constants import normalize_system_key


def _tokenize_query(text: str) -> tuple[str, ...]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text or "")]
    return tuple(dict.fromkeys(token for token in tokens if token))


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
    normalized_query = normalize_system_key(query)
    query_tokens = set(_tokenize_query(query))
    minimum_score = settings.thermo_rag_min_score if min_score is None else min_score
    candidate_limit = settings.thermo_rag_top_k if top_k is None else top_k
    candidates: list[ThermoRagCandidateRecord] = []

    for document in build_thermo_card_index():
        score = 0.0
        reasons: list[str] = []
        matched_terms: list[str] = []

        if normalized_query and normalized_query in document.normalized_names:
            score += 1.0
            reasons.append("exact_system_or_alias_match")
            matched_terms.append(document.card.system_name)

        name_matches = _match_terms(query, document.card.all_names())
        if name_matches:
            score += 0.84
            reasons.append("name_text_match")
            matched_terms.extend(name_matches)

        component_matches = _match_terms(query, document.component_aliases)
        if component_matches:
            component_bonus = 0.28 * min(len(component_matches), len(document.card.components))
            score += component_bonus
            reasons.append("component_alias_match")
            matched_terms.extend(component_matches)

        phase_matches = _match_terms(query, document.phase_aliases)
        if phase_matches:
            score += min(0.18, 0.08 * len(phase_matches))
            reasons.append("phase_match")
            matched_terms.extend(phase_matches)

        tag_matches = _match_terms(query, document.tag_aliases)
        if tag_matches:
            score += min(0.12, 0.05 * len(tag_matches))
            reasons.append("tag_match")
            matched_terms.extend(tag_matches)

        if query_tokens and document.tokens:
            overlap = query_tokens.intersection(document.tokens)
            if overlap:
                lexical_score = min(0.26, 0.09 * len(overlap))
                score += lexical_score
                reasons.append("lexical_overlap")
                matched_terms.extend(sorted(overlap))

        if score < minimum_score:
            continue

        strategy = reasons[0] if reasons else "weak_match"
        candidates.append(
            ThermoRagCandidateRecord(
                card=document.card,
                score=score,
                selection_strategy=strategy,
                match_reasons=tuple(dict.fromkeys(reasons)),
                matched_terms=tuple(dict.fromkeys(matched_terms)),
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.card.system_name))
    return tuple(candidates[:candidate_limit])


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
            "recommended_embedding_model": settings.thermo_rag_embedding_model,
        }

    top_candidate = candidates[0]
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    auto_selected = top_candidate.score >= threshold and (top_candidate.score - second_score) >= margin

    retrieval = {
        "matched": auto_selected,
        "query": query,
        "selection_strategy": "rag_auto_select" if auto_selected else "rag_candidates_only",
        "selected_system_name": top_candidate.card.system_name if auto_selected else None,
        "top_score": round(top_candidate.score, 3),
        "score_margin": round(top_candidate.score - second_score, 3),
        "threshold": threshold,
        "margin": margin,
        "candidates": payload_candidates,
        "recommended_embedding_model": settings.thermo_rag_embedding_model,
    }
    if not auto_selected:
        return None, retrieval
    return top_candidate.card.public_payload(), retrieval
