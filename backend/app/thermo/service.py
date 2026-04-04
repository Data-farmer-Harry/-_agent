from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.state import DiagramRequest
from app.thermo.codegen import CodeGenerationService
from app.thermo.rag_service import ThermoRagService
from app.thermo.registry import build_calculated_prompt_hint, get_calculated_binary_card, retrieve_thermo_database
from app.utils.constants import normalize_system_key


class PhaseDiagramAgentService:
    DEFAULT_TEMPERATURE_MIN = 300.0
    DEFAULT_TEMPERATURE_MAX = 1800.0
    DEFAULT_PRESSURE = 101325.0
    DEFAULT_STEP_SIZE = 50.0
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")
    RANGE_PATTERN = re.compile(
        r"(?P<low>\d+(?:\.\d+)?)\s*(?P<low_unit>k|K|℃|°C|°c|c|C)?\s*"
        r"(?:-|~|到|至|to)\s*"
        r"(?P<high>\d+(?:\.\d+)?)\s*(?P<high_unit>k|K|℃|°C|°c|c|C)?"
    )
    PRESSURE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>gpa|mpa|kpa|pa|bar)\b", flags=re.IGNORECASE)
    STEP_PATTERN = re.compile(r"(?:step(?:\s*size)?|步长)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)", flags=re.IGNORECASE)

    def __init__(self, codegen_service: CodeGenerationService, llm_client: LLMClient | None = None) -> None:
        self.codegen_service = codegen_service
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _normalize_system_name(system_name: str) -> str:
        return re.sub(r"\s+", "", system_name).replace("/", "-")

    @classmethod
    def _extract_system_name(cls, message: str, fallback: str) -> str:
        match = cls.SYSTEM_PATTERN.search(message)
        if match:
            return cls._normalize_system_name(match.group(1))
        return fallback or "Unknown system"

    @classmethod
    def _extract_temperature_range(cls, message: str, default_min: float, default_max: float) -> tuple[float, float]:
        match = cls.RANGE_PATTERN.search(message)
        if not match:
            return default_min, default_max

        lower = float(match.group("low"))
        upper = float(match.group("high"))
        if upper <= lower:
            lower, upper = min(lower, upper), max(lower, upper)

        low_unit = (match.group("low_unit") or "").lower()
        high_unit = (match.group("high_unit") or "").lower()
        unit = high_unit or low_unit
        if unit in {"℃", "°c", "c"}:
            lower += 273.15
            upper += 273.15
        return lower, upper

    @classmethod
    def _extract_pressure(cls, message: str, default_pressure: float) -> float:
        match = cls.PRESSURE_PATTERN.search(message)
        if not match:
            return default_pressure

        value = float(match.group("value"))
        unit = match.group("unit").lower()
        multiplier = {
            "pa": 1.0,
            "kpa": 1_000.0,
            "mpa": 1_000_000.0,
            "gpa": 1_000_000_000.0,
            "bar": 100_000.0,
        }[unit]
        return value * multiplier

    @classmethod
    def _extract_step_size(cls, message: str, default_step: float) -> float:
        match = cls.STEP_PATTERN.search(message)
        if not match:
            return default_step
        return max(float(match.group("value")), 1.0)

    @classmethod
    def _has_explicit_system(cls, message: str, fallback: str) -> bool:
        return bool(cls.SYSTEM_PATTERN.search(message) or fallback.strip())

    @classmethod
    def _has_explicit_temperature_range(cls, message: str) -> bool:
        return bool(cls.RANGE_PATTERN.search(message))

    @classmethod
    def _has_explicit_pressure(cls, message: str) -> bool:
        return bool(cls.PRESSURE_PATTERN.search(message))

    @classmethod
    def _has_explicit_step_size(cls, message: str) -> bool:
        return bool(cls.STEP_PATTERN.search(message))

    @staticmethod
    def _deduce_diagram_type(message: str, fallback: str) -> str:
        lowered = message.lower()
        if "ternary" in lowered or "三元" in lowered:
            return "ternary"
        return "binary" if ("binary" in lowered or "二元" in lowered or fallback == "binary") else fallback

    def _call_json_llm(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any] | None:
        if not self.llm_client.is_configured():
            return None
        return self.llm_client.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=0.1,
        )

    @staticmethod
    def _safe_float(value: Any, default: float, *, minimum: float | None = None) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(parsed, minimum)
        return parsed

    @classmethod
    def _safe_probability(cls, value: Any, default: float) -> float:
        parsed = cls._safe_float(value, default)
        return max(0.0, min(parsed, 1.0))

    def infer_request_from_chat(self, message: str, overrides: dict[str, Any]) -> tuple[DiagramRequest, dict[str, Any]]:
        override_temperature_min = float(overrides.get("temperature_min", self.DEFAULT_TEMPERATURE_MIN))
        override_temperature_max = float(overrides.get("temperature_max", self.DEFAULT_TEMPERATURE_MAX))
        override_pressure = float(overrides.get("pressure", self.DEFAULT_PRESSURE))
        override_step_size = float(overrides.get("step_size", self.DEFAULT_STEP_SIZE))
        fallback_min, fallback_max = self._extract_temperature_range(
            message,
            override_temperature_min,
            override_temperature_max,
        )
        fallback_request = DiagramRequest(
            system_name=self._extract_system_name(message, str(overrides.get("system_name") or "")),
            diagram_type=self._deduce_diagram_type(message, str(overrides.get("diagram_type") or "binary")),
            temperature_min=fallback_min,
            temperature_max=fallback_max,
            pressure=self._extract_pressure(message, override_pressure),
            step_size=self._extract_step_size(message, override_step_size),
            notes=(str(overrides.get("notes") or "").strip() or message.strip()),
        )
        explicit_system = self._has_explicit_system(message, str(overrides.get("system_name") or ""))
        explicit_temperature_range = self._has_explicit_temperature_range(message) or (
            override_temperature_min != self.DEFAULT_TEMPERATURE_MIN or override_temperature_max != self.DEFAULT_TEMPERATURE_MAX
        )
        explicit_pressure = self._has_explicit_pressure(message) or override_pressure != self.DEFAULT_PRESSURE
        explicit_step_size = self._has_explicit_step_size(message) or override_step_size != self.DEFAULT_STEP_SIZE
        explicit_diagram_type = any(token in message.lower() for token in ("binary", "ternary", "二元", "三元")) or bool(
            str(overrides.get("diagram_type") or "").strip()
        )

        if self.llm_client.is_configured():
            try:
                llm_payload = self._call_json_llm(
                    system_prompt="Return conservative JSON for a binary phase diagram request. JSON only.",
                    user_prompt=(
                        "Return JSON with keys: system_name, diagram_type, temperature_min, temperature_max, pressure, step_size, notes, confidence.\n"
                        f"User message:\n{message}\n\n"
                        f"Caller defaults:\n{json.dumps(overrides, ensure_ascii=False)}"
                    ),
                    max_tokens=700,
                )
                if llm_payload:
                    candidate = DiagramRequest(
                        system_name=str(llm_payload.get("system_name") or fallback_request.system_name).strip() or fallback_request.system_name,
                        diagram_type=(
                            str(llm_payload.get("diagram_type") or fallback_request.diagram_type)
                            if str(llm_payload.get("diagram_type") or fallback_request.diagram_type) in {"binary", "ternary"}
                            else fallback_request.diagram_type
                        ),
                        temperature_min=self._safe_float(llm_payload.get("temperature_min"), fallback_request.temperature_min),
                        temperature_max=self._safe_float(llm_payload.get("temperature_max"), fallback_request.temperature_max),
                        pressure=self._safe_float(llm_payload.get("pressure"), fallback_request.pressure),
                        step_size=self._safe_float(llm_payload.get("step_size"), fallback_request.step_size, minimum=1.0),
                        notes=str(llm_payload.get("notes") or fallback_request.notes).strip() or fallback_request.notes,
                    )
                    if explicit_system:
                        candidate = candidate.model_copy(update={"system_name": fallback_request.system_name})
                    if explicit_diagram_type:
                        candidate = candidate.model_copy(update={"diagram_type": fallback_request.diagram_type})
                    if explicit_temperature_range:
                        candidate = candidate.model_copy(
                            update={
                                "temperature_min": fallback_request.temperature_min,
                                "temperature_max": fallback_request.temperature_max,
                            }
                        )
                    if explicit_pressure:
                        candidate = candidate.model_copy(update={"pressure": fallback_request.pressure})
                    if explicit_step_size:
                        candidate = candidate.model_copy(update={"step_size": fallback_request.step_size})
                    confidence = self._safe_probability(llm_payload.get("confidence"), 0.82)
                    return candidate, {
                        "source": "llm_request_interpreter",
                        "confidence": confidence,
                        "message": message,
                    }
                if settings.require_llm_for_agents:
                    raise LLMRequiredError("PhaseDiagramAgent 需要 LLM 解析结构化相图请求，但本次没有得到有效 JSON。")
            except (RuntimeError, ValueError) as exc:
                if settings.require_llm_for_agents:
                    raise LLMRequiredError(f"PhaseDiagramAgent 在解析生成请求时调用 LLM 失败：{exc}") from exc
        elif settings.require_llm_for_agents:
            self.llm_client.require_configured(agent_name="PhaseDiagramAgent", capability="相图请求解析")

        return fallback_request, {
            "source": "heuristic_request_interpreter",
            "confidence": 0.6,
            "message": message,
        }

    @staticmethod
    def _build_code_snapshot(generated_code: str) -> str:
        lines = generated_code.splitlines()
        return "\n".join(lines[:80])

    @staticmethod
    def _extract_relevant_html_text(html_content: str) -> str:
        without_scripts = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", html_content, flags=re.IGNORECASE)
        without_styles = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", without_scripts, flags=re.IGNORECASE)
        without_tags = re.sub(r"<[^>]+>", " ", without_styles)
        return re.sub(r"\s+", " ", without_tags).strip()

    @staticmethod
    def _term_in_text(card_text: str, term: str) -> bool:
        lowered = card_text.lower()
        normalized_term = normalize_system_key(term)
        if not normalized_term:
            return term.lower() in lowered
        if any(ord(character) > 127 for character in term):
            return term in card_text
        boundary_pattern = rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])"
        if re.search(boundary_pattern, lowered):
            return True
        term_with_spaces = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
        if term_with_spaces and re.search(rf"(?<![a-z0-9]){re.escape(term_with_spaces)}(?![a-z0-9])", re.sub(r"[^a-z0-9]+", " ", lowered)):
            return True
        return False

    @classmethod
    def _contains_all_required_terms(cls, card_text: str, groups: tuple[tuple[str, ...], ...]) -> list[str]:
        missing: list[str] = []
        for group in groups:
            if not any(cls._term_in_text(card_text, term) for term in group):
                missing.append("/".join(group))
        return missing

    @classmethod
    def _contains_forbidden_terms(cls, card_text: str, groups: tuple[tuple[str, ...], ...]) -> list[str]:
        found: list[str] = []
        for group in groups:
            if any(cls._term_in_text(card_text, term) for term in group):
                found.append("/".join(group))
        return found

    def review_generated_artifact(
        self,
        request: DiagramRequest,
        generated_code: str,
        html_content: str,
        stdout: str,
        stderr: str,
    ) -> dict[str, Any]:
        _, preflight_issues = self.codegen_service.sanitize_and_validate_code(request, generated_code)
        blocking_issues = list(preflight_issues)
        advisory_issues: list[str] = []

        lowered_html = html_content.lower()
        if "phase-diagram-agent-layout" not in lowered_html:
            blocking_issues.append("Generated HTML is missing the phase-diagram-agent-layout marker.")
        if "phase-diagram-agent-result" not in lowered_html:
            blocking_issues.append("Generated HTML is missing the standardized phase-diagram-agent-result root container.")
        if stderr.strip():
            blocking_issues.append("Execution still produced stderr output; this run should not be trusted.")

        calculated_card = get_calculated_binary_card(request.system_name)
        combined_text = f"{generated_code}\n{self._extract_relevant_html_text(html_content)}\n{stdout}"
        if calculated_card is None:
            blocking_issues.append("The requested system is not registered in the thermodynamic database registry.")
        else:
            if "build_calculated_phase_diagram_report" not in generated_code:
                blocking_issues.append("Database-backed systems must be generated through build_calculated_phase_diagram_report.")
            if "pycalphad_tdb_database" not in html_content:
                blocking_issues.append("The final HTML does not declare pycalphad_tdb_database as the model source.")
            if "tdb_equilibrium_calculation" not in html_content:
                blocking_issues.append("The final HTML does not declare tdb_equilibrium_calculation as the model mode.")
            if calculated_card.database_name.lower() not in html_content.lower():
                blocking_issues.append(f"The final HTML does not mention the expected database file {calculated_card.database_name}.")
            for phase_name in calculated_card.phases:
                if phase_name.lower() not in combined_text.lower():
                    blocking_issues.append(f"The generated result is missing the calculated phase label {phase_name}.")

        review_mode = "heuristic_guardrail"
        llm_summary = ""
        llm_confidence: float | None = None
        if self.llm_client.is_configured():
            knowledge_hint = build_calculated_prompt_hint(request.system_name) or "(none)"
            try:
                llm_payload = self._call_json_llm(
                    system_prompt=(
                        "You are reviewing an agent run that should generate code as a thin wrapper over a local pycalphad + TDB equilibrium calculation helper. "
                        "Return JSON only."
                    ),
                    user_prompt=(
                        f"Request: {request.model_dump_json()}\n"
                        f"Knowledge card: {knowledge_hint}\n"
                        f"stdout: {stdout[:1200]}\n"
                        f"stderr: {stderr[:1200]}\n"
                        f"code:\n{self._build_code_snapshot(generated_code)}\n"
                        "Return JSON with keys: summary, confidence, passed, blocking_issues, advisory_issues.\n"
                        "Blocking issues should only be used for: wrong system, wrong execution mode, missing required phase names, missing database markers, or broken HTML/output contract."
                    ),
                    max_tokens=700,
                )
                if llm_payload:
                    review_mode = "llm_plus_heuristic_guardrail"
                    llm_summary = str(llm_payload.get("summary") or "").strip()
                    try:
                        llm_confidence = self._safe_probability(llm_payload.get("confidence"), 0.75)
                    except (TypeError, ValueError):
                        llm_confidence = None
                    for issue in llm_payload.get("blocking_issues", []) if isinstance(llm_payload.get("blocking_issues"), list) else []:
                        text = str(issue).strip()
                        if text and text not in blocking_issues:
                            blocking_issues.append(text)
                    for issue in llm_payload.get("advisory_issues", []) if isinstance(llm_payload.get("advisory_issues"), list) else []:
                        text = str(issue).strip()
                        if text and text not in advisory_issues:
                            advisory_issues.append(text)
                    if llm_payload.get("passed") is False and not llm_payload.get("blocking_issues"):
                        summary_text = llm_summary or "LLM reviewer rejected the run."
                        if summary_text not in blocking_issues:
                            blocking_issues.append(summary_text)
                elif settings.require_llm_for_agents:
                    raise LLMRequiredError("PhaseDiagramAgent 需要 LLM 审查结果，但本次没有得到有效 JSON。")
            except RuntimeError as exc:
                if settings.require_llm_for_agents:
                    raise LLMRequiredError(f"PhaseDiagramAgent 在结果审查阶段调用 LLM 失败：{exc}") from exc
        elif settings.require_llm_for_agents:
            self.llm_client.require_configured(agent_name="PhaseDiagramAgent", capability="结果自检与审查")

        passed = not blocking_issues
        confidence = max(0.2, 0.94 - 0.16 * len(blocking_issues) - 0.04 * len(advisory_issues))
        if llm_confidence is not None:
            confidence = min(confidence, llm_confidence) if blocking_issues else max(confidence, llm_confidence)

        summary = llm_summary or (
            "Agent review passed. The generated code used the local pycalphad/TDB calculation helper and the artifact matches the requested system."
            if passed
            else f"Agent review found {len(blocking_issues)} blocking issue(s) in the wrapper code or generated artifact."
        )
        if passed and advisory_issues:
            summary = f"{summary} 另外保留 {len(advisory_issues)} 条建议性提醒。"

        return {
            "passed": passed,
            "summary": summary,
            "confidence": round(confidence, 2),
            "issues": blocking_issues,
            "advisory_issues": advisory_issues,
            "review_mode": review_mode,
        }

    def lookup_registered_database(
        self,
        system_name: str,
        *,
        query_text: str = "",
        use_rag: bool | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        card, retrieval = retrieve_thermo_database(system_name)
        if card is None:
            rag_enabled = settings.thermo_rag_enabled if use_rag is None else use_rag
            if rag_enabled:
                rag_query = query_text.strip() or system_name
                rag_card, rag_retrieval = ThermoRagService.retrieve(rag_query)
                if rag_card is not None:
                    return rag_card, {
                        **rag_retrieval,
                        "matched": True,
                        "query": rag_query,
                        "lookup_mode": "exact_then_rag",
                    }
                return None, {
                    **retrieval,
                    "lookup_mode": "exact_then_rag",
                    "rag": rag_retrieval,
                }
            return None, retrieval
        return card.public_payload(), {
            **retrieval,
            "lookup_mode": "exact",
            "selection_strategy": "exact_registry_match",
        }
