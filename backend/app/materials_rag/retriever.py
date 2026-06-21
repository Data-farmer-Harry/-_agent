from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.core.bm25 import BM25Index, build_bm25_index, score_bm25
from app.materials_rag.document_store import load_materials_rag_documents
from app.materials_rag.models import MaterialsRagDocument, MaterialsRagHit, MaterialsRagQuery
from app.materials_rag.normalizer import canonical_terms, extract_materials, normalize_material, normalize_text, tokenize_text
from app.materials_rag.vector import build_embedding_with_backend, build_embeddings, effective_embedding_backend, embedding_signature
from app.rag.query_rewrite import rewrite_materials_query
from app.rag.reranker import rerank_texts
from app.rag.sqlite_vector_store import content_digest, get_vector_store


@dataclass(frozen=True)
class _IndexedDocument:
    document: MaterialsRagDocument
    normalized_title: str
    normalized_content: str
    normalized_keywords: tuple[str, ...]
    normalized_materials: tuple[str, ...]
    normalized_methods: tuple[str, ...]
    normalized_tools: tuple[str, ...]
    token_set: frozenset[str]
    bm25_tokens: tuple[str, ...]
    embedding_text: str
    embedding_backend: str


_INDEX_CACHE_KEY: tuple[object, ...] | None = None
_INDEX_CACHE_DOCUMENTS: tuple[_IndexedDocument, ...] = ()
_INDEX_CACHE_BM25: BM25Index | None = None


def _document_cache_fingerprint(documents: tuple[MaterialsRagDocument, ...]) -> tuple[tuple[str, str, str], ...]:
    return tuple((document.id, document.title, document.content) for document in documents)


def _embedding_text(document: MaterialsRagDocument) -> str:
    parts = [
        document.title,
        document.domain,
        document.doc_type,
        document.content,
        " ".join(document.keywords),
        " ".join(document.materials),
        " ".join(document.methods),
        " ".join(document.tools),
    ]
    return "\n".join(part for part in parts if part)


def _bm25_tokens_for_document(document: MaterialsRagDocument) -> tuple[str, ...]:
    text = _embedding_text(document)
    tokens: list[str] = []
    tokens.extend(tokenize_text(text))
    tokens.extend(canonical_terms(text))
    tokens.extend(f"material:{material}" for material in document.materials)
    tokens.extend(f"domain:{document.domain}")
    tokens.extend(f"doc_type:{document.doc_type}")
    tokens.extend(f"method:{method}" for method in document.methods)
    tokens.extend(f"tool:{tool}" for tool in document.tools)
    return tuple(token.lower() for token in tokens if token)


def _bm25_tokens_for_query(query: MaterialsRagQuery, *, search_text: str | None = None) -> tuple[str, ...]:
    query_text = search_text or query.query
    tokens: list[str] = []
    tokens.extend(tokenize_text(query_text))
    tokens.extend(canonical_terms(query_text))
    tokens.extend(f"material:{material}" for material in extract_materials(query_text))
    if query.material:
        normalized = normalize_material(query.material)
        if normalized:
            tokens.append(f"material:{normalized}")
    if query.domain:
        tokens.append(f"domain:{query.domain.strip().lower()}")
    if query.doc_type:
        tokens.append(f"doc_type:{query.doc_type.strip().lower()}")
    return tuple(token.lower() for token in tokens if token)


def _build_index() -> tuple[_IndexedDocument, ...]:
    return _build_index_with_bm25()[0]


def _build_index_with_bm25() -> tuple[tuple[_IndexedDocument, ...], BM25Index]:
    global _INDEX_CACHE_DOCUMENTS, _INDEX_CACHE_KEY, _INDEX_CACHE_BM25

    documents = load_materials_rag_documents()
    embedding_texts = [_embedding_text(document) for document in documents]
    document_ids = [document.id for document in documents]
    digest = content_digest(zip(document_ids, embedding_texts))
    store = get_vector_store()
    preferred_backend = effective_embedding_backend()
    preferred_signature = embedding_signature(preferred_backend)
    store_current = preferred_backend != "disabled" and store.collection_is_current(
        "materials_rag",
        embedding_signature=preferred_signature,
        content_digest_value=digest,
        document_ids=document_ids,
    )
    cache_key = (
        _document_cache_fingerprint(documents),
        preferred_signature,
        str(store.path),
        store_current,
    )
    if _INDEX_CACHE_KEY == cache_key and _INDEX_CACHE_BM25 is not None:
        return _INDEX_CACHE_DOCUMENTS, _INDEX_CACHE_BM25

    embedding_backend = preferred_backend
    actual_signature = preferred_signature
    if store_current:
        status = store.collection_status("materials_rag")
        if status is not None:
            embedding_backend = status.embedding_backend
            actual_signature = status.embedding_signature
    elif preferred_backend != "disabled":
        vectors, embedding_backend = build_embeddings(embedding_texts, backend=preferred_backend)
        actual_signature = embedding_signature(embedding_backend)
        if vectors and all(vector for vector in vectors):
            store.replace_collection(
                "materials_rag",
                embedding_signature=actual_signature,
                embedding_backend=embedding_backend,
                content_digest_value=digest,
                documents=[(document_id, vector) for document_id, vector in zip(document_ids, vectors)],
            )
            store_current = True
    cache_key = (
        _document_cache_fingerprint(documents),
        actual_signature,
        str(store.path),
        store_current,
    )
    indexed: list[_IndexedDocument] = []
    for document, embedding_text in zip(documents, embedding_texts):
        keyword_terms: list[str] = []
        for item in document.keywords:
            keyword_terms.extend(tokenize_text(item))
            keyword_terms.extend(canonical_terms(item))
        bm25_tokens = _bm25_tokens_for_document(document)
        indexed.append(
            _IndexedDocument(
                document=document,
                normalized_title=normalize_text(document.title),
                normalized_content=normalize_text(document.content),
                normalized_keywords=tuple(dict.fromkeys(keyword_terms)),
                normalized_materials=tuple(
                    dict.fromkeys(
                        normalized
                        for normalized in (normalize_material(item) for item in document.materials)
                        if normalized
                    )
                ),
                normalized_methods=tuple(item.strip().lower() for item in document.methods if item.strip()),
                normalized_tools=tuple(item.strip().lower() for item in document.tools if item.strip()),
                token_set=frozenset(tokenize_text(document.title + " " + document.content + " " + " ".join(document.keywords))),
                bm25_tokens=bm25_tokens,
                embedding_text=embedding_text,
                embedding_backend=embedding_backend,
            )
        )
    _INDEX_CACHE_KEY = cache_key
    _INDEX_CACHE_DOCUMENTS = tuple(indexed)
    _INDEX_CACHE_BM25 = build_bm25_index(item.bm25_tokens for item in _INDEX_CACHE_DOCUMENTS)
    return _INDEX_CACHE_DOCUMENTS, _INDEX_CACHE_BM25


def _excerpt(content: str, *, max_chars: int = 220) -> str:
    trimmed = " ".join(content.split())
    if len(trimmed) <= max_chars:
        return trimmed
    return trimmed[: max_chars - 1].rstrip() + "…"


def _domain_allowed(document: MaterialsRagDocument, domain_filter: str) -> tuple[bool, bool]:
    if not domain_filter:
        return True, False
    document_domain = document.domain.strip().lower()
    if document_domain == domain_filter:
        return True, False
    if (
        document.doc_type.strip().lower() == "encyclopedia_chunk"
        and domain_filter in {"materials", "metallurgy"}
        and document_domain in {"materials", "metallurgy"}
    ):
        return True, True
    return False, False


def search_materials_rag(query: MaterialsRagQuery) -> tuple[MaterialsRagHit, ...]:
    if not settings.materials_rag_enabled:
        return tuple()

    indexed_documents, bm25_index = _build_index_with_bm25()
    query_rewrite = rewrite_materials_query(query.query)
    search_text = query_rewrite.search_query
    normalized_query = normalize_text(search_text)
    query_tokens = set(tokenize_text(search_text))
    query_canonicals = set(canonical_terms(search_text))
    query_materials = set(extract_materials(search_text))
    if query.material:
        material_filter = normalize_material(query.material)
        if material_filter:
            query_materials.add(material_filter)

    domain_filter = (query.domain or "").strip().lower()
    doc_type_filter = (query.doc_type or "").strip().lower()
    material_filter = normalize_material(query.material)
    query_vector, query_embedding_backend = build_embedding_with_backend(search_text)
    query_bm25_tokens = _bm25_tokens_for_query(query, search_text=search_text)
    vector_scores: dict[str, float] = {}
    if query_vector and indexed_documents and query_embedding_backend == indexed_documents[0].embedding_backend:
        vector_scores = {
            hit.document_id: hit.similarity
            for hit in get_vector_store().search("materials_rag", query_vector, top_k=len(indexed_documents))
        }

    hits: list[MaterialsRagHit] = []
    for document_index, indexed in enumerate(indexed_documents):
        document = indexed.document
        domain_allowed, used_related_domain = _domain_allowed(document, domain_filter)
        if not domain_allowed:
            continue
        if doc_type_filter and document.doc_type.strip().lower() != doc_type_filter:
            continue
        if material_filter and indexed.normalized_materials and material_filter not in indexed.normalized_materials:
            continue

        lexical_score = 0.0
        matched_fields: list[str] = []

        for token in query_canonicals:
            term = token.split(":", 1)[-1].lower()
            if term and term in indexed.normalized_title:
                lexical_score += 2.2
                matched_fields.append("title")
                break
        else:
            for token in query_tokens:
                if token and token in indexed.normalized_title:
                    lexical_score += 1.6
                    matched_fields.append("title")
                    break

        keyword_overlap = query_canonicals.intersection(indexed.normalized_keywords) or query_tokens.intersection(indexed.normalized_keywords)
        if keyword_overlap:
            lexical_score += min(2.4, 0.9 * len(keyword_overlap))
            matched_fields.append("keywords")

        if query_materials and query_materials.intersection(indexed.normalized_materials):
            lexical_score += 1.4
            matched_fields.append("materials")

        token_overlap = query_tokens.intersection(indexed.token_set)
        if token_overlap:
            lexical_score += min(1.4, 0.18 * len(token_overlap))
            matched_fields.append("content")

        if any(term.startswith("analysis:") and term.split(":", 1)[-1] in indexed.normalized_methods for term in query_canonicals):
            lexical_score += 0.4
            matched_fields.append("methods")

        if "lammps" in normalized_query and "lammps" in indexed.normalized_tools:
            lexical_score += 0.3
            matched_fields.append("tools")

        if used_related_domain:
            matched_fields.append("related_domain")

        if query_rewrite.changed and matched_fields:
            matched_fields.append("query_rewrite")

        bm25_raw_score = score_bm25(query_bm25_tokens, bm25_index.documents[document_index], bm25_index)
        bm25_score = bm25_raw_score * settings.materials_rag_bm25_weight
        if bm25_score > 0:
            matched_fields.append("bm25")

        vector_similarity = vector_scores.get(document.id, 0.0)
        if vector_similarity >= settings.materials_rag_vector_min_similarity:
            matched_fields.append("vector")

        score = lexical_score + bm25_score + (vector_similarity * settings.materials_rag_vector_weight)

        if document.trust_level.strip().lower() == "high" and score > 0:
            score *= 1.05

        if score <= 0:
            continue

        hits.append(
            MaterialsRagHit(
                document=document,
                score=round(score, 3),
                lexical_score=round(lexical_score, 3),
                bm25_score=round(bm25_score, 3),
                vector_score=round(vector_similarity, 3),
                embedding_backend=indexed.embedding_backend,
                matched_fields=list(dict.fromkeys(matched_fields)),
            )
        )

    hits.sort(key=lambda item: (-item.score, item.document.title.lower(), item.document.id))
    final_limit = max(1, query.top_k)
    pool_limit = final_limit
    if settings.rag_reranker_enabled:
        pool_limit = max(final_limit, max(2, settings.rag_reranker_candidate_pool))
    pool = hits[:pool_limit]
    rerank = rerank_texts(
        query_rewrite.rerank_query,
        [_embedding_text(hit.document) for hit in pool],
    )
    output: list[MaterialsRagHit] = []
    for item in rerank.items[:final_limit]:
        hit = pool[item.index]
        output.append(
            hit.model_copy(
                update={
                    "document": hit.document.model_copy(update={"content": _excerpt(hit.document.content)}),
                    "rerank_score": (
                        round(item.relevance_score, 6) if item.relevance_score is not None else None
                    ),
                    "reranker_backend": rerank.backend,
                    "original_rank": item.index + 1,
                }
            )
        )
    return tuple(output)
