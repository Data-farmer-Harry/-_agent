from __future__ import annotations

import json
from dataclasses import dataclass

from app.schemas import AgentRunRequest, TaskRouteName, WorkspaceId
from app.services.llm_client import LLMClient


REDRAW_KEYWORDS = (
    "讲解",
    "解释",
    "总结",
    "汇报",
    "组会",
    "html",
    "页面",
    "网页",
    "重绘",
    "重画",
    "redraw",
    "report",
    "slide",
    "presentation",
)

RECOGNIZE_KEYWORDS = (
    "识别",
    "解析",
    "提取",
    "ocr",
    "读图",
    "recognize",
    "analyze image",
    "extract",
)

PHASE_DIAGRAM_KEYWORDS = (
    "相图",
    "phase diagram",
    "liquidus",
    "solidus",
    "binodal",
    "ternary",
    "binary",
)

LAMMPS_KEYWORDS = (
    "lammps",
    "molecular dynamics",
    "md simulation",
    "atomistic",
    "势函数",
    "分子动力学",
)


@dataclass(frozen=True)
class AgentDecision:
    route_name: TaskRouteName
    workspace_id: WorkspaceId
    intent: str
    reason: str
    source: str
    confidence: float


class AgentDecisionService:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _normalized_text(request: AgentRunRequest) -> str:
        segments = [
            request.user_input,
            request.task_type_hint or "",
            request.diagram_request.system_name if request.diagram_request else "",
            request.diagram_request.notes if request.diagram_request else "",
            request.image_diagram_request.system_name if request.image_diagram_request else "",
            request.image_diagram_request.chart_title if request.image_diagram_request else "",
            request.image_diagram_request.notes if request.image_diagram_request else "",
            request.html_redraw_request.system_name if request.html_redraw_request else "",
            request.html_redraw_request.chart_title if request.html_redraw_request else "",
            request.html_redraw_request.notes if request.html_redraw_request else "",
            request.html_redraw_request.message if request.html_redraw_request else "",
        ]
        return " ".join(segment for segment in segments if segment).lower()

    def _fallback_decision(self, request: AgentRunRequest) -> AgentDecision:
        text = self._normalized_text(request)
        has_image = request.image_diagram_request is not None or bool(request.html_redraw_request and request.html_redraw_request.image_data_url)

        if request.workspace_hint == "lammps" or any(keyword in text for keyword in LAMMPS_KEYWORDS):
            return AgentDecision(
                route_name="lammps.generate",
                workspace_id="lammps",
                intent="lammps_stub",
                reason="Detected simulation-oriented language, so the agent routed the request to the LAMMPS stub workspace.",
                source="heuristic_agent_decider",
                confidence=0.71,
            )

        if has_image and any(keyword in text for keyword in REDRAW_KEYWORDS):
            return AgentDecision(
                route_name="phase_diagram.redraw_html",
                workspace_id="phase_diagram",
                intent="html_redraw",
                reason="Detected an image-backed explanation or presentation request, so the agent chose HTML redraw instead of Python execution.",
                source="heuristic_agent_decider",
                confidence=0.76,
            )

        if has_image:
            return AgentDecision(
                route_name="phase_diagram.recognize",
                workspace_id="phase_diagram",
                intent="image_recognition",
                reason="Detected an uploaded phase-diagram image, so the agent chose multimodal recognition as the safest default path.",
                source="heuristic_agent_decider",
                confidence=0.72,
            )

        if any(keyword in text for keyword in REDRAW_KEYWORDS):
            return AgentDecision(
                route_name="phase_diagram.redraw_html",
                workspace_id="phase_diagram",
                intent="html_redraw",
                reason="Detected a phase-diagram explanation/presentation request, so the agent chose HTML redraw.",
                source="heuristic_agent_decider",
                confidence=0.74,
            )

        if request.diagram_request is not None or request.workspace_hint == "phase_diagram" or any(keyword in text for keyword in PHASE_DIAGRAM_KEYWORDS):
            return AgentDecision(
                route_name="phase_diagram.generate",
                workspace_id="phase_diagram",
                intent="python_generation",
                reason="Detected a phase-diagram request and selected Python code generation plus local execution.",
                source="heuristic_agent_decider",
                confidence=0.68,
            )

        return AgentDecision(
            route_name="generic.unknown",
            workspace_id="generic",
            intent="unsupported",
            reason="The request could not be mapped onto a supported materials-agent tool chain.",
            source="heuristic_agent_decider",
            confidence=0.35,
        )

    def decide(self, request: AgentRunRequest) -> AgentDecision:
        explicit_route = (request.task_type_hint or "").strip()
        if explicit_route in {
            "phase_diagram.generate",
            "phase_diagram.recognize",
            "phase_diagram.redraw_html",
            "phase_diagram.from_image",
            "phase_diagram.repair",
            "lammps.generate",
            "lammps.repair",
            "materials.lookup",
            "materials.compare",
            "materials.analysis",
            "generic.unknown",
        }:
            workspace_id: WorkspaceId = "phase_diagram"
            intent = "explicit"
            reason = "Using the explicit task_type_hint supplied by the caller."
            if explicit_route.startswith("lammps."):
                workspace_id = "lammps"
                intent = "lammps_stub"
                reason = "Using explicit LAMMPS task_type_hint from the request; the stub router is available while execution tools remain reserved."
            elif explicit_route == "generic.unknown":
                workspace_id = "generic"
                intent = "unsupported"
            elif explicit_route.startswith("materials."):
                workspace_id = "generic"
                intent = "explicit"
            elif explicit_route in {"phase_diagram.recognize", "phase_diagram.from_image"}:
                intent = "image_recognition"
                reason = "Using explicit phase-diagram task_type_hint from the request."
            elif explicit_route == "phase_diagram.redraw_html":
                intent = "html_redraw"
                reason = "Using explicit phase-diagram task_type_hint from the request."
            else:
                intent = "python_generation" if explicit_route == "phase_diagram.generate" else "legacy_route"
                reason = "Using explicit phase-diagram task_type_hint from the request."
            return AgentDecision(
                route_name=explicit_route,
                workspace_id=workspace_id,
                intent=intent,
                reason=reason,
                source="explicit_task_type_hint",
                confidence=0.99,
            )

        if self.llm_client.is_configured():
            try:
                payload = self.llm_client.chat_json(
                    system_prompt="You are a materials-research agent planner. Return JSON only.",
                    user_prompt=(
                        "Choose exactly one route for the user's request.\n"
                        "Allowed routes:\n"
                        "- phase_diagram.generate: write Python code, run local Python, and review the generated artifact.\n"
                        "- phase_diagram.recognize: inspect an uploaded phase-diagram image and return conservative structured recognition.\n"
                        "- phase_diagram.redraw_html: create an HTML page that redraws or explains a phase diagram from textual or image context.\n"
                        "- lammps.generate: reserve the request for the future LAMMPS tool chain.\n"
                        "- generic.unknown: unsupported.\n\n"
                        "Return JSON with keys: route_name, intent, reason, confidence.\n"
                        f"Request summary:\n{json.dumps({'user_input': request.user_input, 'has_image': bool(request.image_diagram_request or (request.html_redraw_request and request.html_redraw_request.image_data_url)), 'workspace_hint': request.workspace_hint, 'diagram_request': request.diagram_request.model_dump() if request.diagram_request else {}, 'image_diagram_request': request.image_diagram_request.model_dump() if request.image_diagram_request else {}, 'html_redraw_request': request.html_redraw_request.model_dump() if request.html_redraw_request else {}}, ensure_ascii=False)}"
                    ),
                    max_tokens=900,
                )
                if payload:
                    route_name = str(payload.get("route_name") or "").strip()
                    if route_name in {
                        "phase_diagram.generate",
                        "phase_diagram.recognize",
                        "phase_diagram.redraw_html",
                        "lammps.generate",
                        "generic.unknown",
                    }:
                        workspace_id: WorkspaceId = "phase_diagram"
                        if route_name.startswith("lammps."):
                            workspace_id = "lammps"
                        elif route_name == "generic.unknown":
                            workspace_id = "generic"
                        confidence = payload.get("confidence", 0.82)
                        try:
                            normalized_confidence = max(0.0, min(float(confidence), 1.0))
                        except (TypeError, ValueError):
                            normalized_confidence = 0.82
                        return AgentDecision(
                            route_name=route_name,
                            workspace_id=workspace_id,
                            intent=str(payload.get("intent") or "").strip() or "agent_selected",
                            reason=str(payload.get("reason") or "").strip() or "The LLM selected the route that best matches the current research task.",
                            source="llm_agent_decider",
                            confidence=normalized_confidence,
                        )
            except RuntimeError:
                pass

        return self._fallback_decision(request)
