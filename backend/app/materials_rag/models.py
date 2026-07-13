from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MaterialsRagDocument(BaseModel):
    id: str
    domain: str
    doc_type: str
    title: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    source: str = ""
    source_url: str = ""
    trust_level: str = "medium"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MaterialsRagHit(BaseModel):
    document: MaterialsRagDocument
    score: float
    lexical_score: float = 0.0
    bm25_score: float = 0.0
    vector_score: float = 0.0
    embedding_backend: str = ""
    rerank_score: float | None = None
    reranker_backend: str = ""
    original_rank: int | None = None
    graph_score: float = 0.0
    graph_paths: list[str] = Field(default_factory=list)
    graph_community: str = ""
    matched_fields: list[str] = Field(default_factory=list)


class MaterialsRagQuery(BaseModel):
    query: str
    domain: str | None = None
    doc_type: str | None = None
    material: str | None = None
    top_k: int = 5


class MaterialsRagCandidate(BaseModel):
    id: str
    domain: str
    doc_type: str
    title: str
    score: float
    lexical_score: float = 0.0
    bm25_score: float = 0.0
    vector_score: float = 0.0
    embedding_backend: str = ""
    rerank_score: float | None = None
    reranker_backend: str = ""
    original_rank: int | None = None
    graph_score: float = 0.0
    graph_paths: list[str] = Field(default_factory=list)
    graph_community: str = ""
    matched_fields: list[str] = Field(default_factory=list)
    source: str = ""
    source_url: str = ""
    trust_level: str = "medium"
    materials: list[str] = Field(default_factory=list)
    content_excerpt: str = ""


class MaterialsRagSearchResponse(BaseModel):
    query: str
    matched: bool = False
    hits: list[MaterialsRagCandidate] = Field(default_factory=list)
    note: str = ""
