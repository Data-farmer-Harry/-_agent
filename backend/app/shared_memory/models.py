from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


ScopeType = Literal["run", "conversation", "user", "global"]
ItemType = Literal["constraint", "fact", "evidence", "result", "preference", "finding", "repair"]
MemoryStatus = Literal["active", "superseded", "conflicted", "quarantined"]
MemoryAuthority = Literal["user", "execution", "registry", "validated_document", "rag", "llm_inference"]
MemoryPolarity = Literal["positive", "negative", "unknown"]
ConflictStatus = Literal["open", "resolved", "dismissed", "needs_user"]
ConflictType = Literal["value", "unit", "polarity", "authority", "context", "version"]
ConflictDetectionMode = Literal["structured", "heuristic", "semantic_candidate"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_memory_id() -> str:
    return f"mem-{uuid4().hex[:16]}"


def new_conflict_id() -> str:
    return f"conf-{uuid4().hex[:16]}"


class MemoryScope(BaseModel):
    scope_type: ScopeType
    scope_id: str
    conversation_id: str = ""
    user_id: str = ""
    include_global: bool = True

    @field_validator("scope_id")
    @classmethod
    def _scope_id_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("scope_id must be non-empty")
        return normalized

    def visible_scope_keys(self) -> list[tuple[str, str]]:
        scopes: list[tuple[str, str]] = [(self.scope_type, self.scope_id)]
        if self.scope_type == "run" and self.conversation_id:
            scopes.append(("conversation", self.conversation_id))
        if self.scope_type in {"run", "conversation"} and self.user_id:
            scopes.append(("user", self.user_id))
        if self.scope_type == "user" and self.scope_id:
            scopes = [("user", self.scope_id)]
        if self.include_global:
            scopes.append(("global", "global"))
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in scopes:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped


class MemoryItem(BaseModel):
    memory_id: str = Field(default_factory=new_memory_id)
    schema_version: str = "shared-memory/v1"
    scope_type: ScopeType
    scope_id: str
    item_type: ItemType
    subject: str
    predicate: str
    value: Any = None
    unit: str = ""
    text: str = ""
    normalized_text: str = ""
    polarity: MemoryPolarity = "unknown"
    status: MemoryStatus = "active"
    authority: MemoryAuthority = "llm_inference"
    confidence: float = 1.0
    source_refs: list[str] = Field(default_factory=list)
    content_hash: str = ""
    normalized_hash: str = ""
    embedding_id: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope_id", "item_type", "subject", "predicate")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory item fields cannot be empty")
        return normalized

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))


class RawEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"ev-{uuid4().hex[:16]}")
    memory_id: str
    source_type: str
    source_ref: str
    path_or_url: str = ""
    content_hash: str = ""
    mime_type: str = "text/plain"
    excerpt: str = ""
    full_content_inline: bool = False
    hash_verified: bool | None = None
    verification_error: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceDigest(BaseModel):
    """Bounded L2 digest with a mandatory pointer back to raw L3 evidence."""

    digest_id: str = Field(default_factory=lambda: f"digest-{uuid4().hex[:16]}")
    schema_version: str = "shared-memory-evidence-digest/v1"
    memory_id: str
    l1_fields: dict[str, Any] = Field(default_factory=dict)
    l2_summary: str = ""
    l3_raw_evidence_ids: list[str] = Field(default_factory=list)
    content_hash: str = ""
    source_refs: list[str] = Field(default_factory=list)
    compression_method: str = ""
    protected: bool = False
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("memory_id")
    @classmethod
    def _memory_id_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory_id must be non-empty")
        return normalized


class WorkingState(BaseModel):
    """Cross-agent L1 working context assembled from shared memory retrieval."""

    schema_version: str = "shared-memory-working-state/v1"
    run_id: str = ""
    conversation_id: str = ""
    scope_filter: list[tuple[str, str]] = Field(default_factory=list)
    locked_facts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_digests: list[EvidenceDigest] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    raw_evidence_ids: list[str] = Field(default_factory=list)
    retrieval_backend: str = ""
    prompt_budget_bytes: int = 0
    estimated_after_bytes: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConflictRecord(BaseModel):
    conflict_id: str = Field(default_factory=new_conflict_id)
    left_memory_id: str
    right_memory_id: str
    conflict_type: ConflictType
    detection_mode: ConflictDetectionMode = "structured"
    status: ConflictStatus = "open"
    evidence_refs: list[str] = Field(default_factory=list)
    resolution: dict[str, Any] | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConflictResolution(BaseModel):
    resolver: str = "system"
    decision: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    resolved_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteResult(BaseModel):
    item: MemoryItem
    created: bool
    deduplicated: bool = False
    dedup_level: str = ""
    duplicate_of: str = ""
    merged_source_refs: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    conflict_statuses: list[str] = Field(default_factory=list)
    conflict_types: list[str] = Field(default_factory=list)


class MemoryRetrievalCandidate(BaseModel):
    item: MemoryItem
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class MemoryRetrievalResult(BaseModel):
    query: str
    rewritten_query: str = ""
    expansion_terms: list[str] = Field(default_factory=list)
    scope_filter: list[tuple[str, str]]
    candidates: list[MemoryRetrievalCandidate] = Field(default_factory=list)
    selected_item_ids: list[str] = Field(default_factory=list)
    dropped_item_ids: list[str] = Field(default_factory=list)
    dropped_reasons: dict[str, str] = Field(default_factory=dict)
    forced_retention_ids: list[str] = Field(default_factory=list)
    prompt_budget_bytes: int = 12_288
    estimated_before_bytes: int = 0
    estimated_after_bytes: int = 0
    retrieval_backend: str = "bm25_lexical_fallback"
