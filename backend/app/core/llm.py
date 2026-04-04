from __future__ import annotations

import json
import re
import socket
import ssl
import time
from typing import Any
from urllib import error, request as urllib_request

from app.config import settings


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

    def _post_messages(self, messages: list[dict[str, Any]], *, max_tokens: int, temperature: float) -> str:
        if not self.is_configured():
            raise RuntimeError("LLM is not configured.")

        endpoint = f"{settings.llm_api_base_url}/chat/completions"
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(settings.llm_max_tokens, max_tokens),
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.llm_api_key}",
            },
            method="POST",
        )

        max_attempts = max(1, settings.llm_request_max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib_request.urlopen(req, timeout=settings.llm_request_timeout_seconds) as response:
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

    def chat_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1) -> str:
        return self._post_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1) -> dict[str, Any] | None:
        content = self.chat_text(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens, temperature=temperature)
        return self.extract_json_object(content)

    def chat_multimodal_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        return self._post_messages(
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
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def chat_multimodal_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> dict[str, Any] | None:
        content = self.chat_multimodal_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_data_url=image_data_url,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return self.extract_json_object(content)


LLMClientService = LLMClient
