from __future__ import annotations

from typing import Any

from app.config import settings
from app.thermo.rag_retriever import search_thermo_cards, select_thermo_card


class ThermoRagService:
    @staticmethod
    def search(query: str, *, top_k: int | None = None) -> dict[str, Any]:
        selected_card, retrieval = select_thermo_card(query, top_k=top_k)
        candidates = search_thermo_cards(query, top_k=top_k)
        return {
            "query": query,
            "matched": bool(candidates),
            "selection_strategy": retrieval.get("selection_strategy", "none"),
            "selected_system_name": (selected_card or {}).get("system_name") if selected_card else None,
            "recommended_embedding_model": settings.thermo_rag_embedding_model,
            "candidates": [candidate.public_payload() for candidate in candidates],
            "note": (
                "Thermo RAG v1 retrieves structured registry cards first; only high-confidence matches can auto-select a TDB, and real execution still uses deterministic file paths."
            ),
        }

    @staticmethod
    def retrieve(query: str) -> tuple[dict[str, object] | None, dict[str, object]]:
        return select_thermo_card(query)
