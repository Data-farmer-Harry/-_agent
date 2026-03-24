from __future__ import annotations

from app.schemas import ArtifactRef, HtmlRedrawRequest
from app.services.artifact_service import ArtifactService
from app.services.phase_diagram_html_service import PhaseDiagramHtmlService
from app.tools.base import BaseTool, ToolExecutionResult
from app.utils.file_utils import write_text_file


class PhaseDiagramHtmlRedrawTool(BaseTool):
    name = "phase_diagram_html_redraw"
    description = "Generate an HTML phase-diagram explanation/redraw page from text or image context"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.redraw_html",)
    tags = ("phase-diagram", "html", "redraw")
    produces_artifacts = ("html",)
    consumes = ("html_redraw_request",)

    def __init__(self, artifact_service: ArtifactService, html_service: PhaseDiagramHtmlService) -> None:
        self.artifact_service = artifact_service
        self.html_service = html_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        request = HtmlRedrawRequest.model_validate(input_data["html_redraw_request"])
        html_content, prompt, generation_source = self.html_service.generate_html(request)
        html_path = self.artifact_service.get_result_path(context["run_id"])
        write_text_file(html_path, html_content)
        return ToolExecutionResult(
            success=True,
            summary="Generated an HTML redraw/explanation page for the requested phase diagram context.",
            output={
                "html_content": html_content,
                "html_path": str(html_path),
                "prompt": prompt,
                "generation_source": generation_source,
            },
            artifacts=[ArtifactRef(kind="html", name="result.html", path=str(html_path), content=html_content)],
            metadata={
                "generation_source": generation_source,
                "generator": "phase_diagram_html_redraw",
            },
            state_delta={
                "html_updated": True,
                "prompt_updated": True,
            },
        )
