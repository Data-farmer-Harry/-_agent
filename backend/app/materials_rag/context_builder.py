from __future__ import annotations

from app.materials_rag.models import MaterialsRagHit


def build_materials_rag_context(*, query: str, hits: list[MaterialsRagHit] | tuple[MaterialsRagHit, ...], max_items: int = 4) -> str:
    if not hits:
        return ""

    lines = ["[材料知识检索结果]"]
    for index, hit in enumerate(list(hits)[:max_items], start=1):
        document = hit.document
        lines.append(f"{index}. title: {document.title}")
        lines.append(f"   domain/doc_type: {document.domain} / {document.doc_type}")
        lines.append(
            "   score: "
            f"total={hit.score:.3f}, lexical={hit.lexical_score:.3f}, "
            f"bm25={hit.bm25_score:.3f}, vector={hit.vector_score:.3f}, backend={hit.embedding_backend or 'none'}"
        )
        if hit.rerank_score is not None:
            lines.append(
                f"   rerank: score={hit.rerank_score:.6f}, backend={hit.reranker_backend}, "
                f"original_rank={hit.original_rank}"
            )
        lines.append(f"   matched_fields: {', '.join(hit.matched_fields) or 'none'}")
        lines.append(f"   content: {document.content}")
        if document.materials:
            lines.append(f"   materials: {', '.join(document.materials)}")
        lines.append(f"   source: {document.source}")
        if document.source_url:
            lines.append(f"   url: {document.source_url}")
    lines.append("这些内容只作为背景知识增强，不能覆盖现有 registry、参数校验或真实计算结果。")
    return "\n".join(lines)
