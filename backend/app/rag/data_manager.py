from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.materials_rag.document_store import DEFAULT_DOCUMENTS_PATHS, load_materials_rag_documents
from app.materials_rag.service import MaterialsRagService
from app.rag.sqlite_vector_store import get_vector_store
from app.thermo.rag_service import ThermoRagService
from app.thermo.registry import load_thermo_database_cards
from app.utils.path_utils import read_json_file_if_exists


class RagCollectionProfile(BaseModel):
    name: str
    document_count: int
    retrieval_modes: list[str] = Field(default_factory=list)
    embedding_backend: str = ""
    embedding_model: str = ""
    vector_dimensions_config: int = 0
    vector_dimensions_observed: int | None = None
    bm25_weight: float = 0.0
    vector_weight: float = 0.0
    source_files: list[str] = Field(default_factory=list)
    domains: dict[str, int] = Field(default_factory=dict)
    doc_types: dict[str, int] = Field(default_factory=dict)
    systems: list[str] = Field(default_factory=list)
    vector_store_backend: str = ""
    vector_store_path: str = ""
    vector_store_indexed_documents: int = 0


class RagBenchmarkProfile(BaseModel):
    available: bool = False
    generated_at: str = ""
    top_k: int | None = None
    elapsed_seconds: float | None = None
    materials_summary: dict[str, Any] = Field(default_factory=dict)
    wikipedia_materials_summary: dict[str, Any] = Field(default_factory=dict)
    thermo_summary: dict[str, Any] = Field(default_factory=dict)


class RagManagerReport(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    collections: list[RagCollectionProfile] = Field(default_factory=list)
    benchmark: RagBenchmarkProfile = Field(default_factory=RagBenchmarkProfile)
    notes: list[str] = Field(default_factory=list)


class RagSearchBundle(BaseModel):
    query: str
    top_k: int = 5
    materials_rag: dict[str, Any] = Field(default_factory=dict)
    thermo_rag: dict[str, Any] = Field(default_factory=dict)


class RagDataManager:
    """Read-only manager that summarizes and probes the active RAG stores."""

    def __init__(self, benchmark_path: Path | None = None) -> None:
        self.benchmark_path = benchmark_path or (settings.tmp_dir / "rag_recall" / "latest.json")

    def _latest_benchmark(self) -> RagBenchmarkProfile:
        payload = read_json_file_if_exists(self.benchmark_path)
        if not isinstance(payload, dict):
            return RagBenchmarkProfile()
        return RagBenchmarkProfile(
            available=True,
            generated_at=str(payload.get("generated_at") or ""),
            top_k=payload.get("top_k") if isinstance(payload.get("top_k"), int) else None,
            elapsed_seconds=payload.get("elapsed_seconds") if isinstance(payload.get("elapsed_seconds"), (int, float)) else None,
            materials_summary=(payload.get("materials_rag") or {}).get("summary", {})
            if isinstance(payload.get("materials_rag"), dict)
            else {},
            wikipedia_materials_summary=(payload.get("wikipedia_materials_rag") or {}).get("summary", {})
            if isinstance(payload.get("wikipedia_materials_rag"), dict)
            else {},
            thermo_summary=(payload.get("thermo_rag") or {}).get("summary", {}) if isinstance(payload.get("thermo_rag"), dict) else {},
        )

    def _observed_vector_dims(self) -> dict[str, int | None]:
        payload = read_json_file_if_exists(self.benchmark_path)
        if not isinstance(payload, dict) or not isinstance(payload.get("embedding"), dict):
            return {"materials": None, "thermo": None}
        embedding = payload["embedding"]
        return {
            "materials": embedding.get("materials_vector_dim") if isinstance(embedding.get("materials_vector_dim"), int) else None,
            "thermo": embedding.get("thermo_vector_dim") if isinstance(embedding.get("thermo_vector_dim"), int) else None,
        }

    def inventory(self) -> RagManagerReport:
        materials_documents = load_materials_rag_documents()
        thermo_cards = load_thermo_database_cards()
        observed_dims = self._observed_vector_dims()
        vector_inventory = get_vector_store().inventory()
        vector_collections = {
            str(item.get("collection")): item
            for item in vector_inventory.get("collections", [])
            if isinstance(item, dict)
        }
        materials_vectors = vector_collections.get("materials_rag", {})
        thermo_vectors = vector_collections.get("thermo_rag", {})

        materials_domains = Counter(document.domain for document in materials_documents)
        materials_doc_types = Counter(document.doc_type for document in materials_documents)
        thermo_families = Counter(card.family for card in thermo_cards)
        thermo_formats = Counter(card.format for card in thermo_cards)

        return RagManagerReport(
            collections=[
                RagCollectionProfile(
                    name="materials_rag",
                    document_count=len(materials_documents),
                    retrieval_modes=["structured_lexical", "bm25_sparse", "sqlite_vec_dense_knn"],
                    embedding_backend=settings.materials_rag_embedding_backend,
                    embedding_model=settings.materials_rag_embedding_model,
                    vector_dimensions_config=settings.materials_rag_embedding_dimensions,
                    vector_dimensions_observed=int(materials_vectors.get("dimensions") or 0) or observed_dims["materials"],
                    bm25_weight=settings.materials_rag_bm25_weight,
                    vector_weight=settings.materials_rag_vector_weight,
                    source_files=[str(path) for path in DEFAULT_DOCUMENTS_PATHS if path.exists()],
                    domains=dict(materials_domains),
                    doc_types=dict(materials_doc_types),
                    systems=sorted({material for doc in materials_documents for material in doc.materials})[:80],
                    vector_store_backend=str(vector_inventory.get("backend") or ""),
                    vector_store_path=str(vector_inventory.get("database_path") or ""),
                    vector_store_indexed_documents=int(materials_vectors.get("document_count") or 0),
                ),
                RagCollectionProfile(
                    name="thermo_rag",
                    document_count=len(thermo_cards),
                    retrieval_modes=["exact_alias_registry", "structured_lexical", "bm25_sparse", "sqlite_vec_dense_knn"],
                    embedding_backend=settings.thermo_rag_embedding_backend,
                    embedding_model=settings.thermo_rag_embedding_model,
                    vector_dimensions_config=settings.thermo_rag_embedding_dimensions,
                    vector_dimensions_observed=int(thermo_vectors.get("dimensions") or 0) or observed_dims["thermo"],
                    bm25_weight=settings.thermo_rag_bm25_weight,
                    vector_weight=settings.thermo_rag_vector_weight,
                    source_files=["backend/configs/thermo_registry.json", "backend/configs/thermo_databases/"],
                    domains=dict(thermo_families),
                    doc_types=dict(thermo_formats),
                    systems=sorted(card.system_name for card in thermo_cards),
                    vector_store_backend=str(vector_inventory.get("backend") or ""),
                    vector_store_path=str(vector_inventory.get("database_path") or ""),
                    vector_store_indexed_documents=int(thermo_vectors.get("document_count") or 0),
                ),
            ],
            benchmark=self._latest_benchmark(),
            notes=[
                "RAG manager is read-only by default; ingestion/build scripts remain explicit operations.",
                "Dense vectors are persisted and queried through SQLite + sqlite-vec; BM25 and structured lexical retrieval remain enabled.",
                "Execution still uses deterministic registry/TDB/runtime paths after retrieval.",
            ],
        )

    @staticmethod
    def search(query: str, *, top_k: int = 5) -> RagSearchBundle:
        return RagSearchBundle(
            query=query,
            top_k=top_k,
            materials_rag=MaterialsRagService.search_payload(query, top_k=top_k),
            thermo_rag=ThermoRagService.search(query, top_k=top_k),
        )
