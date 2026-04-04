from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any, Dict


@dataclass
class LLMConfig:
    provider: str = os.getenv("LLM_PROVIDER", "openai_compatible")
    base_url: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8317/v1")
    model: str = os.getenv("LLM_MODEL", "gpt-5.4")
    api_key: str = os.getenv("LLM_API_KEY", "your-api-key-1")
    timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


_RUNTIME_OVERRIDES: Dict[str, Any] = {}
_LOCK = Lock()


def load_llm_config() -> LLMConfig:
    base = asdict(LLMConfig())
    with _LOCK:
        merged = {**base, **_RUNTIME_OVERRIDES}
    return LLMConfig(**merged)


def update_runtime_llm_config(payload: Dict[str, Any]) -> LLMConfig:
    allowed = {"provider", "base_url", "model", "api_key", "timeout_seconds"}
    with _LOCK:
        for key, value in payload.items():
            if key not in allowed:
                continue
            if value is None:
                continue
            if key == "timeout_seconds":
                _RUNTIME_OVERRIDES[key] = int(value)
            else:
                _RUNTIME_OVERRIDES[key] = str(value).strip()
    return load_llm_config()


def llm_config_public_payload() -> Dict[str, Any]:
    config = load_llm_config()
    masked_key = ""
    if config.api_key:
        masked_key = f"{config.api_key[:4]}...{config.api_key[-4:]}" if len(config.api_key) > 8 else "***set***"
    return {
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "timeout_seconds": config.timeout_seconds,
        "api_key_set": bool(config.api_key),
        "api_key_masked": masked_key,
    }
