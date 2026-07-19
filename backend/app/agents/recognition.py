from __future__ import annotations

import ast
import json
import re
from typing import Any

from app.config import settings
from app.core.artifacts import ArtifactService
from app.core.llm import LLMClient, LLMRequiredError
from app.core.llm_capabilities import LLMCapability
from app.recognition_simulator import RecognitionSimulationBundle, RecognitionSimulationService
from app.state import AgentGraphState, AxisSpec, CriticalPoint, PlotRegionHint, RecognitionResult


class RecognitionAgent:
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")
    TEMPERATURE_PATTERN = re.compile(r"(temperature|temp|温度|℃|°c|kelvin|\bk\b)", flags=re.IGNORECASE)
    COMPOSITION_PATTERN = re.compile(r"(composition|mole fraction|at\.?%|wt\.?%|成分|摩尔分数|质量分数)", flags=re.IGNORECASE)
    ELEMENT_ALIASES = {
        "铝": "Al",
        "al": "Al",
        "aluminum": "Al",
        "aluminium": "Al",
        "锌": "Zn",
        "zn": "Zn",
        "zinc": "Zn",
        "镁": "Mg",
        "mg": "Mg",
        "magnesium": "Mg",
        "镍": "Ni",
        "ni": "Ni",
        "nickel": "Ni",
        "铁": "Fe",
        "fe": "Fe",
        "iron": "Fe",
        "铅": "Pb",
        "pb": "Pb",
        "lead": "Pb",
        "锡": "Sn",
        "sn": "Sn",
        "tin": "Sn",
        "钴": "Co",
        "co": "Co",
        "cobalt": "Co",
        "铬": "Cr",
        "cr": "Cr",
        "chromium": "Cr",
        "钛": "Ti",
        "ti": "Ti",
        "titanium": "Ti",
        "钒": "V",
        "v": "V",
        "vanadium": "V",
        "铌": "Nb",
        "nb": "Nb",
        "niobium": "Nb",
        "铜": "Cu",
        "cu": "Cu",
        "copper": "Cu",
        "铂": "Pt",
        "pt": "Pt",
        "platinum": "Pt",
        "铼": "Re",
        "re": "Re",
        "rhenium": "Re",
        "钯": "Pd",
        "pd": "Pd",
        "palladium": "Pd",
        "钌": "Ru",
        "ru": "Ru",
        "ruthenium": "Ru",
        "锝": "Tc",
        "tc": "Tc",
        "technetium": "Tc",
        "钼": "Mo",
        "mo": "Mo",
        "molybdenum": "Mo",
    }

    def __init__(self, llm_client: LLMClient | None = None, artifact_service: ArtifactService | None = None) -> None:
        self.llm_client = llm_client or LLMClient()
        self.simulation_service = RecognitionSimulationService(artifact_service or ArtifactService())

    @staticmethod
    def _coerce_confidence(value: Any, default: float) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = default
        return max(0.0, min(confidence, 1.0))

    @classmethod
    def _normalize_system(cls, value: Any, *, labels: list[str], raw_summary: str, fallback: str) -> str:
        candidates: list[str] = []
        if isinstance(value, str) and value.strip():
            candidates.append(value)
        candidates.extend(label for label in labels if isinstance(label, str) and label.strip())
        if raw_summary.strip():
            candidates.append(raw_summary)
        if fallback.strip():
            candidates.append(fallback)

        for candidate in candidates:
            match = cls.SYSTEM_PATTERN.search(candidate)
            if match:
                return match.group(1).replace("/", "-").replace(" ", "")
            alias_match = cls._infer_system_from_aliases(candidate)
            if alias_match:
                return alias_match
        return fallback.strip()

    @classmethod
    def _infer_system_from_aliases(cls, text: str) -> str:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return ""
        hits: list[tuple[int, str]] = []
        for alias, symbol in cls.ELEMENT_ALIASES.items():
            index = lowered.find(alias)
            if index >= 0:
                hits.append((index, symbol))
        if not hits:
            return ""
        hits.sort(key=lambda item: item[0])
        ordered_symbols: list[str] = []
        for _, symbol in hits:
            if symbol not in ordered_symbols:
                ordered_symbols.append(symbol)
        if len(ordered_symbols) >= 2:
            return "-".join(ordered_symbols[:3])
        return ""

    @classmethod
    def _normalize_system_from_context(cls, *, primary: Any, labels: list[str], raw_summary: str, request_message: str, request_system_name: str, asset_name: str) -> str:
        merged_fallback = "\n".join(part for part in (request_system_name, request_message, asset_name) if str(part or "").strip())
        return cls._normalize_system(primary, labels=labels, raw_summary=raw_summary, fallback=merged_fallback)

    @staticmethod
    def _normalize_diagram_type(value: Any, *, fallback: str) -> str:
        text = str(value or fallback or "binary").strip().lower()
        if "ternary" in text or "三元" in text:
            return "ternary"
        return "binary"

    @staticmethod
    def _coerce_optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _coerce_ratio(cls, value: Any) -> float | None:
        numeric = cls._coerce_optional_float(value)
        if numeric is None:
            return None
        if numeric > 1.0 and numeric <= 100.0:
            numeric = numeric / 100.0
        if numeric < 0.0 or numeric > 1.0:
            return None
        return numeric

    @classmethod
    def _normalize_plot_region(cls, payload: Any) -> PlotRegionHint:
        raw = payload if isinstance(payload, dict) else {}
        region = PlotRegionHint(
            left=cls._coerce_ratio(raw.get("left")),
            top=cls._coerce_ratio(raw.get("top")),
            right=cls._coerce_ratio(raw.get("right")),
            bottom=cls._coerce_ratio(raw.get("bottom")),
            confidence=cls._coerce_confidence(raw.get("confidence"), 0.0) if raw else None,
            source=str(raw.get("source") or "llm_plot_region") if raw else "",
        )
        if None not in (region.left, region.right) and region.right <= region.left:
            region.left = None
            region.right = None
        if None not in (region.top, region.bottom) and region.bottom <= region.top:
            region.top = None
            region.bottom = None
        return region

    @classmethod
    def _normalize_axis(cls, payload: Any, *, default_label: str = "") -> AxisSpec:
        raw = payload if isinstance(payload, dict) else {}
        axis = AxisSpec(
            label=str(raw.get("label") or default_label or "").strip(),
            minimum=cls._coerce_optional_float(raw.get("minimum", raw.get("min"))),
            maximum=cls._coerce_optional_float(raw.get("maximum", raw.get("max"))),
            unit=str(raw.get("unit") or "").strip(),
        )
        label_lower = axis.label.lower()
        unit_lower = axis.unit.lower()
        if ("mole fraction" in label_lower or "composition" in label_lower) and axis.minimum is None and axis.maximum is None:
            if unit_lower in {"", "fraction"}:
                axis.minimum = 0.0
                axis.maximum = 1.0
            elif "%" in axis.unit or "at.%" in unit_lower:
                axis.minimum = 0.0
                axis.maximum = 100.0
        if axis.minimum is not None and axis.maximum is not None and axis.maximum < axis.minimum:
            axis.minimum, axis.maximum = axis.maximum, axis.minimum
        return axis

    @classmethod
    def _normalize_phases(cls, values: Any) -> list[str]:
        seen: set[str] = set()
        phases: list[str] = []
        for value in values if isinstance(values, list) else []:
            candidate = value
            if isinstance(value, dict):
                candidate = value.get("name") or value.get("label") or value.get("phase") or value.get("symbol") or value
            elif isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("{") and stripped.endswith("}"):
                    try:
                        parsed = ast.literal_eval(stripped)
                    except (SyntaxError, ValueError):
                        parsed = None
                    if isinstance(parsed, dict):
                        candidate = parsed.get("name") or parsed.get("label") or parsed.get("phase") or parsed.get("symbol") or value
            text = re.sub(r"\s+", " ", str(candidate)).strip(" \t\r\n,;")
            if not text:
                continue
            if text.lower() == "liquid":
                text = "LIQUID"
            elif re.fullmatch(r"[A-Za-z0-9_+.-]+", text):
                text = text.upper()
            key = text.upper()
            if key in seen:
                continue
            seen.add(key)
            phases.append(text)
        return phases

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
            plot_region=PlotRegionHint(),
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
                self.llm_client.require_configured(agent_name="RecognitionAgent", capability=LLMCapability.VISION_RECOGNITION)
            return self._fallback_result(state)

        request = state["request"]
        try:
            payload = self.llm_client.chat_multimodal_json(
                system_prompt=(
                    "You are the RecognitionAgent for a materials phase-diagram project. "
                    "Read the uploaded phase-diagram screenshot and return structured JSON only. "
                    "Be conservative and physically plausible. "
                    "If axis tick labels are unclear, set numeric fields to null instead of guessing. "
                    "Do not generate final HTML. Only provide structured diagram facts and coarse geometry hints."
                ),
                user_prompt=(
                    "Return JSON with keys: system, diagram_type, x_axis, y_axis, plot_region, phases, critical_points, labels, confidence, raw_summary. "
                    "x_axis and y_axis should have: label, minimum, maximum, unit. "
                    "plot_region should have normalized values in 0..1: left, top, right, bottom, confidence. "
                    "critical_points should be a list of objects with: label, composition, temperature, x_norm, y_norm, confidence, notes. "
                    "This is a materials phase diagram. "
                    "Do not invent a narrow temperature range if the vertical axis ticks are hard to read. "
                    "If the plotting area can be located, estimate its normalized bounding box conservatively. "
                    "If a critical point can be approximately localized in image coordinates, include x_norm and y_norm. "
                    "If the temperature axis cannot be read confidently, keep the label/unit and leave minimum/maximum empty.\n"
                    f"User message:\n{request.message}\n\n"
                    f"Known context:\n{json.dumps({'system_name': request.system_name, 'diagram_type': request.diagram_type}, ensure_ascii=False)}"
                ),
                image_data_url=image_asset.data_url,
                max_tokens=1200,
                temperature=0.1,
                capability=LLMCapability.VISION_RECOGNITION,
            )
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"RecognitionAgent 调用多模态 LLM 识别截图时失败：{exc}") from exc
            return self._fallback_result(state)

        if not payload:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("RecognitionAgent 需要结构化识别结果，但本次没有得到有效 JSON。")
            return self._fallback_result(state)

        raw_summary = str(payload.get("raw_summary") or "").strip()
        labels = [str(item).strip() for item in payload.get("labels", []) if str(item).strip()]
        x_axis = self._normalize_axis(payload.get("x_axis"), default_label="composition")
        y_axis = self._normalize_axis(payload.get("y_axis"), default_label="temperature")
        plot_region = self._normalize_plot_region(payload.get("plot_region"))
        confidence = self._coerce_confidence(payload.get("confidence"), 0.6)
        critical_points = []
        for item in payload.get("critical_points", []) if isinstance(payload.get("critical_points"), list) else []:
            if not isinstance(item, dict):
                continue
            critical_points.append(
                CriticalPoint(
                    label=str(item.get("label") or ""),
                    composition=self._coerce_optional_float(item.get("composition")),
                    temperature=self._coerce_optional_float(item.get("temperature")),
                    notes=str(item.get("notes") or ""),
                    x_norm=self._coerce_ratio(item.get("x_norm")),
                    y_norm=self._coerce_ratio(item.get("y_norm")),
                    confidence=self._coerce_confidence(item.get("confidence"), 0.0) if any(
                        key in item for key in ("confidence", "x_norm", "y_norm")
                    ) else None,
                )
            )

        return RecognitionResult(
            system=self._normalize_system(
                payload.get("system"),
                labels=labels,
                raw_summary=raw_summary,
                fallback=f"{request.system_name}\n{request.message}\n{image_asset.name}",
            ),
            diagram_type=self._normalize_diagram_type(payload.get("diagram_type"), fallback=request.diagram_type),
            x_axis=x_axis,
            y_axis=y_axis,
            plot_region=plot_region,
            phases=self._normalize_phases(payload.get("phases")),
            critical_points=critical_points,
            labels=labels,
            confidence=confidence,
            source="llm_recognition_agent",
            raw_summary=raw_summary,
        )

    def build_simulation_bundle(self, *, state: AgentGraphState, recognition_result: RecognitionResult) -> RecognitionSimulationBundle:
        uploaded_assets = state.get("uploaded_assets", [])
        image_asset = next((asset for asset in uploaded_assets if asset.media_type.startswith("image/")), None)
        return self.simulation_service.build_bundle(
            run_id=state["run_id"],
            recognition_result=recognition_result,
            request_message=state["request"].message,
            source_image_data_url=image_asset.data_url if image_asset else None,
            source_image_name=image_asset.name if image_asset else "",
        )
