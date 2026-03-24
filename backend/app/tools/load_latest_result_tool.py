from __future__ import annotations

from app.schemas import ArtifactRef
from app.services.artifact_service import ArtifactService
from app.tools.base import BaseTool, ToolExecutionResult


class LoadLatestResultTool(BaseTool):
    name = "load_latest_html_artifact"
    description = "Load the latest successful HTML artifact"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.generate", "phase_diagram.from_image", "phase_diagram.repair")
    tags = ("phase-diagram", "artifact", "html")
    produces_artifacts = ("html",)

    def __init__(self, artifact_service: ArtifactService) -> None:
        self.artifact_service = artifact_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        html_content = self.artifact_service.load_latest_html()
        if html_content is None:
            return ToolExecutionResult(success=False, summary="No latest HTML artifact found.", output={})
        artifact = ArtifactRef(kind="html", name="latest_result.html", path=str(self.artifact_service.latest_result_path), content=html_content)
        return ToolExecutionResult(
            success=True,
            summary="Loaded latest HTML artifact.",
            output={"html_content": html_content, "html_path": str(self.artifact_service.latest_result_path)},
            artifacts=[artifact],
            metadata={"source": "latest_result"},
        )
