from __future__ import annotations

from app.schemas import ArtifactRef, DiagramRequest
from app.services.codegen_service import CodeGenerationService
from app.tools.base import BaseTool, ToolExecutionResult


class PhaseDiagramCodegenTool(BaseTool):
    name = "phase_diagram_codegen"
    description = "Generate Python code for a phase diagram task"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.generate", "phase_diagram.repair")
    tags = ("phase-diagram", "codegen", "html")
    produces_artifacts = ("code",)
    consumes = ("diagram_request",)

    def __init__(self, codegen_service: CodeGenerationService) -> None:
        self.codegen_service = codegen_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        request = DiagramRequest.model_validate(input_data["diagram_request"])
        force_placeholder = bool(input_data.get("force_placeholder"))
        prompt = self.codegen_service.build_prompt(request)
        generation_source = "placeholder_forced"
        if force_placeholder:
            generated_code = self.codegen_service.build_placeholder_code(request)
        else:
            generated_code, generation_source = self.codegen_service.generate_code_with_source(request)
        artifact_name = "placeholder_code.py" if force_placeholder else "generated_code.py"
        summary = "Generated deterministic placeholder phase diagram code." if force_placeholder else "Generated phase diagram code."
        return ToolExecutionResult(
            success=True,
            summary=summary,
            output={"generated_code": generated_code, "prompt": prompt},
            artifacts=[ArtifactRef(kind="code", name=artifact_name, content=generated_code)],
            metadata={
                "generation_mode": "placeholder" if force_placeholder else "llm_or_placeholder",
                "force_placeholder": force_placeholder,
                "generation_source": generation_source,
            },
            state_delta={
                "generated_code_updated": True,
                "prompt_updated": True,
            },
        )
