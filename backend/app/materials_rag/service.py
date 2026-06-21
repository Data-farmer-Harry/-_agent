from __future__ import annotations

from app.materials_rag.context_builder import build_materials_rag_context
from app.materials_rag.models import MaterialsRagCandidate, MaterialsRagHit, MaterialsRagQuery, MaterialsRagSearchResponse
from app.materials_rag.retriever import search_materials_rag


class MaterialsRagService:
    @staticmethod
    def search(
        query: str,
        *,
        domain: str | None = None,
        doc_type: str | None = None,
        material: str | None = None,
        top_k: int = 5,
    ) -> list[MaterialsRagHit]:
        request = MaterialsRagQuery(
            query=query,
            domain=domain,
            doc_type=doc_type,
            material=material,
            top_k=top_k,
        )
        return list(search_materials_rag(request))

    @classmethod
    def build_context(
        cls,
        query: str,
        *,
        domain: str | None = None,
        doc_type: str | None = None,
        material: str | None = None,
        top_k: int = 5,
        max_items: int = 4,
    ) -> str:
        hits = cls.search(query, domain=domain, doc_type=doc_type, material=material, top_k=top_k)
        return build_materials_rag_context(query=query, hits=hits, max_items=max_items)

    @classmethod
    def search_payload(
        cls,
        query: str,
        *,
        domain: str | None = None,
        doc_type: str | None = None,
        material: str | None = None,
        top_k: int = 5,
    ) -> dict[str, object]:
        hits = cls.search(query, domain=domain, doc_type=doc_type, material=material, top_k=top_k)
        response = MaterialsRagSearchResponse(
            query=query,
            matched=bool(hits),
            hits=[
                MaterialsRagCandidate(
                    id=hit.document.id,
                    domain=hit.document.domain,
                    doc_type=hit.document.doc_type,
                    title=hit.document.title,
                    score=hit.score,
                    lexical_score=hit.lexical_score,
                    bm25_score=hit.bm25_score,
                    vector_score=hit.vector_score,
                    embedding_backend=hit.embedding_backend,
                    rerank_score=hit.rerank_score,
                    reranker_backend=hit.reranker_backend,
                    original_rank=hit.original_rank,
                    matched_fields=hit.matched_fields,
                    source=hit.document.source,
                    source_url=hit.document.source_url,
                    trust_level=hit.document.trust_level,
                    materials=hit.document.materials,
                    content_excerpt=hit.document.content,
                )
                for hit in hits
            ],
            note=(
                "Materials RAG is a service-layer knowledge enhancer. "
                "It can improve explanation, workflow suggestions, and LAMMPS error diagnosis, but it does not override registry checks, schema validation, or the real execution runtime."
            ),
        )
        return response.model_dump(mode="json")
