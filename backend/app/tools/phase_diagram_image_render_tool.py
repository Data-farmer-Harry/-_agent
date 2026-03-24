from __future__ import annotations

from app.schemas import ArtifactRef, ImageDiagramSpec
from app.services.artifact_service import ArtifactService
from app.services.phase_diagram_image_service import PhaseDiagramImageService
from app.tools.base import BaseTool, ToolExecutionResult
from app.utils.file_utils import write_text_file


class PhaseDiagramImageRenderTool(BaseTool):
    name = "phase_diagram_image_render"
    description = "Render a calibrated HTML page from a structured phase-diagram image spec"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.from_image",)
    tags = ("phase-diagram", "image", "render")
    produces_artifacts = ("html",)
    consumes = ("image_spec",)

    def __init__(self, artifact_service: ArtifactService, image_service: PhaseDiagramImageService) -> None:
        self.artifact_service = artifact_service
        self.image_service = image_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        spec = ImageDiagramSpec.model_validate(input_data["image_spec"])
        html_content = self.image_service.render_html(spec)
        html_path = self.artifact_service.get_result_path(context["run_id"])
        write_text_file(html_path, html_content)
        return ToolExecutionResult(
            success=True,
            summary="Rendered a calibrated HTML page from the uploaded phase-diagram screenshot.",
            output={
                "html_content": html_content,
                "html_path": str(html_path),
                "analysis_mode": spec.detection_mode,
                "confidence": spec.confidence,
                "summary": spec.summary,
            },
            artifacts=[ArtifactRef(kind="html", name="result.html", path=str(html_path), content=html_content)],
            metadata={
                "analysis_mode": spec.detection_mode,
                "confidence": spec.confidence,
            },
            state_delta={"html_updated": True},
        )
