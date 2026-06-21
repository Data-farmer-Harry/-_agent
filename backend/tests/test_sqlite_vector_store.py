from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.rag.sqlite_vector_store import SqliteVectorStore, content_digest


class SqliteVectorStoreTests(unittest.TestCase):
    def test_sqlite_vec_persists_collection_and_returns_cosine_neighbors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vectors.sqlite3"
            store = SqliteVectorStore(path)
            documents = [
                ("al-zn", (1.0, 0.0, 0.0)),
                ("pb-sn", (0.0, 1.0, 0.0)),
                ("fe-c", (-1.0, 0.0, 0.0)),
            ]
            digest = content_digest((document_id, document_id) for document_id, _ in documents)

            status = store.replace_collection(
                "thermo_test",
                embedding_signature="local_hash:3",
                embedding_backend="local_hash",
                content_digest_value=digest,
                documents=documents,
            )
            hits = SqliteVectorStore(path).search("thermo_test", (1.0, 0.0, 0.0), top_k=3)
            inventory = SqliteVectorStore(path).inventory()

        self.assertEqual(status.dimensions, 3)
        self.assertEqual(status.document_count, 3)
        self.assertEqual(hits[0].document_id, "al-zn")
        self.assertAlmostEqual(hits[0].similarity, 1.0, places=5)
        self.assertEqual(hits[1].document_id, "pb-sn")
        self.assertEqual(inventory["backend"], "sqlite_vec")
        self.assertEqual(inventory["extension_version"], "v0.1.9")
        self.assertEqual(inventory["collections"][0]["document_count"], 3)

    def test_collection_fingerprint_controls_persistent_index_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vectors.sqlite3"
            store = SqliteVectorStore(path)
            document_ids = ["a", "b"]
            digest = content_digest((("a", "alpha"), ("b", "beta")))
            store.replace_collection(
                "materials_test",
                embedding_signature="model:v1",
                embedding_backend="llm_api",
                content_digest_value=digest,
                documents=[("a", (1.0, 0.0)), ("b", (0.0, 1.0))],
            )

            self.assertTrue(
                SqliteVectorStore(path).collection_is_current(
                    "materials_test",
                    embedding_signature="model:v1",
                    content_digest_value=digest,
                    document_ids=document_ids,
                )
            )
            self.assertFalse(
                store.collection_is_current(
                    "materials_test",
                    embedding_signature="model:v2",
                    content_digest_value=digest,
                    document_ids=document_ids,
                )
            )
            self.assertFalse(
                store.collection_is_current(
                    "materials_test",
                    embedding_signature="model:v1",
                    content_digest_value="changed",
                    document_ids=document_ids,
                )
            )


if __name__ == "__main__":
    unittest.main()
