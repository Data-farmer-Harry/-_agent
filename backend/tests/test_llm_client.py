from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.core.llm import LLMClient
from app.config import settings


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class LLMClientTests(unittest.TestCase):
    def test_dashscope_requests_disable_thinking_when_configured(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        previous = {
            "llm_enabled": settings.llm_enabled,
            "llm_api_base_url": settings.llm_api_base_url,
            "llm_api_key": settings.llm_api_key,
            "llm_model": settings.llm_model,
            "llm_enable_thinking": settings.llm_enable_thinking,
            "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
            "llm_request_max_retries": settings.llm_request_max_retries,
            "llm_max_tokens": settings.llm_max_tokens,
        }
        settings.llm_enabled = True
        settings.llm_api_base_url = "https://coding.dashscope.aliyuncs.com/v1"
        settings.llm_api_key = "test-key"
        settings.llm_model = "qwen3.5-plus"
        settings.llm_enable_thinking = False
        settings.llm_request_timeout_seconds = 12
        settings.llm_request_max_retries = 0
        settings.llm_max_tokens = 2048
        try:
            client = LLMClient()
            with patch("app.core.llm.urllib_request.urlopen", side_effect=fake_urlopen):
                content = client.chat_text(system_prompt="sys", user_prompt="hello", max_tokens=100)
        finally:
            for key, value in previous.items():
                setattr(settings, key, value)

        self.assertEqual(content, "ok")
        self.assertEqual(captured["url"], "https://coding.dashscope.aliyuncs.com/v1/chat/completions")
        body = captured["body"]
        self.assertEqual(body["model"], "qwen3.5-plus")
        self.assertEqual(body["max_tokens"], 100)
        self.assertIn("enable_thinking", body)
        self.assertFalse(body["enable_thinking"])


if __name__ == "__main__":
    unittest.main()
