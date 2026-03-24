from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.schemas import ArtifactRef
from app.services.agent_catalog import AgentCatalogService
from app.services.agent_runtime import AgentRuntime
from app.services.artifact_service import ArtifactService
from app.services.codegen_service import CodeGenerationService
from app.services.phase_diagram_image_service import PhaseDiagramImageService
from app.services.planner_service import PlannerService
from app.services.prompt_builder import PromptBuilder
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry
from app.tools.base import ToolExecutionResult
from app.tools.phase_diagram_codegen_tool import PhaseDiagramCodegenTool
from app.tools.phase_diagram_image_parse_tool import PhaseDiagramImageParseTool

from tests.support import HtmlContractMixin, StaticTool, build_default_runtime, llm_disabled, make_request, sample_image_request, sample_request


class GenerateFlowIntegrationTests(HtmlContractMixin, unittest.TestCase):
    def test_phase_generate_real_codegen_runtime_produces_contract_html_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, llm_disabled():
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            registry = ToolRegistry()
            registry.register(PhaseDiagramCodegenTool(codegen_service=CodeGenerationService(prompt_builder=PromptBuilder())))
            registry.register(
                StaticTool(
                    "python_execute",
                    "executor",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="Executed generated Python code.",
                        output={
                            "stdout": "ok",
                            "stderr": "",
                            "html_content": "<html><body><main id='phase-diagram-agent-result'><meta name='phase-diagram-agent-layout' content='v1'><h1>Fe-Cu placeholder</h1></main></body></html>",
                            "html_path": str(artifact_service.get_result_path(context["run_id"])),
                        },
                        artifacts=[
                            ArtifactRef(
                                kind="html",
                                name="result.html",
                                path=str(artifact_service.get_result_path(context["run_id"])),
                                content="<html><body><main id='phase-diagram-agent-result'><meta name='phase-diagram-agent-layout' content='v1'><h1>Fe-Cu placeholder</h1></main></body></html>",
                            )
                        ],
                    ),
                )
            )
            registry.register(
                StaticTool(
                    "phase_diagram_result_review",
                    "review",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="Agent review passed.",
                        output={
                            "review_passed": True,
                            "review_summary": "Agent review passed.",
                            "review_confidence": 0.94,
                            "review_issues": [],
                            "review_mode": "test",
                        },
                    ),
                )
            )
            runtime = AgentRuntime(
                task_router=TaskRouter(catalog_service=AgentCatalogService()),
                planner_service=PlannerService(),
                tool_registry=registry,
                artifact_service=artifact_service,
            )
            events = []

            response = runtime.run(
                make_request(
                    user_input="Generate a phase diagram for Fe-Cu",
                    task_type_hint="phase_diagram.generate",
                    diagram_request=sample_request(),
                ),
                event_sink=events.append,
            )

            self.assertTrue(response.success)
            self.assertEqual(response.route.name, "phase_diagram.generate")
            self.assertEqual(response.termination_reason, "completed")
            self.assertIn("Generated phase diagram code.", response.trace[0].summary)
            self.assertIn("Fe-rich terminal solid", response.generated_code or "")
            self.assertIn("python_execute", [observation.tool_name for observation in response.trace])
            self.assertIn("phase_diagram_result_review", [observation.tool_name for observation in response.trace])
            self.assert_html_contract(response.html_content or "", title_fragment="Fe-Cu placeholder")
            self.assertEqual(artifact_service.load_latest_html(), response.html_content)
            self.assertIn("repair", response.metadata["plan_stages"])
            self.assertIn("deterministic placeholder", response.metadata["failure_strategy"])
            self.assertTrue(response.metadata["review"]["passed"])
            self.assertEqual(events[0].type, "run_started")
            self.assertEqual(events[-1].type, "run_completed")
            execute_output = next(observation.output for observation in response.trace if observation.tool_name == "python_execute")
            self.assertNotIn("html_content", execute_output)
            self.assertTrue(execute_output["html_content_omitted"])
            trace_file = artifact_service.load_trace_dict(response.run_id)
            self.assertEqual(trace_file["route"]["name"], "phase_diagram.generate")
            self.assertEqual(len(trace_file["observations"]), len(response.trace))

    def test_phase_from_image_real_parse_runtime_produces_manual_contract_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, llm_disabled():
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            image_service = PhaseDiagramImageService()
            registry = ToolRegistry()
            registry.register(PhaseDiagramImageParseTool(image_service=image_service))
            registry.register(
                StaticTool(
                    "phase_diagram_image_render",
                    "image render",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="Rendered a calibrated HTML page from the uploaded phase-diagram screenshot.",
                        output={
                            "html_content": "<html><body><main id='phase-diagram-agent-result'><meta name='phase-diagram-agent-layout' content='v1'><h1>Calibrated phase-diagram view</h1><p>Composition (wt.% C)</p></main></body></html>",
                            "html_path": str(artifact_service.get_result_path(context["run_id"])),
                            "summary": input_data["image_spec"]["summary"],
                        },
                        artifacts=[
                            ArtifactRef(
                                kind="html",
                                name="result.html",
                                path=str(artifact_service.get_result_path(context["run_id"])),
                                content="<html><body><main id='phase-diagram-agent-result'><meta name='phase-diagram-agent-layout' content='v1'><h1>Calibrated phase-diagram view</h1><p>Composition (wt.% C)</p></main></body></html>",
                            )
                        ],
                    ),
                )
            )
            runtime = AgentRuntime(
                task_router=TaskRouter(catalog_service=AgentCatalogService()),
                planner_service=PlannerService(),
                tool_registry=registry,
                artifact_service=artifact_service,
            )

            response = runtime.run(
                make_request(
                    user_input="Create a calibrated page from a screenshot",
                    task_type_hint="phase_diagram.from_image",
                    image_diagram_request=sample_image_request(),
                )
            )

            self.assertTrue(response.success)
            self.assertEqual(response.route.name, "phase_diagram.from_image")
            self.assertEqual([step.status for step in response.plan_steps], ["completed", "completed"])
            self.assertEqual(response.metadata["image_spec"]["detection_mode"], "manual_calibrated")
            self.assert_html_contract(response.html_content or "", title_fragment="Calibrated phase-diagram view")
            self.assertIn("Composition (wt.% C)", response.html_content or "")
            self.assertIn("deterministic axes", response.metadata["image_spec"]["summary"])
            self.assertIn("manual calibrated reconstruction", response.metadata["failure_strategy"])
            self.assertEqual(artifact_service.load_latest_html(), response.html_content)

    def test_lammps_stub_real_runtime_returns_stub_trace_without_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime, _, _ = build_default_runtime(root_dir=Path(tmp_dir), include_lammps=True)

            response = runtime.run(
                make_request(
                    user_input="Run a LAMMPS molecular dynamics simulation for Cu",
                    task_type_hint="lammps.generate",
                    context={"material": "Cu"},
                )
            )

            self.assertTrue(response.success)
            self.assertEqual(response.route.workspace_id, "lammps")
            self.assertEqual(response.termination_reason, "stub_completed")
            self.assertIsNone(response.html_content)
            self.assertEqual([step.status for step in response.plan_steps], ["completed"])
            self.assertEqual(response.trace[0].tool_name, "lammps_command_router")
            self.assertIn("next_actions", response.trace[0].output)


if __name__ == "__main__":
    unittest.main()
