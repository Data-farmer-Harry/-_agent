from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.config.llm_config import LLMConfig, load_llm_config
from src.utils.attachments import build_base64_payload, build_data_url


@dataclass
class LLMGenerationResult:
    content: Union[Dict[str, Any], str]
    attachment_updates: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class LLMAdapter:
    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config

    def supports_native_pdf_input(self) -> bool:
        config = self.config or load_llm_config()
        provider = (config.provider or "openai_compatible").strip().lower()
        return provider == "openai_responses" and bool(config.api_key)

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMGenerationResult:
        config = self.config or load_llm_config()
        provider = (config.provider or "openai_compatible").strip().lower()
        normalized_attachments = attachments or []
        if provider == "openai_responses":
            if not config.api_key:
                return self._fallback_response(
                    user_prompt,
                    response_schema,
                    self._metadata_only_updates(
                        self._image_attachments(normalized_attachments),
                        "当前模型配置未启用远程多模态调用，已仅按文件元信息参与对话。",
                    ),
                )
            return self._call_openai_responses(
                config,
                system_prompt,
                user_prompt,
                response_schema,
                normalized_attachments,
            )
        if provider == "ollama":
            return self._call_ollama(config, system_prompt, user_prompt, response_schema, normalized_attachments)
        if provider != "openai_compatible":
            return self._fallback_response(
                user_prompt,
                response_schema,
                self._metadata_only_updates(
                    self._image_attachments(normalized_attachments),
                    "当前 provider 未启用图片输入，已仅按文件元信息参与对话。",
                ),
            )
        if not config.api_key:
            return self._fallback_response(
                user_prompt,
                response_schema,
                self._metadata_only_updates(
                    self._image_attachments(normalized_attachments),
                    "当前模型配置未启用远程多模态调用，已仅按文件元信息参与对话。",
                ),
            )
        return self._call_remote(config, system_prompt, user_prompt, response_schema, normalized_attachments)

    def _call_openai_responses(
        self,
        config: LLMConfig,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
    ) -> LLMGenerationResult:
        pdf_attachments = self._pdf_attachments(attachments)
        image_attachments = self._image_attachments(attachments)
        payload: Dict[str, Any] = {
            "model": config.model,
            "instructions": system_prompt,
            "input": self._build_responses_input(user_prompt, pdf_attachments, image_attachments),
        }
        req = urllib.request.Request(
            url=f"{config.base_url.rstrip('/')}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            http.client.HTTPException,
            OSError,
        ):
            native_updates = self._native_file_updates(
                pdf_attachments,
                "当前模型未接受原生 PDF 文件输入，已回退到非原生路径。",
            )
            native_updates.update(
                self._metadata_only_updates(
                    image_attachments,
                    "当前模型未读取图片内容，已仅按文件元信息参与对话。",
                )
            )
            return self._fallback_response(user_prompt, response_schema, native_updates)

        text = self._normalize_responses_output_text(body)
        attachment_updates = self._native_file_updates(pdf_attachments)
        attachment_updates.update(self._multimodal_updates(image_attachments))
        return self._build_generation_result(text, user_prompt, response_schema, attachment_updates)

    def _call_remote(
        self,
        config: LLMConfig,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
        forced_metadata_updates: Optional[Dict[str, Dict[str, Any]]] = None,
        allow_image_retry: bool = True,
    ) -> LLMGenerationResult:
        image_attachments = self._image_attachments(attachments)
        payload: Dict[str, Any] = {
            "model": config.model,
            "messages": self._build_openai_messages(system_prompt, user_prompt, image_attachments),
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            url=f"{config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            http.client.HTTPException,
            OSError,
        ):
            if image_attachments and allow_image_retry:
                metadata_updates = self._metadata_only_updates(
                    image_attachments,
                    "当前模型未读取图片内容，已仅按文件元信息参与对话。",
                )
                return self._call_remote(
                    config,
                    system_prompt,
                    user_prompt,
                    response_schema,
                    [],
                    forced_metadata_updates=metadata_updates,
                    allow_image_retry=False,
                )
            return self._fallback_response(
                user_prompt,
                response_schema,
                forced_metadata_updates or {},
            )

        text = self._normalize_response_text(body["choices"][0]["message"]["content"])
        attachment_updates = forced_metadata_updates or self._multimodal_updates(image_attachments)
        return self._build_generation_result(text, user_prompt, response_schema, attachment_updates)

    def _call_ollama(
        self,
        config: LLMConfig,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
        forced_metadata_updates: Optional[Dict[str, Dict[str, Any]]] = None,
        allow_image_retry: bool = True,
    ) -> LLMGenerationResult:
        image_attachments = self._image_attachments(attachments)
        payload: Dict[str, Any] = {
            "model": config.model,
            "messages": self._build_ollama_messages(system_prompt, user_prompt, image_attachments),
            "stream": False,
        }
        if response_schema:
            payload["format"] = "json"
        req = urllib.request.Request(
            url=f"{config.base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            http.client.HTTPException,
            OSError,
        ):
            if image_attachments and allow_image_retry:
                metadata_updates = self._metadata_only_updates(
                    image_attachments,
                    "当前模型未读取图片内容，已仅按文件元信息参与对话。",
                )
                return self._call_ollama(
                    config,
                    system_prompt,
                    user_prompt,
                    response_schema,
                    [],
                    forced_metadata_updates=metadata_updates,
                    allow_image_retry=False,
                )
            return self._fallback_response(
                user_prompt,
                response_schema,
                forced_metadata_updates or {},
            )

        text = str(body.get("message", {}).get("content", "")).strip()
        attachment_updates = forced_metadata_updates or self._multimodal_updates(image_attachments)
        return self._build_generation_result(text, user_prompt, response_schema, attachment_updates)

    def _build_generation_result(
        self,
        text: str,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]],
        attachment_updates: Dict[str, Dict[str, Any]],
    ) -> LLMGenerationResult:
        if response_schema:
            parsed = self._parse_json_text(text)
            if parsed is not None:
                return LLMGenerationResult(content=parsed, attachment_updates=attachment_updates)
            return self._fallback_response(user_prompt, response_schema, attachment_updates)
        content = text or self._fallback_text(user_prompt)
        return LLMGenerationResult(content=content, attachment_updates=attachment_updates)

    def _build_openai_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        image_attachments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not image_attachments:
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        content: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for attachment in image_attachments:
            path = Path(str(attachment.get("path", "") or ""))
            mime_type = str(attachment.get("mime_type", "image/png") or "image/png")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": build_data_url(path, mime_type)},
                }
            )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

    def _build_ollama_messages(
        self,
        system_prompt: str,
        user_prompt: str,
        image_attachments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        user_message: Dict[str, Any] = {"role": "user", "content": user_prompt}
        if image_attachments:
            user_message["images"] = [
                build_base64_payload(Path(str(attachment.get("path", "") or "")))
                for attachment in image_attachments
            ]
        return [
            {"role": "system", "content": system_prompt},
            user_message,
        ]

    def _build_responses_input(
        self,
        user_prompt: str,
        pdf_attachments: List[Dict[str, Any]],
        image_attachments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        content: List[Dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
        for attachment in pdf_attachments:
            path = Path(str(attachment.get("path", "") or ""))
            content.append(
                {
                    "type": "input_file",
                    "filename": str(
                        attachment.get("original_name")
                        or attachment.get("stored_name")
                        or path.name
                        or "attachment.pdf"
                    ),
                    "file_data": build_base64_payload(path),
                }
            )
        for attachment in image_attachments:
            path = Path(str(attachment.get("path", "") or ""))
            mime_type = str(attachment.get("mime_type", "image/png") or "image/png")
            content.append(
                {
                    "type": "input_image",
                    "image_url": build_data_url(path, mime_type),
                }
            )
        return [{"role": "user", "content": content}]

    @staticmethod
    def _normalize_response_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text", "") or "").strip()
                    if text:
                        parts.append(text)
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _normalize_responses_output_text(self, body: Dict[str, Any]) -> str:
        output_text = body.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        parts: List[str] = []
        for item in body.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for content in item.get("content", []) or []:
                    if not isinstance(content, dict):
                        continue
                    text = str(content.get("text", "") or "").strip()
                    if text:
                        parts.append(text)
            else:
                text = str(item.get("text", "") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    @staticmethod
    def _image_attachments(attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            attachment
            for attachment in attachments
            if str(attachment.get("category", "") or "") == "image" and str(attachment.get("path", "") or "")
        ]

    @staticmethod
    def _pdf_attachments(attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            attachment
            for attachment in attachments
            if str(attachment.get("category", "") or "") == "pdf" and str(attachment.get("path", "") or "")
        ]

    def _metadata_only_updates(
        self,
        attachments: List[Dict[str, Any]],
        reason: str,
    ) -> Dict[str, Dict[str, Any]]:
        return {
            self._attachment_key(attachment): {
                "conversation_mode": "metadata-only",
                "conversation_used": True,
                "fallback_reason": reason,
            }
            for attachment in attachments
        }

    def _multimodal_updates(self, attachments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return {
            self._attachment_key(attachment): {
                "conversation_mode": "multimodal",
                "conversation_used": True,
                "fallback_reason": "",
            }
            for attachment in attachments
        }

    def _native_file_updates(
        self,
        attachments: List[Dict[str, Any]],
        reason: str = "",
    ) -> Dict[str, Dict[str, Any]]:
        return {
            self._attachment_key(attachment): {
                "conversation_mode": "native-file",
                "conversation_used": True,
                "fallback_reason": reason,
            }
            for attachment in attachments
        }

    @staticmethod
    def _attachment_key(attachment: Dict[str, Any]) -> str:
        return str(attachment.get("upload_id") or attachment.get("stored_name") or attachment.get("original_name") or "")

    @staticmethod
    def _parse_json_text(text: str) -> Optional[Dict[str, Any]]:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if fenced:
            try:
                parsed = json.loads(fenced.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None
        return None

    def _fallback_response(
        self,
        user_prompt: str,
        response_schema: Optional[Dict[str, Any]],
        attachment_updates: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> LLMGenerationResult:
        if response_schema:
            return LLMGenerationResult(
                content={"raw_text": user_prompt},
                attachment_updates=attachment_updates or {},
            )
        return LLMGenerationResult(
            content=self._fallback_text(user_prompt),
            attachment_updates=attachment_updates or {},
        )

    @staticmethod
    def _fallback_text(user_prompt: str) -> str:
        return user_prompt
