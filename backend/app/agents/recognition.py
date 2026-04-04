from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.state import AgentGraphState, AxisSpec, CriticalPoint, RecognitionResult


class RecognitionAgent:
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _fallback_result(state: AgentGraphState) -> RecognitionResult:
        request = state["request"]
        asset = state.get("uploaded_assets", [None])[0]
        match = RecognitionAgent.SYSTEM_PATTERN.search(request.message)
        system = match.group(1).replace("/", "-").replace(" ", "") if match else request.system_name
        labels = []
        if asset and asset.name:
            labels.append(asset.name)
        return RecognitionResult(
            system=system or "",
            diagram_type=request.diagram_type,
            x_axis=AxisSpec(label="composition"),
            y_axis=AxisSpec(label="temperature", unit="K"),
            phases=[],
            critical_points=[],
            labels=labels,
            confidence=0.35,
            source="heuristic_recognition_fallback",
            raw_summary="Uploaded image reached the RecognitionAgent, but the current run fell back to a lightweight heuristic parser.",
        )

    def recognize(self, state: AgentGraphState) -> RecognitionResult:
        uploaded_assets = state.get("uploaded_assets", [])
        if not uploaded_assets:
            return RecognitionResult(
                system="",
                diagram_type="binary",
                confidence=0.0,
                source="no_asset",
                raw_summary="No uploaded image was provided.",
            )

        image_asset = next((asset for asset in uploaded_assets if asset.media_type.startswith("image/")), uploaded_assets[0])
        if not self.llm_client.is_configured() or not image_asset.data_url:
            if settings.require_llm_for_agents:
                if not image_asset.data_url:
                    raise LLMRequiredError("RecognitionAgent 收到了截图任务，但当前图片数据为空，无法执行真实多模态识别。")
                self.llm_client.require_configured(agent_name="RecognitionAgent", capability="多模态相图识别")
            return self._fallback_result(state)

        request = state["request"]
        try:
            payload = self.llm_client.chat_multimodal_json(
                system_prompt=(
                    "You are the RecognitionAgent for a materials phase-diagram project. "
                    "Read the uploaded phase-diagram screenshot and return structured JSON only."
                ),
                user_prompt=(
                    "Return JSON with keys: system, diagram_type, x_axis, y_axis, phases, critical_points, labels, confidence, raw_summary. "
                    "x_axis and y_axis should have: label, minimum, maximum, unit. "
                    "critical_points should be a list of objects with: label, composition, temperature, notes. "
                    "Be conservative. If uncertain, leave fields blank instead of hallucinating.\n"
                    f"User message:\n{request.message}\n\n"
                    f"Known context:\n{json.dumps({'system_name': request.system_name, 'diagram_type': request.diagram_type}, ensure_ascii=False)}"
                ),
                image_data_url=image_asset.data_url,
                max_tokens=1200,
                temperature=0.1,
            )
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"RecognitionAgent 调用多模态 LLM 识别截图时失败：{exc}") from exc
            return self._fallback_result(state)

        if not payload:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("RecognitionAgent 需要结构化识别结果，但本次没有得到有效 JSON。")
            return self._fallback_result(state)

        critical_points = []
        for item in payload.get("critical_points", []) if isinstance(payload.get("critical_points"), list) else []:
            if not isinstance(item, dict):
                continue
            critical_points.append(
                CriticalPoint(
                    label=str(item.get("label") or ""),
                    composition=float(item["composition"]) if item.get("composition") is not None else None,
                    temperature=float(item["temperature"]) if item.get("temperature") is not None else None,
                    notes=str(item.get("notes") or ""),
                )
            )

        return RecognitionResult(
            system=str(payload.get("system") or "").strip(),
            diagram_type=str(payload.get("diagram_type") or request.diagram_type or "binary"),
            x_axis=AxisSpec.model_validate(payload.get("x_axis") or {}),
            y_axis=AxisSpec.model_validate(payload.get("y_axis") or {}),
            phases=[str(item) for item in payload.get("phases", []) if str(item).strip()],
            critical_points=critical_points,
            labels=[str(item) for item in payload.get("labels", []) if str(item).strip()],
            confidence=max(0.0, min(float(payload.get("confidence", 0.6)), 1.0)),
            source="llm_recognition_agent",
            raw_summary=str(payload.get("raw_summary") or "").strip(),
        )
