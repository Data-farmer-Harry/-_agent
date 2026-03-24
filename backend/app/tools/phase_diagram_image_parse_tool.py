from __future__ import annotations

from app.schemas import ArtifactRef, ImageDiagramRequest
from app.services.phase_diagram_image_service import PhaseDiagramImageService
from app.tools.base import BaseTool, ToolExecutionResult


class PhaseDiagramImageParseTool(BaseTool):
    name = "phase_diagram_image_parse"
    description = "Parse an uploaded phase-diagram screenshot into a calibrated structured spec"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.recognize", "phase_diagram.from_image")
    tags = ("phase-diagram", "image", "vision")
    produces_artifacts = ("json",)
    consumes = ("image_diagram_request",)

    def __init__(self, image_service: PhaseDiagramImageService) -> None:
        self.image_service = image_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        request = ImageDiagramRequest.model_validate(input_data["image_diagram_request"])
        spec, prompt = self.image_service.analyze_image(request)
        return ToolExecutionResult(
            success=True,
            summary=f"Built a structured image spec using {spec.detection_mode.replace('_', ' ')} mode.",
            output={
                "image_spec": spec.model_dump(),
                "prompt": prompt,
                "detection_mode": spec.detection_mode,
                "confidence": spec.confidence,
                "summary": spec.summary,
            },
            artifacts=[ArtifactRef(kind="json", name="image_spec.json", content=spec.model_dump_json(indent=2))],
            metadata={
                "detection_mode": spec.detection_mode,
                "confidence": spec.confidence,
            },
            state_delta={
                "image_spec_updated": True,
                "prompt_updated": True,
            },
        )
