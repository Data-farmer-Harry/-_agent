from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator, Sequence

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - exercised by diagnostics on incomplete installs
    sqlite_vec = None  # type: ignore[assignment]

from app.config import settings


@dataclass(frozen=True)
class VectorCollectionStatus:
    collection: str
    table_name: str
    embedding_signature: str
    embedding_backend: str
    content_digest: str
    dimensions: int
    document_count: int
    updated_at: str


@dataclass(frozen=True)
class VectorSearchHit:
    document_id: str
    similarity: float
    distance: float


_STORE_LOCK = RLock()


def configured_vector_store_path() -> Path:
    configured = settings.rag_vector_store_path.strip()
    if not configured:
        return settings.tmp_dir / "rag" / "vector_store.sqlite3"
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = settings.tmp_dir.parent / path
    return path.resolve()


def content_digest(items: Iterable[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for document_id, text in items:
        digest.update(document_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class SqliteVectorStore:
    """Persistent sqlite-vec collections shared by the RAG retrievers."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or configured_vector_store_path()).resolve()

    @staticmethod
    def extension_available() -> bool:
        return sqlite_vec is not None

    @staticmethod
    def _table_name(collection: str) -> str:
        suffix = hashlib.sha1(collection.encode("utf-8")).hexdigest()[:16]
        return f"rag_vec_{suffix}"

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if sqlite_vec is None:
            raise RuntimeError("sqlite-vec is not installed; run pip install -r backend/requirements.txt")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        try:
            self._initialize(connection)
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS rag_vector_collections (
                collection TEXT PRIMARY KEY,
                table_name TEXT NOT NULL UNIQUE,
                embedding_signature TEXT NOT NULL,
                embedding_backend TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                document_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rag_vector_documents (
                collection TEXT NOT NULL,
                vector_rowid INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                PRIMARY KEY (collection, vector_rowid),
                UNIQUE (collection, document_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rag_vector_documents_id
                ON rag_vector_documents(collection, document_id);
            """
        )

    @staticmethod
    def _status_from_row(row: sqlite3.Row | None) -> VectorCollectionStatus | None:
        if row is None:
            return None
        return VectorCollectionStatus(
            collection=str(row["collection"]),
            table_name=str(row["table_name"]),
            embedding_signature=str(row["embedding_signature"]),
            embedding_backend=str(row["embedding_backend"]),
            content_digest=str(row["content_digest"]),
            dimensions=int(row["dimensions"]),
            document_count=int(row["document_count"]),
            updated_at=str(row["updated_at"]),
        )

    def collection_status(self, collection: str) -> VectorCollectionStatus | None:
        with _STORE_LOCK, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM rag_vector_collections WHERE collection = ?",
                (collection,),
            ).fetchone()
            return self._status_from_row(row)

    def collection_is_current(
        self,
        collection: str,
        *,
        embedding_signature: str,
        content_digest_value: str,
        document_ids: Sequence[str],
    ) -> bool:
        with _STORE_LOCK, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM rag_vector_collections WHERE collection = ?",
                (collection,),
            ).fetchone()
            status = self._status_from_row(row)
            if status is None:
                return False
            if (
                status.embedding_signature != embedding_signature
                or status.content_digest != content_digest_value
                or status.document_count != len(document_ids)
            ):
                return False
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (status.table_name,),
            ).fetchone()
            if table_exists is None:
                return False
            stored_ids = [
                str(item["document_id"])
                for item in connection.execute(
                    "SELECT document_id FROM rag_vector_documents WHERE collection = ? ORDER BY vector_rowid",
                    (collection,),
                ).fetchall()
            ]
            return stored_ids == list(document_ids)

    def replace_collection(
        self,
        collection: str,
        *,
        embedding_signature: str,
        embedding_backend: str,
        content_digest_value: str,
        documents: Sequence[tuple[str, Sequence[float]]],
    ) -> VectorCollectionStatus:
        if not documents:
            raise ValueError("sqlite-vec collection requires at least one document vector")
        document_ids = [document_id for document_id, _ in documents]
        if len(set(document_ids)) != len(document_ids):
            raise ValueError(f"duplicate document id in vector collection {collection}")
        dimensions = len(documents[0][1])
        if dimensions <= 0 or any(len(vector) != dimensions for _, vector in documents):
            raise ValueError(f"inconsistent or empty vectors for collection {collection}")

        table_name = self._table_name(collection)
        updated_at = datetime.now(timezone.utc).isoformat()
        with _STORE_LOCK, self._connection() as connection:
            previous = connection.execute(
                "SELECT table_name FROM rag_vector_collections WHERE collection = ?",
                (collection,),
            ).fetchone()
            connection.execute("BEGIN IMMEDIATE")
            try:
                if previous is not None and str(previous["table_name"]) != table_name:
                    old_table = str(previous["table_name"])
                    connection.execute(f'DROP TABLE IF EXISTS "{old_table}"')
                connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                connection.execute(
                    f'CREATE VIRTUAL TABLE "{table_name}" USING vec0('
                    f'embedding float[{dimensions}] distance_metric=cosine)'
                )
                connection.execute("DELETE FROM rag_vector_documents WHERE collection = ?", (collection,))
                connection.executemany(
                    "INSERT INTO rag_vector_documents(collection, vector_rowid, document_id) VALUES (?, ?, ?)",
                    ((collection, rowid, document_id) for rowid, document_id in enumerate(document_ids, start=1)),
                )
                connection.executemany(
                    f'INSERT INTO "{table_name}"(rowid, embedding) VALUES (?, ?)',
                    (
                        (rowid, sqlite_vec.serialize_float32(list(vector)))
                        for rowid, (_, vector) in enumerate(documents, start=1)
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO rag_vector_collections(
                        collection, table_name, embedding_signature, embedding_backend,
                        content_digest, dimensions, document_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection) DO UPDATE SET
                        table_name = excluded.table_name,
                        embedding_signature = excluded.embedding_signature,
                        embedding_backend = excluded.embedding_backend,
                        content_digest = excluded.content_digest,
                        dimensions = excluded.dimensions,
                        document_count = excluded.document_count,
                        updated_at = excluded.updated_at
                    """,
                    (
                        collection,
                        table_name,
                        embedding_signature,
                        embedding_backend,
                        content_digest_value,
                        dimensions,
                        len(documents),
                        updated_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return VectorCollectionStatus(
            collection=collection,
            table_name=table_name,
            embedding_signature=embedding_signature,
            embedding_backend=embedding_backend,
            content_digest=content_digest_value,
            dimensions=dimensions,
            document_count=len(documents),
            updated_at=updated_at,
        )

    def search(self, collection: str, query_vector: Sequence[float], *, top_k: int) -> tuple[VectorSearchHit, ...]:
        if not query_vector or top_k <= 0:
            return tuple()
        with _STORE_LOCK, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM rag_vector_collections WHERE collection = ?",
                (collection,),
            ).fetchone()
            status = self._status_from_row(row)
            if status is None or len(query_vector) != status.dimensions:
                return tuple()
            limit = min(max(1, top_k), status.document_count)
            vector_rows = connection.execute(
                f'SELECT rowid, distance FROM "{status.table_name}" '
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(list(query_vector)), limit),
            ).fetchall()
            id_map = {
                int(item["vector_rowid"]): str(item["document_id"])
                for item in connection.execute(
                    "SELECT vector_rowid, document_id FROM rag_vector_documents WHERE collection = ?",
                    (collection,),
                ).fetchall()
            }
        hits: list[VectorSearchHit] = []
        for item in vector_rows:
            rowid = int(item["rowid"])
            document_id = id_map.get(rowid)
            if document_id is None:
                continue
            distance = float(item["distance"])
            hits.append(
                VectorSearchHit(
                    document_id=document_id,
                    similarity=max(0.0, min(1.0, 1.0 - distance)),
                    distance=distance,
                )
            )
        return tuple(hits)

    def inventory(self) -> dict[str, object]:
        with _STORE_LOCK, self._connection() as connection:
            version = str(connection.execute("SELECT vec_version()").fetchone()[0])
            rows = connection.execute("SELECT * FROM rag_vector_collections ORDER BY collection").fetchall()
        collections = [self._status_from_row(row) for row in rows]
        return {
            "backend": "sqlite_vec",
            "database_path": str(self.path),
            "database_exists": self.path.exists(),
            "extension_version": version,
            "collections": [
                {
                    "collection": item.collection,
                    "embedding_signature": item.embedding_signature,
                    "embedding_backend": item.embedding_backend,
                    "dimensions": item.dimensions,
                    "document_count": item.document_count,
                    "updated_at": item.updated_at,
                }
                for item in collections
                if item is not None
            ],
        }


def get_vector_store() -> SqliteVectorStore:
    return SqliteVectorStore(configured_vector_store_path())
