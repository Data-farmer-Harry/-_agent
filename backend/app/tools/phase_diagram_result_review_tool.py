from __future__ import annotations

import json

from app.schemas import ArtifactRef, DiagramRequest, HtmlRedrawRequest
from app.services.phase_diagram_agent_service import PhaseDiagramAgentService
from app.services.phase_diagram_html_service import PhaseDiagramHtmlService
from app.tools.base import BaseTool, ToolExecutionResult


class PhaseDiagramResultReviewTool(BaseTool):
    name = "phase_diagram_result_review"
    description = "Review generated phase-diagram artifacts before accepting the result"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.generate", "phase_diagram.repair", "phase_diagram.redraw_html")
    tags = ("phase-diagram", "review", "quality")
    produces_artifacts = ("json", "text")
    consumes = ("diagram_request", "html_redraw_request", "generated_code", "html_content", "stdout", "stderr")

    def __init__(self, phase_agent_service: PhaseDiagramAgentService, html_service: PhaseDiagramHtmlService) -> None:
        self.phase_agent_service = phase_agent_service
        self.html_service = html_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        route_name = str(context.get("route_name") or "")
        has_generated_code = bool(str(input_data.get("generated_code") or "").strip())
        has_diagram_request = bool(input_data.get("diagram_request"))

        if route_name == "phase_diagram.redraw_html" and input_data.get("html_redraw_request"):
            request = HtmlRedrawRequest.model_validate(input_data["html_redraw_request"])
            review = self.html_service.review_redraw_artifact(request=request, html_content=input_data.get("html_content", ""))
        elif has_generated_code or has_diagram_request:
            request = DiagramRequest.model_validate(input_data["diagram_request"])
            review = self.phase_agent_service.review_generated_artifact(
                request=request,
                generated_code=input_data.get("generated_code", ""),
                html_content=input_data.get("html_content", ""),
                stdout=input_data.get("stdout", ""),
                stderr=input_data.get("stderr", ""),
            )
        else:
            request = HtmlRedrawRequest.model_validate(input_data["html_redraw_request"])
            review = self.html_service.review_redraw_artifact(request=request, html_content=input_data.get("html_content", ""))
        summary_lines = [review["summary"], f"Confidence: {review['confidence']}", f"Mode: {review['review_mode']}"]
        if review["issues"]:
            summary_lines.append("Issues:")
            summary_lines.extend(f"- {issue}" for issue in review["issues"])

        return ToolExecutionResult(
            success=bool(review["passed"]),
            summary=review["summary"],
            output={
                "review_passed": review["passed"],
                "review_summary": review["summary"],
                "review_confidence": review["confidence"],
                "review_issues": review["issues"],
                "review_mode": review["review_mode"],
            },
            artifacts=[
                ArtifactRef(kind="json", name="review_report.json", content=json.dumps(review, ensure_ascii=False, indent=2)),
                ArtifactRef(kind="text", name="review_summary.txt", content="\n".join(summary_lines)),
            ],
            metadata={
                "review_mode": review["review_mode"],
                "review_passed": review["passed"],
            },
            state_delta={
                "review_updated": True,
                "review_passed": bool(review["passed"]),
            },
        )
