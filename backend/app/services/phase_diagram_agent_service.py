from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.schemas import DiagramRequest
from app.services.codegen_service import CodeGenerationService
from app.services.llm_client import LLMClient


class PhaseDiagramAgentService:
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")
    RANGE_PATTERN = re.compile(
        r"(?P<low>\d+(?:\.\d+)?)\s*(?:-|~|到|至|to)\s*(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>k|K|℃|°C|°c|c|C)?"
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

        unit = (match.group("unit") or "").lower()
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

    @staticmethod
    def _deduce_diagram_type(message: str, fallback: str) -> str:
        lowered = message.lower()
        if any(keyword in lowered for keyword in ("ternary", "三元")):
            return "ternary"
        if any(keyword in lowered for keyword in ("binary", "二元")):
            return "binary"
        return fallback

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any] | None:
        stripped = content.strip()
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", stripped, flags=re.IGNORECASE)
        candidate = fenced.group(1) if fenced else stripped
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

    def _call_json_llm(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any] | None:
        if not self.llm_client.is_configured():
            return None
        return self.llm_client.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=0.1,
        )

    def infer_request_from_chat(
        self,
        message: str,
        overrides: dict[str, Any],
    ) -> tuple[DiagramRequest, dict[str, Any]]:
        fallback_request = DiagramRequest(
            system_name=self._extract_system_name(message, str(overrides.get("system_name") or "")),
            diagram_type=self._deduce_diagram_type(message, str(overrides.get("diagram_type") or "binary")),
            temperature_min=self._extract_temperature_range(message, float(overrides.get("temperature_min", 300.0)), float(overrides.get("temperature_max", 1800.0)))[0],
            temperature_max=self._extract_temperature_range(message, float(overrides.get("temperature_min", 300.0)), float(overrides.get("temperature_max", 1800.0)))[1],
            pressure=self._extract_pressure(message, float(overrides.get("pressure", 101325.0))),
            step_size=self._extract_step_size(message, float(overrides.get("step_size", 50.0))),
            notes=(str(overrides.get("notes") or "").strip() or message.strip()),
        )

        if settings.llm_api_base_url and settings.llm_api_key:
            try:
                llm_payload = self._call_json_llm(
                    system_prompt="You convert materials-agent chat requests into conservative JSON. Return JSON only.",
                    user_prompt=(
                        "Read the user's materials-agent request and return JSON with keys: "
                        "system_name, diagram_type, temperature_min, temperature_max, pressure, step_size, notes, confidence.\n"
                        f"User message:\n{message}\n\n"
                        f"Caller defaults:\n{json.dumps(overrides, ensure_ascii=False)}"
                    ),
                    max_tokens=800,
                )
                if llm_payload:
                    candidate = DiagramRequest(
                        system_name=str(llm_payload.get("system_name") or fallback_request.system_name).strip() or fallback_request.system_name,
                        diagram_type=str(llm_payload.get("diagram_type") or fallback_request.diagram_type),
                        temperature_min=float(llm_payload.get("temperature_min", fallback_request.temperature_min)),
                        temperature_max=float(llm_payload.get("temperature_max", fallback_request.temperature_max)),
                        pressure=float(llm_payload.get("pressure", fallback_request.pressure)),
                        step_size=max(float(llm_payload.get("step_size", fallback_request.step_size)), 1.0),
                        notes=str(llm_payload.get("notes") or fallback_request.notes).strip() or fallback_request.notes,
                    )
                    confidence = float(llm_payload.get("confidence", 0.78))
                    return candidate, {
                        "source": "llm_request_interpreter",
                        "confidence": max(0.0, min(confidence, 1.0)),
                        "message": message,
                    }
            except (RuntimeError, ValueError):
                pass

        return fallback_request, {
            "source": "heuristic_request_interpreter",
            "confidence": 0.58,
            "message": message,
        }

    def review_generated_artifact(
        self,
        request: DiagramRequest,
        generated_code: str,
        html_content: str,
        stdout: str,
        stderr: str,
    ) -> dict[str, Any]:
        _, quality_issues = self.codegen_service.sanitize_and_validate_code(request, generated_code)
        issues = list(quality_issues)
        lowered_html = html_content.lower()

        if "phase-diagram-agent-layout" not in lowered_html:
            issues.append("Generated HTML is missing the structured layout marker phase-diagram-agent-layout.")
        if "phase-diagram-agent-result" not in lowered_html and "normalized-page-shell" not in lowered_html:
            issues.append("Generated HTML is missing the standardized root container for the result page.")
        if stderr.strip():
            issues.append("Execution still produced stderr output; the artifact should be treated as untrusted until repaired.")

        system_tokens = [token.lower() for token in re.split(r"[^A-Za-z]+", request.system_name) if token]
        if system_tokens and not any(token in lowered_html for token in system_tokens):
            issues.append("The final HTML does not clearly reference the requested material system.")

        review_mode = "heuristic_guardrail"
        llm_summary = ""
        llm_confidence: float | None = None
        should_call_llm_review = bool(settings.llm_api_base_url and settings.llm_api_key and len(generated_code) <= 5000)
        if should_call_llm_review:
            html_signals = {
                "has_layout_marker": "phase-diagram-agent-layout" in lowered_html,
                "has_standard_root": "phase-diagram-agent-result" in lowered_html or "normalized-page-shell" in lowered_html,
                "stderr_present": bool(stderr.strip()),
            }
            try:
                llm_payload = self._call_json_llm(
                    system_prompt="You are a cautious materials-agent reviewer. Return JSON only.",
                    user_prompt=(
                        "Review whether the generated phase-diagram artifact matches the requested system and looks structurally safe.\n"
                        f"Request: {request.model_dump_json()}\n"
                        f"HTML signals: {json.dumps(html_signals, ensure_ascii=False)}\n"
                        f"stdout: {stdout[:1200]}\n"
                        f"stderr: {stderr[:1200]}\n"
                        f"code:\n{generated_code[:7000]}\n"
                        "Return JSON with keys: summary, confidence, issues."
                    ),
                    max_tokens=900,
                )
                if llm_payload:
                    review_mode = "llm_plus_heuristic_guardrail"
                    llm_summary = str(llm_payload.get("summary") or "").strip()
                    try:
                        llm_confidence = max(0.0, min(float(llm_payload.get("confidence", 0.72)), 1.0))
                    except (TypeError, ValueError):
                        llm_confidence = None
                    for raw_issue in llm_payload.get("issues", []):
                        issue = str(raw_issue).strip()
                        if issue and issue not in issues:
                            issues.append(issue)
            except RuntimeError:
                pass

        confidence = max(0.12, 0.92 - 0.17 * len(issues))
        if llm_confidence is not None:
            confidence = min(confidence, llm_confidence) if issues else max(confidence, llm_confidence)

        passed = not issues
        summary = (
            llm_summary
            or (
                "Agent review passed. The generated code and HTML contract look consistent with the requested phase diagram."
                if passed
                else f"Agent review found {len(issues)} issue(s) that should be repaired before trusting the artifact."
            )
        )

        return {
            "passed": passed,
            "summary": summary,
            "confidence": round(confidence, 2),
            "issues": issues,
            "review_mode": review_mode,
        }
