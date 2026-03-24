from __future__ import annotations

from app.schemas import ArtifactRef, DiagramRequest
from app.services.codegen_service import CodeGenerationService
from app.tools.base import BaseTool, ToolExecutionResult


class PhaseDiagramRepairTool(BaseTool):
    name = "phase_diagram_repair"
    description = "Repair generated Python code using execution stderr"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.generate", "phase_diagram.repair")
    tags = ("phase-diagram", "repair", "codegen")
    produces_artifacts = ("code",)
    consumes = ("diagram_request", "generated_code", "stderr")

    def __init__(self, codegen_service: CodeGenerationService) -> None:
        self.codegen_service = codegen_service

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        request = DiagramRequest.model_validate(input_data["diagram_request"])
        generated_code = input_data["generated_code"]
        stderr = input_data.get("stderr", "")
        repaired_code = self.codegen_service.repair_code(request, generated_code, stderr)
        if not repaired_code:
            return ToolExecutionResult(success=False, summary="No repaired code was produced.", output={})
        repaired_code, quality_issues = self.codegen_service.sanitize_and_validate_code(request, repaired_code)
        if quality_issues:
            return ToolExecutionResult(
                success=False,
                summary="Repaired code still failed semantic validation.",
                output={"generated_code": repaired_code, "quality_issues": quality_issues},
                artifacts=[ArtifactRef(kind="code", name="repaired_code.py", content=repaired_code)],
                metadata={"quality_gate_passed": False},
                state_delta={"generated_code_updated": True, "repair_attempted": True},
            )
        return ToolExecutionResult(
            success=True,
            summary="Repaired generated code.",
            output={"generated_code": repaired_code},
            artifacts=[ArtifactRef(kind="code", name="repaired_code.py", content=repaired_code)],
            metadata={"quality_gate_passed": True},
            state_delta={"generated_code_updated": True, "repair_attempted": True},
        )
