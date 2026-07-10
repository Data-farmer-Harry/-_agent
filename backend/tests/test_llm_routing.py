from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from app.config import settings
from app.core.llm import LLMClient
from app.core.llm_routing import LLMRoute, LLMRouter, LLMRoutingConfig, load_llm_routing_config


class _FakeResponse:
    def __init__(self, content: str = "ok") -> None:
        self.content = content

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": self.content}}]}).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class LLMRoutingTests(unittest.TestCase):
    def test_router_sends_simple_short_prompt_to_fast_tier(self) -> None:
        router = LLMRouter()

        decision = router.decide(
            system_prompt="Answer briefly in Chinese.",
            user_prompt="你好，请用一句话介绍这个系统。",
            max_tokens=300,
            temperature=0.1,
        )

        self.assertEqual(decision.tier, "fast")
        self.assertIn("simple_short_prompt", decision.reasons)

    def test_router_sends_lammps_repair_review_to_strong_tier(self) -> None:
        router = LLMRouter()

        decision = router.decide(
            system_prompt="You repair and review a structured LAMMPS request. Return JSON only.",
            user_prompt=(
                "LAMMPS Cu EAM NPT molecular dynamics failed with timestep instability. "
                "Use ADD/DELETE/MODIFY/VERIFY operations and explain physical consistency."
            ),
            max_tokens=1200,
            temperature=0.1,
            capability="lammps.review",
        )

        self.assertEqual(decision.tier, "strong")
        self.assertIn("lammps_or_md", decision.reasons)

    def test_router_sends_multimodal_prompt_to_vision_tier(self) -> None:
        router = LLMRouter()

        decision = router.decide(
            system_prompt="Analyze this image.",
            user_prompt="请识别这张相图截图的坐标轴和相区。",
            max_tokens=1000,
            temperature=0.1,
            multimodal=True,
        )

        self.assertEqual(decision.tier, "vision")
        self.assertIn("vision_or_multimodal", decision.reasons)

    def test_load_routing_config_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "llm_routing.json"
            config_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "fast_max_score": 20,
                        "balanced_max_score": 60,
                        "routes": {
                            "strong": {
                                "api_base_url": "https://openrouter.ai/api/v1",
                                "model": "provider/strong-model",
                                "timeout_seconds": 99,
                                "max_tokens": 3333,
                            }
                        },
                        "fallbacks": {"balanced": "strong", "strong": ""},
                        "capability_min_tiers": {"lammps": "strong"},
                    }
                ),
                encoding="utf-8",
            )

            config = load_llm_routing_config(config_path)

        self.assertEqual(config.fast_max_score, 20)
        self.assertEqual(config.balanced_max_score, 60)
        self.assertEqual(config.route_for("strong").model, "provider/strong-model")
        self.assertEqual(config.route_for("strong").timeout_seconds, 99)
        self.assertEqual(config.fallbacks["balanced"], "strong")
        self.assertEqual(config.fallbacks["strong"], "")
        self.assertEqual(config.capability_min_tiers["lammps"], "strong")

    def test_llm_client_uses_selected_route_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResponse("routed")

        previous = self._snapshot_settings()
        self._configure_llm_settings()
        try:
            router = LLMRouter(
                LLMRoutingConfig(
                    routes={
                        "fast": LLMRoute(
                            model="provider/fast-model",
                            api_base_url="https://fast.example/v1",
                            timeout_seconds=7,
                            max_tokens=50,
                        ),
                        "balanced": LLMRoute(model="provider/balanced-model"),
                    },
                    fallbacks={"fast": ""},
                )
            )
            client = LLMClient(router=router)
            with patch("app.core.llm.urllib_request.urlopen", side_effect=fake_urlopen):
                content = client.chat_text(system_prompt="Answer briefly.", user_prompt="你好", max_tokens=100)
        finally:
            self._restore_settings(previous)

        self.assertEqual(content, "routed")
        self.assertEqual(captured["url"], "https://fast.example/v1/chat/completions")
        self.assertEqual(captured["timeout"], 7)
        body = captured["body"]
        self.assertEqual(body["model"], "provider/fast-model")
        self.assertEqual(body["max_tokens"], 50)
        self.assertEqual(client.last_routing_decision["tier"], "fast")

    def test_llm_client_escalates_to_fallback_route_after_error(self) -> None:
        called_models: list[str] = []

        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            body = json.loads(req.data.decode("utf-8"))
            called_models.append(body["model"])
            if body["model"] == "provider/bad-fast-model":
                raise error.URLError("timed out")
            return _FakeResponse("fallback-ok")

        previous = self._snapshot_settings()
        self._configure_llm_settings()
        try:
            router = LLMRouter(
                LLMRoutingConfig(
                    max_escalations=1,
                    routes={
                        "fast": LLMRoute(model="provider/bad-fast-model", api_base_url="https://openrouter.ai/api/v1"),
                        "balanced": LLMRoute(model="provider/good-balanced-model", api_base_url="https://openrouter.ai/api/v1"),
                    },
                    fallbacks={"fast": "balanced", "balanced": ""},
                )
            )
            client = LLMClient(router=router)
            with patch("app.core.llm.urllib_request.urlopen", side_effect=fake_urlopen):
                content = client.chat_text(system_prompt="Answer briefly.", user_prompt="你好", max_tokens=100)
        finally:
            self._restore_settings(previous)

        self.assertEqual(content, "fallback-ok")
        self.assertEqual(called_models, ["provider/bad-fast-model", "provider/good-balanced-model"])
        self.assertEqual([entry["tier"] for entry in client.routing_history], ["fast", "balanced"])

    @staticmethod
    def _snapshot_settings() -> dict[str, object]:
        keys = {
            "llm_enabled",
            "llm_api_base_url",
            "llm_api_key",
            "llm_model",
            "llm_enable_thinking",
            "llm_request_timeout_seconds",
            "llm_request_max_retries",
            "llm_max_tokens",
        }
        return {key: getattr(settings, key) for key in keys}

    @staticmethod
    def _configure_llm_settings() -> None:
        settings.llm_enabled = True
        settings.llm_api_base_url = "https://default.example/v1"
        settings.llm_api_key = "test-key"
        settings.llm_model = "provider/default-model"
        settings.llm_enable_thinking = False
        settings.llm_request_timeout_seconds = 30
        settings.llm_request_max_retries = 0
        settings.llm_max_tokens = 4000

    @staticmethod
    def _restore_settings(snapshot: dict[str, object]) -> None:
        for key, value in snapshot.items():
            setattr(settings, key, value)


if __name__ == "__main__":
    unittest.main()
