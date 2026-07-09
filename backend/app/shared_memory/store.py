from __future__ import annotations

from contextlib import closing
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from typing import Any

from app.orchestration.fingerprint import stable_content_hash
from app.shared_memory.canonicalization import canonicalize_free_text, canonicalize_key_text, canonicalize_memory_payload
from app.shared_memory.models import (
    ConflictRecord,
    ConflictResolution,
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryWriteResult,
    RawEvidence,
    utc_now_iso,
)
from app.utils.path_utils import ensure_directory


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        import json

        return json.loads(value)
    except Exception:  # noqa: BLE001 - tolerate older/corrupt rows by falling back.
        return default


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_memory_text(item: MemoryItem) -> str:
    if item.normalized_text.strip():
        return canonicalize_free_text(item.normalized_text)
    return canonicalize_memory_payload(subject=item.subject, predicate=item.predicate, value=item.value, unit=item.unit)


def content_hash_for_item(item: MemoryItem) -> str:
    return stable_content_hash(
        {
            "scope_type": item.scope_type,
            "scope_id": item.scope_id,
            "item_type": item.item_type,
            "subject": item.subject,
            "predicate": item.predicate,
            "value": item.value,
            "unit": item.unit,
            "text": item.text,
            "polarity": item.polarity,
        }
    )


def normalized_hash_for_item(item: MemoryItem) -> str:
    return stable_content_hash(
        {
            "scope_type": item.scope_type,
            "scope_id": item.scope_id,
            "item_type": item.item_type,
            "subject": canonicalize_key_text(item.subject),
            "predicate": canonicalize_key_text(item.predicate),
            "normalized_text": normalize_memory_text(item),
            "polarity": item.polarity,
        }
    )


def raw_evidence_text_hash(text: str, *, mime_type: str = "text/plain") -> str:
    return stable_content_hash({"mime_type": mime_type or "text/plain", "content": text or ""})


def file_content_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def embedding_text_hash(*, backend: str, tokens: tuple[str, ...], dimensions: int) -> str:
    return stable_content_hash(
        {
            "schema_version": "shared-memory-embedding-text/v1",
            "backend": backend,
            "dimensions": dimensions,
            "tokens": list(tokens),
        }
    )


class SharedMemoryStore:
    """Additive SQLite store for run/conversation/user/global shared memory."""

    def __init__(self, root_dir: Path, *, db_path: Path | None = None) -> None:
        self.root_dir = ensure_directory(root_dir)
        self.db_path = db_path or (self.root_dir / "memory.sqlite3")
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        ensure_directory(self.db_path.parent)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_items (
                    memory_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    unit TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    normalized_text TEXT NOT NULL DEFAULT '',
                    polarity TEXT NOT NULL DEFAULT 'unknown',
                    status TEXT NOT NULL DEFAULT 'active',
                    authority TEXT NOT NULL DEFAULT 'llm_inference',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    normalized_hash TEXT NOT NULL DEFAULT '',
                    embedding_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    seen_count INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_sources (
                    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    authority TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(memory_id, source_ref),
                    FOREIGN KEY(memory_id) REFERENCES shared_memory_items(memory_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_versions (
                    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES shared_memory_items(memory_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_raw_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    path_or_url TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT 'text/plain',
                    excerpt TEXT NOT NULL DEFAULT '',
                    full_content_inline INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(memory_id, source_ref, content_hash),
                    FOREIGN KEY(memory_id) REFERENCES shared_memory_items(memory_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL DEFAULT 'shared-memory-embedding/v1',
                    backend TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    left_memory_id TEXT NOT NULL,
                    right_memory_id TEXT NOT NULL,
                    conflict_type TEXT NOT NULL,
                    detection_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    resolution_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_memory_resolutions (
                    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conflict_id TEXT NOT NULL,
                    resolver TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(conflict_id) REFERENCES shared_memory_conflicts(conflict_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_scope_status_type
                ON shared_memory_items(scope_type, scope_id, status, item_type)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_subject_predicate
                ON shared_memory_items(subject, predicate, status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_content_hash
                ON shared_memory_items(content_hash)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_normalized_hash
                ON shared_memory_items(normalized_hash)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_conflict_status
                ON shared_memory_conflicts(status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_raw_memory
                ON shared_memory_raw_evidence(memory_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_raw_content_hash
                ON shared_memory_raw_evidence(content_hash)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shared_memory_embeddings_backend_hash
                ON shared_memory_embeddings(backend, text_hash, dimensions)
                """
            )
            connection.commit()

    def write_item(self, item: MemoryItem) -> MemoryWriteResult:
        prepared = self._prepare_item(item)
        with closing(self._connect()) as connection:
            exact = self._find_duplicate(connection, prepared, hash_field="content_hash")
            if exact is not None:
                return self._merge_duplicate(connection, existing=exact, incoming=prepared, dedup_level="exact")
            normalized = self._find_duplicate(connection, prepared, hash_field="normalized_hash")
            if normalized is not None:
                return self._merge_duplicate(connection, existing=normalized, incoming=prepared, dedup_level="normalized")
            self._insert_item(connection, prepared)
            connection.commit()
        return MemoryWriteResult(item=prepared, created=True)

    def get_item(self, memory_id: str) -> MemoryItem | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM shared_memory_items WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._item_from_row(row) if row else None

    def write_raw_evidence(self, evidence: RawEvidence) -> RawEvidence:
        with closing(self._connect()) as connection:
            prepared = self._prepare_raw_evidence(evidence)
            self._insert_raw_evidence(connection, prepared)
            connection.commit()
            row = self._raw_evidence_row_by_id(connection, prepared.evidence_id)
            if row is None:
                row = self._raw_evidence_row_by_unique(connection, prepared)
        return self._raw_evidence_from_row(row) if row is not None else prepared

    def expand_raw_evidence(self, identifiers: list[str]) -> list[RawEvidence]:
        seen: set[str] = set()
        rows: list[sqlite3.Row] = []
        with closing(self._connect()) as connection:
            for raw_identifier in identifiers:
                identifier = str(raw_identifier or "").strip()
                if not identifier:
                    continue
                matched = connection.execute(
                    """
                    SELECT *
                    FROM shared_memory_raw_evidence
                    WHERE evidence_id = ? OR memory_id = ?
                    ORDER BY created_at ASC, evidence_id ASC
                    """,
                    (identifier, identifier),
                ).fetchall()
                if not matched and identifier.startswith("mem-"):
                    item = self.get_item(identifier)
                    if item is not None:
                        for evidence in self._raw_evidence_for_item(item):
                            self._insert_raw_evidence(connection, evidence)
                    matched = connection.execute(
                        """
                        SELECT *
                        FROM shared_memory_raw_evidence
                        WHERE memory_id = ?
                        ORDER BY created_at ASC, evidence_id ASC
                        """,
                        (identifier,),
                    ).fetchall()
                for row in matched:
                    if row["evidence_id"] in seen:
                        continue
                    seen.add(row["evidence_id"])
                    rows.append(row)
            connection.commit()
        return [self._verify_raw_evidence(self._raw_evidence_from_row(row)) for row in rows]

    def list_items(
        self,
        *,
        scope: MemoryScope,
        item_types: list[str] | None = None,
        statuses: list[MemoryStatus] | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        scope_keys = scope.visible_scope_keys()
        statuses = statuses or ["active"]
        clauses = []
        params: list[Any] = []
        for scope_type, scope_id in scope_keys:
            clauses.append("(scope_type = ? AND scope_id = ?)")
            params.extend([scope_type, scope_id])
        where = f"({' OR '.join(clauses)})"
        placeholders = ",".join("?" for _ in statuses)
        where += f" AND status IN ({placeholders})"
        params.extend(statuses)
        if item_types:
            type_placeholders = ",".join("?" for _ in item_types)
            where += f" AND item_type IN ({type_placeholders})"
            params.extend(item_types)
        params.append(max(1, min(limit, 1000)))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM shared_memory_items
                WHERE {where}
                ORDER BY updated_at DESC, memory_id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def update_status(self, memory_id: str, status: MemoryStatus, *, reason: str = "") -> MemoryItem:
        now = utc_now_iso()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM shared_memory_items WHERE memory_id = ?", (memory_id,)).fetchone()
            if row is None:
                raise KeyError(f"Shared memory item not found: {memory_id}")
            item = self._item_from_row(row)
            updated = item.model_copy(update={"status": status, "updated_at": now})
            connection.execute(
                """
                UPDATE shared_memory_items
                SET status = ?, updated_at = ?
                WHERE memory_id = ?
                """,
                (status, now, memory_id),
            )
            self._append_version(connection, updated, event_type="status_change", reason=reason)
            connection.commit()
        return updated

    def record_conflict(self, conflict: ConflictRecord) -> ConflictRecord:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO shared_memory_conflicts (
                    conflict_id,
                    left_memory_id,
                    right_memory_id,
                    conflict_type,
                    detection_mode,
                    status,
                    evidence_refs_json,
                    resolution_json,
                    created_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.conflict_id,
                    conflict.left_memory_id,
                    conflict.right_memory_id,
                    conflict.conflict_type,
                    conflict.detection_mode,
                    conflict.status,
                    _json_dumps(conflict.evidence_refs),
                    _json_dumps(conflict.resolution) if conflict.resolution is not None else None,
                    conflict.created_at,
                    conflict.updated_at,
                    _json_dumps(conflict.metadata),
                ),
            )
            connection.commit()
        return conflict

    def resolve_conflict(self, conflict_id: str, resolution: ConflictResolution) -> ConflictRecord:
        now = utc_now_iso()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM shared_memory_conflicts WHERE conflict_id = ?", (conflict_id,)).fetchone()
            if row is None:
                raise KeyError(f"Shared memory conflict not found: {conflict_id}")
            resolution_payload = resolution.model_dump(mode="json")
            connection.execute(
                """
                UPDATE shared_memory_conflicts
                SET status = 'resolved', resolution_json = ?, updated_at = ?
                WHERE conflict_id = ?
                """,
                (_json_dumps(resolution_payload), now, conflict_id),
            )
            connection.execute(
                """
                INSERT INTO shared_memory_resolutions (
                    conflict_id, resolver, decision, reason, evidence_refs_json, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict_id,
                    resolution.resolver,
                    resolution.decision,
                    resolution.reason,
                    _json_dumps(resolution.evidence_refs),
                    resolution.resolved_at,
                    _json_dumps(resolution.metadata),
                ),
            )
            connection.commit()
        resolved = self.get_conflict(conflict_id)
        if resolved is None:
            raise KeyError(f"Shared memory conflict not found after resolving: {conflict_id}")
        return resolved

    def get_conflict(self, conflict_id: str) -> ConflictRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM shared_memory_conflicts WHERE conflict_id = ?", (conflict_id,)).fetchone()
        return self._conflict_from_row(row) if row else None

    def version_history(self, memory_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT version, event_type, item_json, reason, created_at
                FROM shared_memory_versions
                WHERE memory_id = ?
                ORDER BY version ASC
                """,
                (memory_id,),
            ).fetchall()
        return [
            {
                "version": row["version"],
                "event_type": row["event_type"],
                "item": _json_loads(row["item_json"], {}),
                "reason": row["reason"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_or_create_embedding_vector(
        self,
        *,
        embedding_id: str,
        tokens: tuple[str, ...],
        vector_factory: Callable[[tuple[str, ...]], tuple[float, ...]],
        backend: str = "deterministic_hash_v1",
        dimensions: int = 96,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a persisted embedding vector and reuse it across retrievals.

        The current backend is deterministic and dependency-free. The schema is
        intentionally shaped like a production embedding cache, so a future
        sqlite-vec/API-backed implementation can reuse the same stable IDs,
        text hashes and use-count telemetry.
        """

        normalized_tokens = tuple(str(token or "").strip() for token in tokens if str(token or "").strip())
        text_hash = embedding_text_hash(backend=backend, tokens=normalized_tokens, dimensions=dimensions)
        resolved_embedding_id = embedding_id.strip() or f"emb-{text_hash[:16]}"
        now = utc_now_iso()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM shared_memory_embeddings
                WHERE embedding_id = ?
                  AND backend = ?
                  AND text_hash = ?
                  AND dimensions = ?
                """,
                (resolved_embedding_id, backend, text_hash, dimensions),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    UPDATE shared_memory_embeddings
                    SET updated_at = ?, use_count = use_count + 1
                    WHERE embedding_id = ?
                    """,
                    (now, resolved_embedding_id),
                )
                connection.commit()
                vector = tuple(float(value) for value in _json_loads(row["vector_json"], []))
                return {
                    "embedding_id": resolved_embedding_id,
                    "backend": backend,
                    "text_hash": text_hash,
                    "dimensions": dimensions,
                    "vector": vector,
                    "cache_hit": True,
                    "use_count": int(row["use_count"] or 0) + 1,
                }
            vector = vector_factory(normalized_tokens)
            connection.execute(
                """
                INSERT OR REPLACE INTO shared_memory_embeddings (
                    embedding_id,
                    schema_version,
                    backend,
                    text_hash,
                    dimensions,
                    vector_json,
                    created_at,
                    updated_at,
                    use_count,
                    metadata_json
                )
                VALUES (?, 'shared-memory-embedding/v1', ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    resolved_embedding_id,
                    backend,
                    text_hash,
                    dimensions,
                    _json_dumps(list(vector)),
                    now,
                    now,
                    _json_dumps(metadata or {}),
                ),
            )
            connection.commit()
        return {
            "embedding_id": resolved_embedding_id,
            "backend": backend,
            "text_hash": text_hash,
            "dimensions": dimensions,
            "vector": vector,
            "cache_hit": False,
            "use_count": 1,
        }

    def embedding_cache_stats(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT backend, COUNT(*) AS row_count, COALESCE(SUM(use_count), 0) AS use_count
                FROM shared_memory_embeddings
                GROUP BY backend
                ORDER BY backend ASC
                """
            ).fetchall()
        return {
            "row_count": sum(int(row["row_count"] or 0) for row in rows),
            "total_use_count": sum(int(row["use_count"] or 0) for row in rows),
            "backends": {
                row["backend"]: {
                    "row_count": int(row["row_count"] or 0),
                    "use_count": int(row["use_count"] or 0),
                }
                for row in rows
            },
        }

    def _prepare_item(self, item: MemoryItem) -> MemoryItem:
        now = utc_now_iso()
        source_refs = _dedupe_source_refs(item.source_refs)
        if not source_refs:
            raise ValueError("Shared memory item requires at least one source_ref")
        normalized_text = normalize_memory_text(item)
        metadata = dict(item.metadata)
        if item.text.strip():
            existing_raw_ids = [str(value) for value in metadata.get("raw_evidence_ids", [])] if isinstance(metadata.get("raw_evidence_ids"), list) else []
            raw_ids = [*_dedupe_source_refs(existing_raw_ids), *[_raw_evidence_id_for(item.memory_id, source_ref) for source_ref in source_refs]]
            metadata["raw_evidence_ids"] = _dedupe_source_refs(raw_ids)
        prepared = item.model_copy(
            update={
                "normalized_text": normalized_text,
                "content_hash": item.content_hash or content_hash_for_item(item),
                "normalized_hash": item.normalized_hash or normalized_hash_for_item(item),
                "embedding_id": item.embedding_id
                or f"emb-{stable_content_hash({'schema_version': 'shared-memory-embedding-id/v1', 'normalized_text': normalized_text})[:16]}",
                "updated_at": item.updated_at or now,
                "created_at": item.created_at or now,
                "source_refs": source_refs,
                "metadata": metadata,
            }
        )
        return prepared

    def _find_duplicate(self, connection: sqlite3.Connection, item: MemoryItem, *, hash_field: str) -> sqlite3.Row | None:
        return connection.execute(
            f"""
            SELECT *
            FROM shared_memory_items
            WHERE scope_type = ?
              AND scope_id = ?
              AND item_type = ?
              AND status = 'active'
              AND {hash_field} = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (item.scope_type, item.scope_id, item.item_type, getattr(item, hash_field)),
        ).fetchone()

    def _insert_item(self, connection: sqlite3.Connection, item: MemoryItem) -> None:
        connection.execute(
            """
            INSERT INTO shared_memory_items (
                memory_id,
                schema_version,
                scope_type,
                scope_id,
                item_type,
                subject,
                predicate,
                value_json,
                unit,
                text,
                normalized_text,
                polarity,
                status,
                authority,
                confidence,
                content_hash,
                normalized_hash,
                embedding_id,
                created_at,
                updated_at,
                expires_at,
                metadata_json,
                last_seen_at,
                seen_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.memory_id,
                item.schema_version,
                item.scope_type,
                item.scope_id,
                item.item_type,
                item.subject,
                item.predicate,
                _json_dumps(item.value),
                item.unit,
                item.text,
                item.normalized_text,
                item.polarity,
                item.status,
                item.authority,
                item.confidence,
                item.content_hash,
                item.normalized_hash,
                item.embedding_id,
                item.created_at,
                item.updated_at,
                item.expires_at,
                _json_dumps(item.metadata),
                item.updated_at,
                1,
            ),
        )
        for source_ref in item.source_refs:
            self._insert_source(connection, item, source_ref)
        for raw_evidence in self._raw_evidence_for_item(item):
            self._insert_raw_evidence(connection, raw_evidence)
        self._append_version(connection, item, event_type="insert", reason="new_memory_item")

    def _merge_duplicate(
        self,
        connection: sqlite3.Connection,
        *,
        existing: sqlite3.Row,
        incoming: MemoryItem,
        dedup_level: str,
    ) -> MemoryWriteResult:
        existing_item = self._item_from_row(existing)
        merged_sources = list(existing_item.source_refs)
        for source_ref in incoming.source_refs:
            if source_ref not in merged_sources:
                merged_sources.append(source_ref)
        now = utc_now_iso()
        merged_metadata = {**existing_item.metadata, "last_dedup_level": dedup_level}
        if existing_item.text.strip():
            existing_raw_ids = [str(value) for value in merged_metadata.get("raw_evidence_ids", [])] if isinstance(merged_metadata.get("raw_evidence_ids"), list) else []
            merged_metadata["raw_evidence_ids"] = _dedupe_source_refs(
                [*existing_raw_ids, *[_raw_evidence_id_for(existing_item.memory_id, source_ref) for source_ref in merged_sources]]
            )
        merged_item = existing_item.model_copy(
            update={
                "updated_at": now,
                "source_refs": merged_sources,
                "metadata": merged_metadata,
            }
        )
        connection.execute(
            """
            UPDATE shared_memory_items
            SET updated_at = ?,
                last_seen_at = ?,
                seen_count = seen_count + 1,
                metadata_json = ?
            WHERE memory_id = ?
            """,
            (
                now,
                now,
                _json_dumps(merged_metadata),
                existing_item.memory_id,
            ),
        )
        for source_ref in merged_sources:
            self._insert_source(connection, existing_item, source_ref)
        for raw_evidence in self._raw_evidence_for_item(merged_item):
            self._insert_raw_evidence(connection, raw_evidence)
        self._append_version(connection, merged_item, event_type=f"dedup_{dedup_level}", reason=f"merged duplicate {incoming.memory_id}")
        connection.commit()
        return MemoryWriteResult(
            item=merged_item,
            created=False,
            deduplicated=True,
            dedup_level=dedup_level,
            duplicate_of=existing_item.memory_id,
            merged_source_refs=merged_sources,
        )

    def _insert_source(self, connection: sqlite3.Connection, item: MemoryItem, source_ref: str) -> None:
        normalized = source_ref.strip()
        if not normalized:
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO shared_memory_sources (
                memory_id, source_ref, authority, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (item.memory_id, normalized, item.authority, utc_now_iso(), "{}"),
        )

    def _prepare_raw_evidence(self, evidence: RawEvidence) -> RawEvidence:
        metadata = dict(evidence.metadata or {})
        path_or_url = str(evidence.path_or_url or "").strip()
        excerpt = str(evidence.excerpt or "")
        content_hash = evidence.content_hash.strip()
        hash_source = str(metadata.get("hash_source") or "")
        if not content_hash and path_or_url and _is_local_path(path_or_url) and Path(path_or_url).exists():
            content_hash = file_content_hash(Path(path_or_url))
            hash_source = "local_file_bytes"
        if not content_hash and excerpt:
            content_hash = raw_evidence_text_hash(excerpt, mime_type=evidence.mime_type)
            hash_source = "inline_excerpt"
        if hash_source:
            metadata["hash_source"] = hash_source
        return evidence.model_copy(
            update={
                "evidence_id": evidence.evidence_id.strip() or _raw_evidence_id_for(evidence.memory_id, evidence.source_ref),
                "memory_id": evidence.memory_id.strip(),
                "source_type": evidence.source_type.strip() or "memory_item",
                "source_ref": evidence.source_ref.strip(),
                "path_or_url": path_or_url,
                "content_hash": content_hash,
                "mime_type": evidence.mime_type.strip() or "text/plain",
                "excerpt": excerpt,
                "created_at": evidence.created_at or utc_now_iso(),
                "metadata": metadata,
            }
        )

    def _insert_raw_evidence(self, connection: sqlite3.Connection, evidence: RawEvidence) -> None:
        prepared = self._prepare_raw_evidence(evidence)
        if not prepared.memory_id or not prepared.source_ref or not (prepared.excerpt or prepared.path_or_url):
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO shared_memory_raw_evidence (
                evidence_id,
                memory_id,
                source_type,
                source_ref,
                path_or_url,
                content_hash,
                mime_type,
                excerpt,
                full_content_inline,
                created_at,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared.evidence_id,
                prepared.memory_id,
                prepared.source_type,
                prepared.source_ref,
                prepared.path_or_url,
                prepared.content_hash,
                prepared.mime_type,
                prepared.excerpt,
                1 if prepared.full_content_inline else 0,
                prepared.created_at,
                _json_dumps(prepared.metadata),
            ),
        )

    def _raw_evidence_for_item(self, item: MemoryItem) -> list[RawEvidence]:
        if not item.text.strip():
            return []
        evidence: list[RawEvidence] = []
        source_refs = item.source_refs or [f"memory:{item.memory_id}"]
        for source_ref in source_refs:
            path_or_url = _path_or_url_from_source_ref(source_ref, item.metadata)
            excerpt = item.text[:8000]
            evidence.append(
                RawEvidence(
                    evidence_id=_raw_evidence_id_for(item.memory_id, source_ref),
                    memory_id=item.memory_id,
                    source_type=_source_type_for_item(item),
                    source_ref=source_ref,
                    path_or_url=path_or_url,
                    mime_type=str(item.metadata.get("mime_type") or "text/plain"),
                    excerpt=excerpt,
                    full_content_inline=bool(excerpt and len(item.text) <= 8000),
                    metadata={
                        "memory_item_content_hash": item.content_hash,
                        "memory_item_normalized_hash": item.normalized_hash,
                        "item_type": item.item_type,
                        "authority": item.authority,
                        "subject": item.subject,
                        "predicate": item.predicate,
                    },
                )
            )
        return evidence

    def _raw_evidence_row_by_id(self, connection: sqlite3.Connection, evidence_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM shared_memory_raw_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()

    def _raw_evidence_row_by_unique(self, connection: sqlite3.Connection, evidence: RawEvidence) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM shared_memory_raw_evidence
            WHERE memory_id = ?
              AND source_ref = ?
              AND content_hash = ?
            ORDER BY created_at ASC, evidence_id ASC
            LIMIT 1
            """,
            (evidence.memory_id, evidence.source_ref, evidence.content_hash),
        ).fetchone()

    def _raw_evidence_from_row(self, row: sqlite3.Row) -> RawEvidence:
        return RawEvidence(
            evidence_id=row["evidence_id"],
            memory_id=row["memory_id"],
            source_type=row["source_type"],
            source_ref=row["source_ref"],
            path_or_url=row["path_or_url"],
            content_hash=row["content_hash"],
            mime_type=row["mime_type"],
            excerpt=row["excerpt"],
            full_content_inline=bool(row["full_content_inline"]),
            created_at=row["created_at"],
            metadata=_json_loads(row["metadata_json"], {}),
        )

    def _verify_raw_evidence(self, evidence: RawEvidence) -> RawEvidence:
        metadata = dict(evidence.metadata or {})
        expected = evidence.content_hash.strip()
        verified: bool | None = None
        error_message = ""
        actual = ""
        if not expected:
            error_message = "missing_content_hash"
        elif evidence.path_or_url and _is_local_path(evidence.path_or_url):
            path = Path(evidence.path_or_url)
            if not path.exists():
                error_message = "local_source_missing"
                verified = False
            else:
                actual = file_content_hash(path)
                verified = actual == expected
                if not verified:
                    error_message = "content_hash_mismatch"
        elif evidence.full_content_inline or evidence.excerpt:
            actual = raw_evidence_text_hash(evidence.excerpt, mime_type=evidence.mime_type)
            verified = actual == expected
            if not verified:
                error_message = "content_hash_mismatch"
        elif evidence.path_or_url.startswith(("http://", "https://")):
            verified = False
            error_message = "remote_source_not_fetched"
        else:
            error_message = "no_verifiable_content"
        metadata["hash_verified"] = verified
        if actual:
            metadata["actual_content_hash"] = actual
        if error_message:
            metadata["verification_error"] = error_message
        return evidence.model_copy(
            update={
                "hash_verified": verified,
                "verification_error": error_message,
                "metadata": metadata,
            }
        )

    def _append_version(self, connection: sqlite3.Connection, item: MemoryItem, *, event_type: str, reason: str) -> None:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM shared_memory_versions WHERE memory_id = ?",
            (item.memory_id,),
        ).fetchone()
        version = int(row["version"] or 0) + 1
        connection.execute(
            """
            INSERT INTO shared_memory_versions (
                memory_id, version, event_type, item_json, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item.memory_id, version, event_type, item.model_dump_json(), reason, utc_now_iso()),
        )

    def _item_from_row(self, row: sqlite3.Row) -> MemoryItem:
        source_refs = self._source_refs_for(row["memory_id"])
        return MemoryItem(
            memory_id=row["memory_id"],
            schema_version=row["schema_version"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            item_type=row["item_type"],
            subject=row["subject"],
            predicate=row["predicate"],
            value=_json_loads(row["value_json"], None),
            unit=row["unit"],
            text=row["text"],
            normalized_text=row["normalized_text"],
            polarity=row["polarity"],
            status=row["status"],
            authority=row["authority"],
            confidence=float(row["confidence"]),
            source_refs=source_refs,
            content_hash=row["content_hash"],
            normalized_hash=row["normalized_hash"],
            embedding_id=row["embedding_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            metadata=_json_loads(row["metadata_json"], {}),
        )

    def _source_refs_for(self, memory_id: str) -> list[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT source_ref
                FROM shared_memory_sources
                WHERE memory_id = ?
                ORDER BY source_id ASC
                """,
                (memory_id,),
            ).fetchall()
        return [row["source_ref"] for row in rows]

    def _conflict_from_row(self, row: sqlite3.Row) -> ConflictRecord:
        return ConflictRecord(
            conflict_id=row["conflict_id"],
            left_memory_id=row["left_memory_id"],
            right_memory_id=row["right_memory_id"],
            conflict_type=row["conflict_type"],
            detection_mode=row["detection_mode"],
            status=row["status"],
            evidence_refs=_json_loads(row["evidence_refs_json"], []),
            resolution=_json_loads(row["resolution_json"], None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_json_loads(row["metadata_json"], {}),
        )


def _dedupe_source_refs(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _raw_evidence_id_for(memory_id: str, source_ref: str) -> str:
    return f"ev-{stable_content_hash({'memory_id': memory_id, 'source_ref': source_ref})[:16]}"


def _source_type_for_item(item: MemoryItem) -> str:
    if item.item_type == "evidence" and item.authority == "rag":
        return "rag_document"
    if item.authority == "user":
        return "user_message"
    if item.authority == "execution":
        return "execution_fact"
    if item.authority == "registry":
        return "registry"
    if item.item_type == "finding":
        return "finding"
    return "memory_item"


def _path_or_url_from_source_ref(source_ref: str, metadata: dict[str, Any]) -> str:
    for key in ("path_or_url", "path", "url", "source_url", "artifact_path"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    normalized = source_ref.strip()
    if normalized.startswith(("http://", "https://")):
        return normalized
    if _is_local_path(normalized):
        return normalized
    return ""


def _is_local_path(value: str) -> bool:
    if not value or value.startswith(("http://", "https://")):
        return False
    return value.startswith(("/", "./", "../")) or Path(value).anchor != ""
