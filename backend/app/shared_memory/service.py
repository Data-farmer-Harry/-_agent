from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from app.rag.sqlite_vector_store import SqliteVectorStore, content_digest
from app.shared_memory.conflicts import detect_structured_conflicts
from app.shared_memory.models import (
    ConflictRecord,
    ConflictResolution,
    EvidenceDigest,
    MemoryItem,
    MemoryRetrievalResult,
    MemoryScope,
    MemoryWriteResult,
    RawEvidence,
    WorkingState,
)
from app.shared_memory.retrieval import (
    bm25_tokens_for_query,
    deterministic_text_vector,
    embedding_id_for_tokens,
    embedding_tokens_for_item,
    retrieve_from_items,
    rewrite_memory_query,
)
from app.shared_memory.store import SharedMemoryStore


_SHARED_MEMORY_DENSE_BACKEND = "deterministic_hash_v1"
_SHARED_MEMORY_DENSE_DIMENSIONS = 96
_SHARED_MEMORY_DENSE_SIGNATURE = f"shared_memory:{_SHARED_MEMORY_DENSE_BACKEND}:{_SHARED_MEMORY_DENSE_DIMENSIONS}"


class SharedMemoryService:
    """Unified entry point for cross-agent shared memory.

    This first phase intentionally stays deterministic: SQLite canonical store,
    hard scope filters, exact/normalized dedup, BM25, deterministic dense fallback,
    R2 reranking and R3 evidence expansion. Production embedding/sqlite-vec can
    replace the fallback without changing the public service contract.
    """

    def __init__(self, root_dir: Path, *, store: SharedMemoryStore | None = None) -> None:
        self.store = store or SharedMemoryStore(root_dir=root_dir)

    def write(self, item: MemoryItem) -> MemoryWriteResult:
        result = self.store.write_item(item)
        if not result.created or result.item.status != "active":
            return result
        same_scope_items = self.store.list_items(
            scope=MemoryScope(scope_type=result.item.scope_type, scope_id=result.item.scope_id, include_global=False),
            item_types=[result.item.item_type],
            statuses=["active"],
            limit=1000,
        )
        conflicts = detect_structured_conflicts(incoming=result.item, existing_items=same_scope_items)
        for conflict in conflicts:
            self.store.record_conflict(conflict)
        if not conflicts:
            return result
        updated_item = self._apply_conflict_post_write_actions(result.item, conflicts)
        return result.model_copy(
            update={
                "item": updated_item,
                "conflict_ids": [conflict.conflict_id for conflict in conflicts],
                "conflict_statuses": [conflict.status for conflict in conflicts],
                "conflict_types": [conflict.conflict_type for conflict in conflicts],
            }
        )

    def retrieve(
        self,
        *,
        query: str,
        scope: MemoryScope,
        item_types: list[str] | None = None,
        top_k: int = 6,
        prompt_budget_bytes: int = 12_288,
    ) -> MemoryRetrievalResult:
        candidates = self.store.list_items(scope=scope, item_types=item_types, statuses=["active"], limit=200)
        dense_scores = self._sqlite_vec_dense_scores(
            query=query,
            scope=scope,
            items=candidates,
            item_types=item_types,
            top_k=top_k,
        )
        return retrieve_from_items(
            query=query,
            scope=scope,
            items=candidates,
            top_k=top_k,
            prompt_budget_bytes=prompt_budget_bytes,
            vector_cache=self.store.get_or_create_embedding_vector,
            dense_scores=dense_scores,
            dense_backend="sqlite_vec" if dense_scores is not None else "deterministic_dense_fallback",
        )

    def expand_evidence(self, evidence_ids: list[str]) -> list[RawEvidence]:
        return self.store.expand_raw_evidence(evidence_ids)

    def write_raw_evidence(self, evidence: RawEvidence) -> RawEvidence:
        return self.store.write_raw_evidence(evidence)

    def build_working_state(
        self,
        retrieval: MemoryRetrievalResult,
        *,
        run_id: str = "",
        conversation_id: str = "",
    ) -> WorkingState:
        locked_facts: list[dict[str, Any]] = []
        digests: list[EvidenceDigest] = []
        raw_evidence_ids: list[str] = []
        conflict_ids: list[str] = []
        for candidate in retrieval.candidates:
            item = candidate.item
            metadata = item.metadata or {}
            compression = metadata.get("context_compression") if isinstance(metadata.get("context_compression"), dict) else {}
            l3 = compression.get("l3") if isinstance(compression.get("l3"), dict) else {}
            item_raw_ids = [str(value) for value in (metadata.get("raw_evidence_ids") or l3.get("raw_evidence_ids") or [])]
            raw_evidence_ids.extend(item_raw_ids)
            conflict_ids.extend(str(value) for value in metadata.get("conflict_ids", []) if value)
            l1_fields = {
                "memory_id": item.memory_id,
                "item_type": item.item_type,
                "subject": item.subject,
                "predicate": item.predicate,
                "value": item.value,
                "unit": item.unit,
                "polarity": item.polarity,
                "authority": item.authority,
                "confidence": item.confidence,
                "status": item.status,
            }
            if item.memory_id in retrieval.forced_retention_ids:
                locked_facts.append(l1_fields)
            digests.append(
                EvidenceDigest(
                    memory_id=item.memory_id,
                    l1_fields=l1_fields,
                    l2_summary=item.text,
                    l3_raw_evidence_ids=item_raw_ids,
                    content_hash=item.content_hash,
                    source_refs=list(item.source_refs),
                    compression_method=str(compression.get("method") or "preserve_original"),
                    protected=bool(compression.get("protected", False)),
                    metadata={"score": candidate.score, "reasons": list(candidate.reasons)},
                )
            )
        return WorkingState(
            run_id=run_id,
            conversation_id=conversation_id,
            scope_filter=list(retrieval.scope_filter),
            locked_facts=locked_facts,
            evidence_digests=digests,
            conflict_ids=_dedupe_strings(conflict_ids),
            raw_evidence_ids=_dedupe_strings(raw_evidence_ids),
            retrieval_backend=retrieval.retrieval_backend,
            prompt_budget_bytes=retrieval.prompt_budget_bytes,
            estimated_after_bytes=retrieval.estimated_after_bytes,
            metadata={
                "selected_item_ids": list(retrieval.selected_item_ids),
                "forced_retention_ids": list(retrieval.forced_retention_ids),
                "dropped_item_ids": list(retrieval.dropped_item_ids),
            },
        )

    def resolve_conflict(self, conflict_id: str, decision: ConflictResolution) -> ConflictRecord:
        return self.store.resolve_conflict(conflict_id, decision)

    def record_conflict(self, conflict: ConflictRecord) -> ConflictRecord:
        return self.store.record_conflict(conflict)

    def _sqlite_vec_dense_scores(
        self,
        *,
        query: str,
        scope: MemoryScope,
        items: list[MemoryItem],
        item_types: list[str] | None,
        top_k: int,
    ) -> dict[str, float] | None:
        """Return sqlite-vec dense scores for visible shared memories.

        The canonical memory rows and embedding cache remain in
        ``memory.sqlite3``. The sqlite-vec collection is a rebuildable sidecar
        index under the same memory root, keyed by scope/type/content digest.
        If sqlite-vec is unavailable or the sidecar fails, retrieval safely
        falls back to the existing persistent deterministic dense cache.
        """

        if not items or not SqliteVectorStore.extension_available():
            return None
        rewrite = rewrite_memory_query(query)
        query_tokens = ("query", *bm25_tokens_for_query(rewrite.search_query))
        query_embedding = self.store.get_or_create_embedding_vector(
            embedding_id=embedding_id_for_tokens(query_tokens),
            tokens=query_tokens,
            vector_factory=deterministic_text_vector,
            backend=_SHARED_MEMORY_DENSE_BACKEND,
            dimensions=_SHARED_MEMORY_DENSE_DIMENSIONS,
            metadata={"kind": "query", "index": "shared_memory_sqlite_vec"},
        )
        query_vector = tuple(float(value) for value in query_embedding["vector"])
        if not query_vector:
            return None

        documents: list[tuple[str, tuple[float, ...]]] = []
        digest_inputs: list[tuple[str, str]] = []
        for item in items:
            item_tokens = embedding_tokens_for_item(item)
            item_embedding = self.store.get_or_create_embedding_vector(
                embedding_id=item.embedding_id or embedding_id_for_tokens(item_tokens),
                tokens=item_tokens,
                vector_factory=deterministic_text_vector,
                backend=_SHARED_MEMORY_DENSE_BACKEND,
                dimensions=_SHARED_MEMORY_DENSE_DIMENSIONS,
                metadata={"kind": "memory_item", "memory_id": item.memory_id, "index": "shared_memory_sqlite_vec"},
            )
            vector = tuple(float(value) for value in item_embedding["vector"])
            if len(vector) != _SHARED_MEMORY_DENSE_DIMENSIONS:
                continue
            documents.append((item.memory_id, vector))
            digest_inputs.append(
                (
                    item.memory_id,
                    "|".join(
                        [
                            item.embedding_id,
                            item.content_hash,
                            item.normalized_hash,
                            item.updated_at,
                            str(item.status),
                        ]
                    ),
                )
            )
        if not documents:
            return None

        scope_key = ";".join(f"{scope_type}:{scope_id}" for scope_type, scope_id in scope.visible_scope_keys())
        type_key = ",".join(sorted(item_types or []))
        collection_hash = sha256(f"{scope_key}|{type_key}".encode("utf-8")).hexdigest()[:16]
        collection = f"shared_memory:{collection_hash}"
        digest = content_digest(digest_inputs)
        vector_store = SqliteVectorStore(self.store.root_dir / "shared_memory_vectors.sqlite3")
        document_ids = [memory_id for memory_id, _ in documents]
        try:
            if not vector_store.collection_is_current(
                collection,
                embedding_signature=_SHARED_MEMORY_DENSE_SIGNATURE,
                content_digest_value=digest,
                document_ids=document_ids,
            ):
                vector_store.replace_collection(
                    collection,
                    embedding_signature=_SHARED_MEMORY_DENSE_SIGNATURE,
                    embedding_backend=_SHARED_MEMORY_DENSE_BACKEND,
                    content_digest_value=digest,
                    documents=documents,
                )
            hit_limit = min(len(documents), max(top_k * 8, 30))
            hits = vector_store.search(collection, query_vector, top_k=hit_limit)
        except Exception:  # noqa: BLE001 - sqlite-vec is an optional rebuildable index.
            return None
        return {hit.document_id: hit.similarity for hit in hits}

    def _apply_conflict_post_write_actions(self, item: MemoryItem, conflicts: list[ConflictRecord]) -> MemoryItem:
        """Keep unsafe new memories out of active retrieval without auto-overwriting.

        - semantic_candidate records are review-only signals and do not mutate memory.
        - user/locked conflicts become `conflicted` and require explicit resolution.
        - lower-authority incoming facts that lose to existing authoritative memory are
          quarantined so they cannot pollute later retrieval.
        """

        actionable = [conflict for conflict in conflicts if conflict.detection_mode != "semantic_candidate"]
        if not actionable:
            return item
        for conflict in actionable:
            hint = conflict.metadata.get("resolution_hint") if isinstance(conflict.metadata, dict) else {}
            if not isinstance(hint, dict):
                continue
            if hint.get("action") == "needs_user_confirmation" and hint.get("rationale") == "locked_memory_involved":
                return self.store.update_status(item.memory_id, "conflicted", reason="conflict_with_locked_memory")
            if hint.get("action") != "prefer_higher_authority":
                continue
            if hint.get("loser_memory_id") == item.memory_id and hint.get("winner_memory_id"):
                return self.store.update_status(item.memory_id, "quarantined", reason=f"conflict_lost_authority:{conflict.conflict_id}")
        return item

    def profile(self) -> dict[str, Any]:
        return {
            "module": "SharedMemoryService",
            "database_path": str(self.store.db_path),
            "embedding_enabled": False,
            "embedding_cache_enabled": True,
            "embedding_cache": self.store.embedding_cache_stats(),
            "sqlite_vec_enabled": SqliteVectorStore.extension_available(),
            "sqlite_vec_path": str(self.store.root_dir / "shared_memory_vectors.sqlite3"),
            "retrieval_stage": "r1_metadata_bm25_sqlite_vec_or_persistent_dense_cache_r2_mmr_textrank_r3_raw_evidence",
            "embedding_fallback": "sqlite_vec_knn_when_available_else_persistent_embedding_id_with_deterministic_hash_vector",
            "conflict_detection": "structured_heuristic_and_semantic_candidate",
            "conflict_resolution": "needs_user_and_quarantine_without_auto_overwrite",
            "raw_evidence_expansion": "sqlite_raw_evidence_with_hash_verification",
        }


def _dedupe_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output
