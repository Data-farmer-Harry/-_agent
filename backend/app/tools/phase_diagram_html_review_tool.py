from __future__ import annotations

import json

from app.schemas import ArtifactRef, HtmlRedrawRequest
from app.services.phase_diagram_html_service import PhaseDiagramHtmlService
from app.tools.base import BaseTool, ToolExecutionResult


class PhaseDiagramHtmlReviewTool(BaseTool):
    name = "phase_diagram_html_review"
    description = "Review a generated HTML redraw page before accepting it"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.redraw_html",)
    tags = ("phase-diagram", "html", "review")
    produces_artifacts = ("json", "text")
    consumes = ("html_redraw_request", "html_content")

    def __init__(self, html_service: PhaseDiagramHtmlService) -> None:
        self.html_service = html_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        request = HtmlRedrawRequest.model_validate(input_data["html_redraw_request"])
        review = self.html_service.review_redraw_artifact(request, input_data.get("html_content", ""))
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
                ArtifactRef(kind="json", name="html_review_report.json", content=json.dumps(review, ensure_ascii=False, indent=2)),
                ArtifactRef(kind="text", name="html_review_summary.txt", content="\n".join(summary_lines)),
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
