from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.config import settings
from app.rag.reranker import RerankItem, clear_reranker_cache, rerank_texts


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class RerankerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = {
            "enabled": settings.rag_reranker_enabled,
            "base_url": settings.rag_reranker_api_base_url,
            "api_key": settings.rag_reranker_api_key,
            "model": settings.rag_reranker_model,
            "max_retries": settings.llm_request_max_retries,
        }
        settings.rag_reranker_enabled = True
        settings.rag_reranker_api_base_url = "https://openrouter.ai/api/v1"
        settings.rag_reranker_api_key = "test-reranker-key"
        settings.rag_reranker_model = "cohere/rerank-v3.5"
        settings.llm_request_max_retries = 0
        clear_reranker_cache()

    def tearDown(self) -> None:
        settings.rag_reranker_enabled = bool(self.original["enabled"])
        settings.rag_reranker_api_base_url = str(self.original["base_url"])
        settings.rag_reranker_api_key = str(self.original["api_key"])
        settings.rag_reranker_model = str(self.original["model"])
        settings.llm_request_max_retries = int(self.original["max_retries"])
        clear_reranker_cache()

    def test_openrouter_reranker_reorders_candidates(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request_obj, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = request_obj.full_url
            captured["headers"] = dict(request_obj.header_items())
            captured["body"] = json.loads(request_obj.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "model": "rerank-v3.5",
                    "results": [
                        {"index": 2, "relevance_score": 0.97},
                        {"index": 0, "relevance_score": 0.21},
                        {"index": 1, "relevance_score": 0.02},
                    ],
                }
            )

        with patch("app.rag.reranker.urllib_request.urlopen", side_effect=fake_urlopen):
            result = rerank_texts("Gibbs phase rule", ["copper", "trajectory", "phase equilibrium"])

        self.assertTrue(result.used_remote)
        self.assertEqual(result.backend, "openrouter")
        self.assertEqual([item.index for item in result.items], [2, 0, 1])
        self.assertEqual(result.items[0], RerankItem(index=2, relevance_score=0.97))
        self.assertEqual(captured["url"], "https://openrouter.ai/api/v1/rerank")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-reranker-key")
        self.assertEqual(captured["body"]["top_n"], 3)

    def test_remote_failure_preserves_hybrid_order(self) -> None:
        with patch("app.rag.reranker.urllib_request.urlopen", side_effect=OSError("offline")):
            result = rerank_texts("query", ["first", "second", "third"])

        self.assertFalse(result.used_remote)
        self.assertEqual(result.backend, "fallback")
        self.assertEqual([item.index for item in result.items], [0, 1, 2])

    def test_disabled_reranker_does_not_call_network(self) -> None:
        settings.rag_reranker_enabled = False
        with patch("app.rag.reranker.urllib_request.urlopen") as mocked:
            result = rerank_texts("query", ["first", "second"])

        mocked.assert_not_called()
        self.assertEqual(result.backend, "disabled")
        self.assertEqual([item.index for item in result.items], [0, 1])


if __name__ == "__main__":
    unittest.main()
