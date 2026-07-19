from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from app.config import settings
from app.core.llm import LLMClient, llm_call_context
from app.core.llm_capabilities import ModelCapability
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
        self.assertTrue(decision.policy_metadata["model_capability_check"]["compatible"])

    def test_strict_model_capability_check_rejects_incompatible_route(self) -> None:
        router = LLMRouter(
            LLMRoutingConfig(
                strict_model_capabilities=True,
                routes={
                    "vision": LLMRoute(
                        model="text-only-model",
                        capabilities=(ModelCapability.TEXT.value, ModelCapability.STRUCTURED_OUTPUT.value),
                    )
                },
                fallbacks={"vision": ""},
            )
        )
        decision = router.decide(
            system_prompt="Analyze image.",
            user_prompt="识别上传图片。",
            max_tokens=900,
            temperature=0.1,
            capability="vision.recognition",
            multimodal=True,
        )

        self.assertFalse(decision.policy_metadata["model_capability_check"]["compatible"])
        self.assertIn("vision", decision.policy_metadata["model_capability_check"]["missing"])
        with self.assertRaisesRegex(RuntimeError, "does not declare required capabilities"):
            router.require_model_compatibility(decision)

    def test_router_does_not_infer_vision_from_generic_system_capability_text(self) -> None:
        router = LLMRouter()

        decision = router.decide(
            system_prompt="The system can analyze image, vision, recognition, and multimodal inputs when supplied.",
            user_prompt="你好，请用一句话介绍这个系统。当前没有上传图片。",
            max_tokens=300,
            temperature=0.1,
            capability="chat",
            multimodal=False,
        )

        self.assertEqual(decision.tier, "fast")
        self.assertNotIn("vision_or_multimodal", decision.reasons)

    def test_router_ignores_structured_context_headings_for_simple_chat_task(self) -> None:
        router = LLMRouter()
        wrapped_prompt = (
            "User message:\n它和包晶反应有什么区别？\n\n"
            "Current summary:\nPrevious RAG benchmark and code repair discussion.\n\n"
            "Retrieved long-term memory:\nLAMMPS traceback, query rewrite, citation, JSON.\n\n"
            "Tool results from this turn:\n(none)\n\n"
            "Conversation history:\n[]"
        )

        decision = router.decide(
            system_prompt="Answer clearly in Chinese and respect memory/tool boundaries.",
            user_prompt=wrapped_prompt,
            max_tokens=700,
            temperature=0.2,
            capability="chat",
        )

        self.assertEqual(decision.tier, "fast")
        self.assertNotIn("code_or_repair", decision.reasons)
        self.assertNotIn("research_or_evaluation", decision.reasons)

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
                                "capabilities": ["text", "structured_output", "code", "reasoning"],
                                "capabilities_verified": True,
                                "context_window_tokens": 128000,
                                "latency_class": "high",
                                "cost_class": "high",
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
        self.assertIn("code", config.route_for("strong").capabilities)
        self.assertTrue(config.route_for("strong").capabilities_verified)
        self.assertEqual(config.route_for("strong").context_window_tokens, 128000)
        self.assertEqual(config.route_for("strong").latency_class, "high")
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

    def test_llm_client_records_safe_routing_telemetry(self) -> None:
        def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            _ = req, timeout
            return _FakeResponse("telemetry-ok")

        previous = self._snapshot_settings()
        self._configure_llm_settings()
        LLMClient.clear_routing_telemetry()
        try:
            router = LLMRouter(
                LLMRoutingConfig(
                    routes={"fast": LLMRoute(model="provider/fast-model", api_base_url="https://openrouter.ai/api/v1")},
                    fallbacks={"fast": ""},
                )
            )
            client = LLMClient(router=router)
            with llm_call_context(run_id="run-telemetry", request_id="req-telemetry", conversation_id="conv-telemetry"):
                with patch("app.core.llm.urllib_request.urlopen", side_effect=fake_urlopen):
                    content = client.chat_text(
                        system_prompt="Answer briefly.",
                        user_prompt="unique-private-user-prompt",
                        max_tokens=100,
                        capability="telemetry.test",
                    )
            snapshot = LLMClient.routing_telemetry_snapshot(run_id="run-telemetry", request_id="req-telemetry")
        finally:
            self._restore_settings(previous)
            LLMClient.clear_routing_telemetry()

        self.assertEqual(content, "telemetry-ok")
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["total_calls"], 1)
        record = snapshot["recent_calls"][0]
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertEqual(record["run_id"], "run-telemetry")
        self.assertEqual(record["request_id"], "req-telemetry")
        self.assertEqual(record["capability"], "telemetry.test")
        self.assertEqual(record["model"], "provider/fast-model")
        self.assertIn("prompt_hash", record)
        self.assertEqual(record["feature_schema"], "llm-route-features/v1")
        self.assertIn("feature_values_by_name", record)
        self.assertIn("log_total_chars", record["feature_values_by_name"])
        self.assertEqual(len(record["feature_values"]), len(record["feature_names"]))
        self.assertNotIn("unique-private-user-prompt", serialized)
        self.assertNotIn("test-key", serialized)

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
