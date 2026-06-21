from __future__ import annotations

from dataclasses import dataclass

from app.core.bm25 import BM25DocumentStats
from app.thermo.registry import ThermoDatabaseCard


@dataclass(frozen=True)
class ThermoCardDocument:
    card: ThermoDatabaseCard
    text: str
    normalized_names: tuple[str, ...]
    tokens: tuple[str, ...]
    component_aliases: tuple[str, ...]
    phase_aliases: tuple[str, ...]
    tag_aliases: tuple[str, ...]
    bm25_tokens: tuple[str, ...]
    bm25_stats: BM25DocumentStats
    embedding_backend: str
    embedding_signature: str


@dataclass(frozen=True)
class ThermoRagCandidateRecord:
    card: ThermoDatabaseCard
    score: float
    lexical_score: float
    bm25_score: float
    vector_score: float
    selection_strategy: str
    match_reasons: tuple[str, ...]
    matched_terms: tuple[str, ...]
    embedding_backend: str = ""
    rerank_score: float | None = None
    reranker_backend: str = ""
    original_rank: int | None = None

    def public_payload(self) -> dict[str, object]:
        return {
            "system_name": self.card.system_name,
            "score": round(self.score, 3),
            "lexical_score": round(self.lexical_score, 3),
            "bm25_score": round(self.bm25_score, 3),
            "vector_score": round(self.vector_score, 3),
            "selection_strategy": self.selection_strategy,
            "embedding_backend": self.embedding_backend,
            "rerank_score": round(self.rerank_score, 6) if self.rerank_score is not None else None,
            "reranker_backend": self.reranker_backend,
            "original_rank": self.original_rank,
            "match_reasons": list(self.match_reasons),
            "matched_terms": list(self.matched_terms),
            "aliases": list(self.card.aliases),
            "components": list(self.card.components),
            "phases": list(self.card.phases),
            "tags": list(self.card.tags),
            "database_name": self.card.database_name,
            "summary": self.card.summary,
            "source_url": self.card.source_url,
        }
