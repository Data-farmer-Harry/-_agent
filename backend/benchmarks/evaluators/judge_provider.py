from __future__ import annotations

import json
import os
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request as urllib_request


class JudgeChatClient(Protocol):
    def chat_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.0) -> str:
        ...


@dataclass(frozen=True)
class JudgeProviderConfig:
    provider: str = "offline_contract"
    model: str = "offline-contract-judge/v1"
    api_base_url: str = ""
    api_key: str = ""
    timeout_seconds: int = 60
    max_retries: int = 1
    max_tokens: int = 900
    temperature: float = 0.0
    enabled: bool = False

    @property
    def configured(self) -> bool:
        if self.provider in {"offline_contract", "mock", "local"}:
            return True
        return bool(self.enabled and self.api_base_url and self.api_key and self.model)


class OpenAICompatibleJudgeClient:
    """Minimal OpenAI-compatible chat client for live Judge providers.

    OpenRouter and DashScope compatible-mode both expose `/chat/completions`.
    This client is deliberately tiny and dependency-free so benchmark/live gates
    do not need an extra SDK. It is only used when a live provider is explicitly
    enabled; quick CI keeps using the deterministic offline contract judge.
    """

    TRANSIENT_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
    TRANSIENT_ERROR_MARKERS = (
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
        "ssl",
        "tls",
    )

    def __init__(self, config: JudgeProviderConfig) -> None:
        if not config.configured:
            raise RuntimeError(f"Judge provider is not configured: {config.provider}")
        self.config = config

    def chat_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.0) -> str:
        endpoint = self.config.api_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": min(max_tokens, self.config.max_tokens),
        }
        req = urllib_request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )

        attempts = max(1, self.config.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with urllib_request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                parsed = json.loads(body)
                return _extract_chat_content(parsed)
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                wrapped = RuntimeError(f"Judge provider HTTP error {exc.code}: {detail}")
                last_error = wrapped
                if attempt < attempts and exc.code in self.TRANSIENT_HTTP_CODES:
                    time.sleep(0.8 * attempt)
                    continue
                raise wrapped from exc
            except (error.URLError, TimeoutError, socket.timeout, ssl.SSLError, ConnectionResetError) as exc:
                wrapped = RuntimeError(f"Judge provider request failed: {exc}")
                last_error = wrapped
                reason = str(getattr(exc, "reason", exc)).lower()
                if attempt < attempts and any(marker in reason for marker in self.TRANSIENT_ERROR_MARKERS):
                    time.sleep(0.8 * attempt)
                    continue
                raise wrapped from exc
        if last_error:
            raise last_error
        raise RuntimeError("Judge provider request failed for an unknown reason.")


def judge_provider_config_from_env(env: dict[str, str] | None = None, *, provider: str | None = None) -> JudgeProviderConfig:
    env = env or os.environ
    selected = (provider or env.get("MATERIALS_JUDGE_PROVIDER") or "offline_contract").strip().lower()
    if selected in {"offline", "offline_contract", "deterministic"}:
        return JudgeProviderConfig(provider="offline_contract", model="offline-contract-judge/v1", enabled=False)
    if selected == "mock":
        return JudgeProviderConfig(provider="mock", model="mock-judge/v1", enabled=True)
    if selected == "openrouter":
        return JudgeProviderConfig(
            provider="openrouter",
            model=env.get("OPENROUTER_JUDGE_MODEL", "openai/gpt-4o-mini"),
            api_base_url=env.get("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=env.get("OPENROUTER_API_KEY", ""),
            timeout_seconds=_env_int(env, "MATERIALS_JUDGE_TIMEOUT_SECONDS", 60),
            max_retries=_env_int(env, "MATERIALS_JUDGE_MAX_RETRIES", 1),
            max_tokens=_env_int(env, "MATERIALS_JUDGE_MAX_TOKENS", 900),
            enabled=_env_bool(env, "MATERIALS_JUDGE_LIVE_ENABLED", False),
        )
    if selected == "dashscope":
        return JudgeProviderConfig(
            provider="dashscope",
            model=env.get("DASHSCOPE_JUDGE_MODEL", "qwen-plus"),
            api_base_url=env.get("DASHSCOPE_API_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            api_key=env.get("DASHSCOPE_API_KEY", ""),
            timeout_seconds=_env_int(env, "MATERIALS_JUDGE_TIMEOUT_SECONDS", 60),
            max_retries=_env_int(env, "MATERIALS_JUDGE_MAX_RETRIES", 1),
            max_tokens=_env_int(env, "MATERIALS_JUDGE_MAX_TOKENS", 900),
            enabled=_env_bool(env, "MATERIALS_JUDGE_LIVE_ENABLED", False),
        )
    return JudgeProviderConfig(provider=selected, model=f"{selected}/unknown", enabled=False)


def build_live_judge_client(config: JudgeProviderConfig) -> JudgeChatClient | None:
    if config.provider in {"offline_contract", "mock", "local"} or not config.configured:
        return None
    return OpenAICompatibleJudgeClient(config)


def sanitized_provider_metadata(config: JudgeProviderConfig) -> dict[str, Any]:
    return {
        "provider": config.provider,
        "model": config.model,
        "api_base_url": config.api_base_url,
        "configured": config.configured,
        "enabled": config.enabled,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "max_tokens": config.max_tokens,
        "api_key_present": bool(config.api_key),
    }


def _extract_chat_content(parsed: dict[str, Any]) -> str:
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Judge provider response missing choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "".join(str(part) for part in parts)
    raise RuntimeError("Judge provider response missing message content.")


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(env.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}
