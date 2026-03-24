from __future__ import annotations

from app.schemas import ArtifactRef
from app.services.executor_service import LocalPythonExecutor
from app.tools.base import BaseTool, ToolExecutionResult


class PythonExecuteTool(BaseTool):
    name = "python_execute"
    description = "Execute generated Python code inside a run workspace"
    workspace_id = "phase_diagram"
    supports_routes = ("phase_diagram.generate", "phase_diagram.repair")
    tags = ("phase-diagram", "execution", "python")
    produces_artifacts = ("html",)
    consumes = ("generated_code",)

    def __init__(self, executor: LocalPythonExecutor) -> None:
        self.executor = executor

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        run_id = context["run_id"]
        generated_code = input_data["generated_code"]
        result = self.executor.execute(run_id=run_id, code=generated_code)
        artifacts: list[ArtifactRef] = []
        if result.html_content is not None and result.html_path is not None:
            artifacts.append(
                ArtifactRef(kind="html", name="result.html", path=result.html_path, content=result.html_content)
            )
        return ToolExecutionResult(
            success=result.success,
            summary="Executed generated Python code.",
            output=result.to_dict(),
            artifacts=artifacts,
            metadata={"executor": "local_python"},
            state_delta={
                "stdout_updated": True,
                "stderr_updated": True,
                "html_updated": bool(result.html_content and result.html_path),
            },
        )
