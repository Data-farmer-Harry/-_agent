from __future__ import annotations

import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas import AgentRunRequest, ArtifactRef, DiagramRequest, ImageDiagramRequest
from app.services.agent_catalog import AgentCatalogService
from app.services.agent_decision_service import AgentDecisionService
from app.services.agent_chat_service import AgentChatService
from app.services.agent_runtime import AgentRuntime
from app.services.artifact_service import ArtifactService
from app.services.codegen_service import CodeGenerationService
from app.services.executor_service import LocalPythonExecutor
from app.services.phase_diagram_agent_service import PhaseDiagramAgentService
from app.services.phase_diagram_html_service import PhaseDiagramHtmlService
from app.services.phase_diagram_image_service import PhaseDiagramImageService
from app.services.planner_service import PlannerService
from app.services.prompt_builder import PromptBuilder
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry
from app.tools.base import BaseTool, ToolExecutionResult
from app.tools.lammps_command_router_tool import LammpsCommandRouterTool
from app.tools.load_latest_result_tool import LoadLatestResultTool
from app.tools.phase_diagram_codegen_tool import PhaseDiagramCodegenTool
from app.tools.phase_diagram_html_redraw_tool import PhaseDiagramHtmlRedrawTool
from app.tools.phase_diagram_html_review_tool import PhaseDiagramHtmlReviewTool
from app.tools.phase_diagram_image_parse_tool import PhaseDiagramImageParseTool
from app.tools.phase_diagram_image_render_tool import PhaseDiagramImageRenderTool
from app.tools.phase_diagram_repair_tool import PhaseDiagramRepairTool
from app.tools.phase_diagram_result_review_tool import PhaseDiagramResultReviewTool
from app.tools.python_execute_tool import PythonExecuteTool


def sample_request(system_name: str = "Fe-Cu", diagram_type: str = "binary") -> DiagramRequest:
    return DiagramRequest(
        system_name=system_name,
        diagram_type=diagram_type,
        temperature_min=300.0,
        temperature_max=1800.0,
        pressure=101325.0,
        step_size=50.0,
        notes="regression smoke test",
    )


def sample_image_request() -> ImageDiagramRequest:
    return ImageDiagramRequest(
        image_data_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP+2xqB9QAAAABJRU5ErkJggg==",
        filename="phase.png",
        system_name="Fe-C",
        chart_title="Fe-C calibrated screenshot",
        diagram_type="binary",
        x_axis={"label": "Composition (wt.% C)", "minimum": 0.0, "maximum": 6.7},
        y_axis={"label": "Temperature (°C)", "minimum": 0.0, "maximum": 1600.0},
        notes="prioritize temperature axis and title",
    )


class StaticTool(BaseTool):
    def __init__(self, name: str, description: str, handler):
        self.name = name
        self.description = description
        self._handler = handler

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        return self._handler(input_data, context)


class HtmlContractMixin:
    def assert_html_contract(self, html_content: str, *, title_fragment: str | None = None) -> None:
        self.assertIsInstance(html_content, str)
        self.assertRegex(html_content.lower(), r"<html[\s>]")
        self.assertIn("phase-diagram-agent-layout", html_content)
        self.assertTrue(
            "phase-diagram-agent-result" in html_content or "normalized-page-shell" in html_content,
            "Expected the standardized result container in generated HTML.",
        )
        if title_fragment:
            self.assertIn(title_fragment, html_content)


def build_default_runtime(root_dir: Path, *, include_lammps: bool = True) -> tuple[AgentRuntime, ArtifactService, ToolRegistry]:
    artifact_service = ArtifactService(root_dir=root_dir)
    prompt_builder = PromptBuilder()
    codegen_service = CodeGenerationService(prompt_builder=prompt_builder)
    phase_agent_service = PhaseDiagramAgentService(codegen_service=codegen_service)
    image_service = PhaseDiagramImageService()
    html_service = PhaseDiagramHtmlService()
    decision_service = AgentDecisionService()
    executor = LocalPythonExecutor(artifact_service=artifact_service, python_executable=sys.executable)
    registry = ToolRegistry()
    registry.register(PhaseDiagramCodegenTool(codegen_service=codegen_service))
    registry.register(PhaseDiagramHtmlRedrawTool(artifact_service=artifact_service, html_service=html_service))
    registry.register(PhaseDiagramHtmlReviewTool(html_service=html_service))
    registry.register(PhaseDiagramResultReviewTool(phase_agent_service=phase_agent_service, html_service=html_service))
    registry.register(PhaseDiagramImageParseTool(image_service=image_service))
    registry.register(PhaseDiagramImageRenderTool(artifact_service=artifact_service, image_service=image_service))
    registry.register(PhaseDiagramRepairTool(codegen_service=codegen_service))
    registry.register(PythonExecuteTool(executor=executor))
    registry.register(LoadLatestResultTool(artifact_service=artifact_service))
    if include_lammps:
        registry.register(LammpsCommandRouterTool())

    runtime = AgentRuntime(
        task_router=TaskRouter(catalog_service=AgentCatalogService(), decision_service=decision_service),
        planner_service=PlannerService(),
        tool_registry=registry,
        artifact_service=artifact_service,
    )
    return runtime, artifact_service, registry


def make_request(
    *,
    user_input: str,
    task_type_hint: str | None = None,
    diagram_request: DiagramRequest | None = None,
    image_diagram_request: ImageDiagramRequest | None = None,
    context: dict | None = None,
) -> AgentRunRequest:
    return AgentRunRequest(
        user_input=user_input,
        task_type_hint=task_type_hint,
        diagram_request=diagram_request,
        image_diagram_request=image_diagram_request,
        context=context or {},
    )


@contextmanager
def llm_disabled() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(patch("app.services.codegen_service.settings.llm_api_base_url", ""))
        stack.enter_context(patch("app.services.codegen_service.settings.llm_api_key", ""))
        stack.enter_context(patch("app.services.llm_client.settings.llm_api_base_url", ""))
        stack.enter_context(patch("app.services.llm_client.settings.llm_api_key", ""))
        yield


def html_artifact_names(artifacts: list[ArtifactRef]) -> list[str]:
    return [artifact.name for artifact in artifacts if artifact.kind == "html"]
