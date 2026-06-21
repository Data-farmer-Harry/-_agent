from __future__ import annotations

from typing import Any

from app.config import settings
from app.thermo.rag_retriever import select_thermo_card


class ThermoRagService:
    @staticmethod
    def search(query: str, *, top_k: int | None = None) -> dict[str, Any]:
        selected_card, retrieval = select_thermo_card(query, top_k=top_k)
        candidates = retrieval.get("candidates", [])
        return {
            "query": query,
            "matched": bool(candidates),
            "selection_strategy": retrieval.get("selection_strategy", "none"),
            "selected_system_name": (selected_card or {}).get("system_name") if selected_card else None,
            "embedding_backend": retrieval.get("embedding_backend", settings.thermo_rag_embedding_backend),
            "recommended_embedding_model": settings.thermo_rag_embedding_model,
            "candidates": candidates,
            "note": (
                "Thermo RAG now combines structured lexical matching with an additive vector layer. Vector similarity can expand or rerank candidates, but only lexically grounded high-confidence matches may auto-select a TDB; real execution still uses deterministic file paths."
            ),
        }

    @staticmethod
    def retrieve(query: str) -> tuple[dict[str, object] | None, dict[str, object]]:
        return select_thermo_card(query)
