from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import re
import socket
import ssl
import time
from threading import Lock
from typing import Any
from urllib import error, request as urllib_request

from app.config import settings
from app.core.llm_route_learning import extract_route_features
from app.core.llm_routing import LLMRouter, LLMRoutingDecision
from app.core.observability import log_event


_LLM_CONTEXT_RUN_ID: ContextVar[str] = ContextVar("llm_context_run_id", default="")
_LLM_CONTEXT_REQUEST_ID: ContextVar[str] = ContextVar("llm_context_request_id", default="")
_LLM_CONTEXT_CONVERSATION_ID: ContextVar[str] = ContextVar("llm_context_conversation_id", default="")
_ROUTING_TELEMETRY: deque[dict[str, Any]] = deque(maxlen=1000)
_ROUTING_TELEMETRY_LOCK = Lock()


@contextmanager
def llm_call_context(*, run_id: str = "", request_id: str = "", conversation_id: str = ""):
    """Attach graph/run identifiers to downstream LLM routing telemetry.

    The LLM client is shared by many agents and does not receive the graph state
    directly, so a small contextvar bridge lets us attribute provider calls to a
    concrete run without leaking prompts or secrets into logs.
    """

    run_token = _LLM_CONTEXT_RUN_ID.set(run_id)
    request_token = _LLM_CONTEXT_REQUEST_ID.set(request_id)
    conversation_token = _LLM_CONTEXT_CONVERSATION_ID.set(conversation_id)
    try:
        yield
    finally:
        _LLM_CONTEXT_CONVERSATION_ID.reset(conversation_token)
        _LLM_CONTEXT_REQUEST_ID.reset(request_token)
        _LLM_CONTEXT_RUN_ID.reset(run_token)


class LLMRequiredError(RuntimeError):
    """Raised when a strict agent run requires an LLM but none is available."""


class LLMClient:
    TRANSIENT_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
    TRANSIENT_ERROR_MARKERS = (
        "timed out",
        "timeout",
        "unexpected eof while reading",
        "connection reset",
        "temporarily unavailable",
        "ssl",
        "tls",
        "eof occurred in violation of protocol",
    )

    def __init__(self, router: LLMRouter | None = None) -> None:
        self.router = router or LLMRouter()
        self.last_routing_decision: dict[str, object] | None = None
        self.routing_history: list[dict[str, object]] = []

    @staticmethod
    def _prompt_digest(*parts: str) -> str:
        joined = "\n".join(parts)
        return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @staticmethod
    def _compact_error(exc: Exception | None, *, limit: int = 240) -> str:
        if exc is None:
            return ""
        text = " ".join(str(exc).split())
        if len(text) <= limit:
            return text
        return f"{text[: max(limit - 3, 1)].rstrip()}..."

    @classmethod
    def clear_routing_telemetry(cls) -> None:
        with _ROUTING_TELEMETRY_LOCK:
            _ROUTING_TELEMETRY.clear()

    @classmethod
    def routing_telemetry_snapshot(
        cls,
        *,
        run_id: str = "",
        request_id: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        with _ROUTING_TELEMETRY_LOCK:
            records = list(_ROUTING_TELEMETRY)
            window_size = len(_ROUTING_TELEMETRY)

        if run_id:
            records = [record for record in records if record.get("run_id") == run_id]
        if request_id:
            records = [record for record in records if record.get("request_id") == request_id]

        recent = list(reversed(records[-max(1, limit) :]))
        successful = [record for record in records if record.get("success") is True]
        latency_values = [float(record.get("duration_ms") or 0.0) for record in records if record.get("duration_ms") is not None]
        tier_counts = Counter(str(record.get("tier") or "unknown") for record in records)
        capability_counts = Counter(str(record.get("capability") or "general") for record in records)
        return {
            "available": bool(records),
            "total_calls": len(records),
            "window_size": window_size,
            "recent_calls": recent,
            "tier_counts": dict(tier_counts),
            "capability_counts": dict(capability_counts),
            "fallback_count": sum(1 for record in records if record.get("fallback_from")),
            "success_rate": (len(successful) / len(records)) if records else None,
            "avg_latency_ms": (sum(latency_values) / len(latency_values)) if latency_values else None,
        }

    def _record_routing_telemetry(
        self,
        *,
        decision: LLMRoutingDecision,
        attempted_decisions: list[LLMRoutingDecision],
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        capability: str,
        multimodal: bool,
        started_at: float,
        success: bool,
        response_text: str = "",
        error_exc: Exception | None = None,
        first_error_exc: Exception | None = None,
        fallback_from: str = "",
    ) -> None:
        attempted_tiers = [item.tier for item in attempted_decisions]
        attempted_models = [item.route.effective_model() for item in attempted_decisions]
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        prompt_chars = len(system_prompt) + len(user_prompt)
        route_payload = decision.route.public_payload()
        route_features = extract_route_features(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=decision.capability or capability,
            multimodal=multimodal,
        )
        feature_values_by_name = {
            name: float(value)
            for name, value in zip(route_features.names, route_features.values, strict=True)
        }
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": _LLM_CONTEXT_RUN_ID.get(),
            "request_id": _LLM_CONTEXT_REQUEST_ID.get(),
            "conversation_id": _LLM_CONTEXT_CONVERSATION_ID.get(),
            "tier": decision.tier,
            "score": decision.score,
            "capability": decision.capability or capability or "general",
            "reasons": list(decision.reasons),
            "fallback_tier": decision.fallback_tier,
            "fallback_from": fallback_from,
            "escalation_depth": decision.escalation_depth,
            "model": decision.route.effective_model(),
            "api_base_url": decision.route.effective_api_base_url(),
            "timeout_seconds": decision.route.effective_timeout_seconds(),
            "effective_max_tokens": decision.route.effective_token_budget(max_tokens),
            "requested_max_tokens": max_tokens,
            "temperature": decision.route.effective_temperature(temperature),
            "multimodal": multimodal,
            "prompt_chars": prompt_chars,
            "prompt_hash": self._prompt_digest(system_prompt, user_prompt),
            "feature_schema": "llm-route-features/v1",
            "feature_names": list(route_features.names),
            "feature_values": list(route_features.values),
            "feature_values_by_name": feature_values_by_name,
            "feature_debug": route_features.debug,
            "response_chars": len(response_text),
            "success": success,
            "duration_ms": duration_ms,
            "attempted_tiers": attempted_tiers,
            "attempted_models": attempted_models,
            "retry_budget": settings.llm_request_max_retries,
            "learned_policy_reasons": [reason for reason in decision.reasons if reason.startswith("learned_")],
            "policy_metadata": decision.policy_metadata,
            "route": {
                "model": route_payload.get("model"),
                "api_base_url": route_payload.get("api_base_url"),
                "api_key_env": route_payload.get("api_key_env"),
                "api_key_set": route_payload.get("api_key_set"),
                "timeout_seconds": route_payload.get("timeout_seconds"),
                "max_tokens": route_payload.get("max_tokens"),
                "temperature": route_payload.get("temperature"),
                "enable_thinking": route_payload.get("enable_thinking"),
            },
        }
        if error_exc is not None:
            record["error_type"] = error_exc.__class__.__name__
            record["error"] = self._compact_error(error_exc)
        if first_error_exc is not None:
            record["first_error_type"] = first_error_exc.__class__.__name__
            record["first_error"] = self._compact_error(first_error_exc)

        with _ROUTING_TELEMETRY_LOCK:
            _ROUTING_TELEMETRY.append(record)

        try:
            log_event(
                "llm.routing_call",
                level="info" if success else "warning",
                request_id=str(record["request_id"]),
                run_id=str(record["run_id"]),
                conversation_id=str(record["conversation_id"]),
                message=f"LLM routed to {decision.tier}/{decision.route.effective_model()}",
                tier=decision.tier,
                score=decision.score,
                capability=record["capability"],
                success=success,
                duration_ms=duration_ms,
                fallback_from=fallback_from,
                attempted_tiers=attempted_tiers,
                model=decision.route.effective_model(),
                prompt_chars=prompt_chars,
                prompt_hash=record["prompt_hash"],
                feature_schema=record["feature_schema"],
                feature_values=feature_values_by_name,
                feature_debug=route_features.debug,
                error=record.get("error", ""),
            )
        except Exception:
            # Observability must never break the actual model call path.
            return

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.llm_enabled and settings.llm_api_base_url and settings.llm_api_key)

    def require_configured(self, *, agent_name: str, capability: str) -> None:
        if self.is_configured():
            return
        raise LLMRequiredError(
            f"{agent_name} 需要调用 LLM 才能以真实 agent 方式运行，但当前没有可用的 LLM 配置，"
            f"因此无法继续执行 {capability}。"
        )

    @staticmethod
    def extract_json_object(content: str) -> dict[str, Any] | None:
        stripped = content.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", stripped, flags=re.IGNORECASE)
        candidate = fenced_match.group(1) if fenced_match else stripped

        if not candidate.startswith("{"):
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            candidate = candidate[start : end + 1]

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        return ""

    @classmethod
    def _is_transient_exception(cls, exc: Exception) -> bool:
        if isinstance(exc, error.HTTPError):
            return exc.code in cls.TRANSIENT_HTTP_CODES
        if isinstance(exc, (TimeoutError, socket.timeout, ssl.SSLError, ConnectionResetError)):
            return True
        if isinstance(exc, error.URLError):
            reason_text = str(exc.reason).lower()
            return any(marker in reason_text for marker in cls.TRANSIENT_ERROR_MARKERS)
        return False

    @staticmethod
    def _uses_dashscope_compatible_api(base_url: str) -> bool:
        base_url = base_url.lower()
        return "dashscope.aliyuncs.com" in base_url

    @staticmethod
    def _route_signature(decision: LLMRoutingDecision, *, max_tokens: int, temperature: float) -> tuple[object, ...]:
        route = decision.route
        return (
            route.effective_api_base_url(),
            route.effective_model(),
            route.api_key_env,
            route.effective_timeout_seconds(),
            route.effective_token_budget(max_tokens),
            route.effective_temperature(temperature),
            route.effective_enable_thinking(),
        )

    def _remember_route(self, decision: LLMRoutingDecision) -> None:
        payload = decision.public_payload()
        self.last_routing_decision = payload
        self.routing_history.append(payload)
        if len(self.routing_history) > 50:
            self.routing_history = self.routing_history[-50:]

    def _post_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
        decision: LLMRoutingDecision,
    ) -> str:
        api_base_url = decision.route.effective_api_base_url()
        api_key = decision.route.effective_api_key()
        model = decision.route.effective_model()
        if not (settings.llm_enabled and api_base_url and api_key):
            raise RuntimeError("LLM is not configured.")

        endpoint = f"{api_base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": decision.route.effective_temperature(temperature),
            "max_tokens": decision.route.effective_token_budget(max_tokens),
        }
        if self._uses_dashscope_compatible_api(api_base_url):
            payload["enable_thinking"] = decision.route.effective_enable_thinking()
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        max_attempts = max(1, settings.llm_request_max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib_request.urlopen(req, timeout=decision.route.effective_timeout_seconds()) as response:
                    body = response.read().decode("utf-8")
                parsed = json.loads(body)
                return self.extract_text(parsed["choices"][0]["message"]["content"])
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                wrapped = RuntimeError(f"LLM HTTP error {exc.code}: {detail}")
                last_error = wrapped
                if attempt < max_attempts and self._is_transient_exception(exc):
                    time.sleep(settings.llm_retry_backoff_seconds * attempt)
                    continue
                raise wrapped from exc
            except (error.URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionResetError) as exc:
                wrapped = RuntimeError(f"LLM request failed: {exc}")
                last_error = wrapped
                if attempt < max_attempts and self._is_transient_exception(exc):
                    time.sleep(settings.llm_retry_backoff_seconds * attempt)
                    continue
                raise wrapped from exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM request failed for an unknown reason.")

    def _post_with_routing(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        capability: str = "",
        multimodal: bool = False,
    ) -> str:
        started_at = time.perf_counter()
        decision = self.router.decide(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
            multimodal=multimodal,
        )
        self._remember_route(decision)
        try:
            content = self._post_messages(messages, max_tokens=max_tokens, temperature=temperature, decision=decision)
            self._record_routing_telemetry(
                decision=decision,
                attempted_decisions=[decision],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                capability=capability,
                multimodal=multimodal,
                started_at=started_at,
                success=True,
                response_text=content,
            )
            return content
        except RuntimeError as exc:
            fallback = self.router.fallback_decision(decision, error_message=str(exc))
            if fallback is None:
                self._record_routing_telemetry(
                    decision=decision,
                    attempted_decisions=[decision],
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    capability=capability,
                    multimodal=multimodal,
                    started_at=started_at,
                    success=False,
                    error_exc=exc,
                )
                raise
            if self._route_signature(fallback, max_tokens=max_tokens, temperature=temperature) == self._route_signature(
                decision,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                self._record_routing_telemetry(
                    decision=decision,
                    attempted_decisions=[decision],
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    capability=capability,
                    multimodal=multimodal,
                    started_at=started_at,
                    success=False,
                    error_exc=exc,
                )
                raise
            self._remember_route(fallback)
            try:
                content = self._post_messages(messages, max_tokens=max_tokens, temperature=temperature, decision=fallback)
            except RuntimeError as fallback_exc:
                self._record_routing_telemetry(
                    decision=fallback,
                    attempted_decisions=[decision, fallback],
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    capability=capability,
                    multimodal=multimodal,
                    started_at=started_at,
                    success=False,
                    error_exc=fallback_exc,
                    first_error_exc=exc,
                    fallback_from=decision.tier,
                )
                raise
            self._record_routing_telemetry(
                decision=fallback,
                attempted_decisions=[decision, fallback],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                capability=capability,
                multimodal=multimodal,
                started_at=started_at,
                success=True,
                response_text=content,
                first_error_exc=exc,
                fallback_from=decision.tier,
            )
            return content

    def chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.1,
        capability: str = "",
    ) -> str:
        return self._post_with_routing(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
        )

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.1,
        capability: str = "",
    ) -> dict[str, Any] | None:
        content = self.chat_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
        )
        return self.extract_json_object(content)

    def chat_multimodal_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
        capability: str = "vision",
    ) -> str:
        return self._post_with_routing(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
            multimodal=True,
        )

    def chat_multimodal_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
        capability: str = "vision",
    ) -> dict[str, Any] | None:
        content = self.chat_multimodal_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_data_url=image_data_url,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
        )
        return self.extract_json_object(content)


LLMClientService = LLMClient
