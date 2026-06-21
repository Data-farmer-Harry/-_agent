from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.rag.query_rewrite import rewrite_thermo_query
from app.thermo.rag_index import _build_thermo_card_index_cached, build_thermo_card_index
from app.thermo.rag_retriever import search_thermo_cards
from app.thermo import rag_vector


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class ThermoRagVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_backend = settings.thermo_rag_embedding_backend
        self.original_model = settings.thermo_rag_embedding_model
        self.original_embedding_base_url = settings.thermo_rag_embedding_api_base_url
        self.original_embedding_api_key = settings.thermo_rag_embedding_api_key
        self.original_base_url = settings.llm_api_base_url
        self.original_api_key = settings.llm_api_key
        self.original_llm_enabled = settings.llm_enabled
        self.original_dimensions = settings.thermo_rag_embedding_dimensions
        self.original_batch_size = settings.thermo_rag_embedding_api_batch_size
        self.original_vector_store_path = settings.rag_vector_store_path
        self.rag_tmp = tempfile.TemporaryDirectory()
        settings.rag_vector_store_path = str(Path(self.rag_tmp.name) / "vectors.sqlite3")
        settings.thermo_rag_embedding_api_base_url = ""
        settings.thermo_rag_embedding_api_key = ""
        rag_vector._REMOTE_BACKEND_FAILURES.clear()
        _build_thermo_card_index_cached.cache_clear()

    def tearDown(self) -> None:
        settings.thermo_rag_embedding_backend = self.original_backend
        settings.thermo_rag_embedding_model = self.original_model
        settings.thermo_rag_embedding_api_base_url = self.original_embedding_base_url
        settings.thermo_rag_embedding_api_key = self.original_embedding_api_key
        settings.llm_api_base_url = self.original_base_url
        settings.llm_api_key = self.original_api_key
        settings.llm_enabled = self.original_llm_enabled
        settings.thermo_rag_embedding_dimensions = self.original_dimensions
        settings.thermo_rag_embedding_api_batch_size = self.original_batch_size
        settings.rag_vector_store_path = self.original_vector_store_path
        rag_vector._REMOTE_BACKEND_FAILURES.clear()
        _build_thermo_card_index_cached.cache_clear()
        self.rag_tmp.cleanup()

    def test_llm_api_embedding_uses_current_api_and_dashscope_embedding_base(self) -> None:
        settings.thermo_rag_embedding_backend = "llm_api"
        settings.thermo_rag_embedding_model = "text-embedding-v4"
        settings.llm_api_base_url = "https://coding.dashscope.aliyuncs.com/v1"
        settings.llm_api_key = "test-key"
        settings.llm_enabled = True
        settings.thermo_rag_embedding_dimensions = 4
        settings.thermo_rag_embedding_api_batch_size = 10

        captured: dict[str, object] = {}

        def fake_urlopen(request_obj, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = request_obj.full_url
            captured["headers"] = dict(request_obj.header_items())
            captured["timeout"] = timeout
            body = json.loads(request_obj.data.decode("utf-8"))
            captured["body"] = body
            return _FakeResponse(
                {
                    "data": [
                        {"embedding": [1.0, 0.0, 0.0, 0.0]},
                        {"embedding": [0.0, 1.0, 0.0, 0.0]},
                    ]
                }
            )

        with patch("app.thermo.rag_vector.urllib_request.urlopen", side_effect=fake_urlopen):
            vectors, backend = rag_vector.build_embeddings(["Al-Zn liquidus", "Pb-Sn eutectic"])

        self.assertEqual(backend, "llm_api")
        self.assertEqual(captured["url"], "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        self.assertEqual(captured["body"]["model"], "text-embedding-v4")
        self.assertEqual(captured["body"]["dimensions"], 4)
        self.assertEqual(captured["body"]["input"], ["Al-Zn liquidus", "Pb-Sn eutectic"])
        self.assertEqual(len(vectors), 2)
        self.assertAlmostEqual(vectors[0][0], 1.0)
        self.assertAlmostEqual(vectors[1][1], 1.0)

    def test_llm_api_embedding_falls_back_to_local_hash_after_failure(self) -> None:
        settings.thermo_rag_embedding_backend = "llm_api"
        settings.thermo_rag_embedding_model = "text-embedding-v4"
        settings.llm_api_base_url = "https://coding.dashscope.aliyuncs.com/v1"
        settings.llm_api_key = "test-key"
        settings.llm_enabled = True
        settings.thermo_rag_embedding_dimensions = 32

        with patch("app.thermo.rag_vector.urllib_request.urlopen", side_effect=RuntimeError("boom")):
            vectors, backend = rag_vector.build_embeddings(["Al-Zn liquidus"])

        self.assertEqual(backend, "local_hash")
        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(vectors[0]), 32)
        self.assertIn(rag_vector.embedding_signature("llm_api"), rag_vector._REMOTE_BACKEND_FAILURES)

    def test_index_and_query_share_local_hash_backend_after_remote_failure(self) -> None:
        settings.thermo_rag_embedding_backend = "llm_api"
        settings.thermo_rag_embedding_model = "text-embedding-v4"
        settings.llm_api_base_url = "https://coding.dashscope.aliyuncs.com/v1"
        settings.llm_api_key = "test-key"
        settings.llm_enabled = True

        with patch("app.thermo.rag_vector.urllib_request.urlopen", side_effect=RuntimeError("boom")):
            documents = build_thermo_card_index()
            candidates = search_thermo_cards("alzn phase boundary database", top_k=3)

        self.assertGreaterEqual(len(documents), 1)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(documents[0].embedding_backend, "local_hash")
        self.assertEqual(candidates[0].embedding_backend, "local_hash")

    def test_query_rewrite_adds_thermo_system_keys_for_chinese_query(self) -> None:
        settings.thermo_rag_embedding_backend = "local_hash"

        rewrite = rewrite_thermo_query("我想计算铝锌二元相图，并关注液相线。")
        candidates = search_thermo_cards("我想计算铝锌二元相图，并关注液相线。", top_k=3)

        self.assertIn("alzn", rewrite.normalized_system_keys)
        self.assertIn("Al-Zn", rewrite.expansion_terms)
        self.assertIn("binary phase diagram", rewrite.expansion_terms)
        self.assertEqual(candidates[0].card.system_name, "Al-Zn")
        self.assertIn("query_rewrite", candidates[0].match_reasons)

    def test_thermo_document_embeddings_are_reused_after_index_cache_reset(self) -> None:
        settings.thermo_rag_embedding_backend = "local_hash"
        settings.thermo_rag_embedding_dimensions = 64
        first = build_thermo_card_index()
        self.assertTrue(first)
        _build_thermo_card_index_cached.cache_clear()

        with patch("app.thermo.rag_index.build_embeddings", side_effect=AssertionError("embedding API should not rerun")):
            second = build_thermo_card_index()

        self.assertEqual(len(second), len(first))


if __name__ == "__main__":
    unittest.main()
