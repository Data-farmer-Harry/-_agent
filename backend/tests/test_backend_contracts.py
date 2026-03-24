from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import agent_catalog, agent_manifest, generate_and_run, generate_from_image, health
from app.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    ArtifactRef,
    DiagramRequest,
    HtmlRedrawRequest,
    ImageDiagramRequest,
    PlanStep,
    TaskRoute,
    ToolObservation,
)
from app.services.agent_runtime import AgentRuntime
from app.services.agent_catalog import AgentCatalogService
from app.services.artifact_service import ArtifactService
from app.services.codegen_service import CodeGenerationService
from app.services.executor_service import LocalPythonExecutor
from app.services.phase_diagram_html_service import PhaseDiagramHtmlService
from app.services.phase_diagram_image_service import PhaseDiagramImageService
from app.services.planner_service import PlannerService
from app.services.prompt_builder import PromptBuilder
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry
from app.tools.base import BaseTool, ToolExecutionResult


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


def make_agent_response() -> AgentRunResponse:
    route = TaskRoute(
        name="phase_diagram.generate",
        workspace_id="phase_diagram",
        reason="Detected a phase-diagram request from the structured payload or command text.",
        selected_tool="phase_diagram_codegen",
        available_tools=["phase_diagram_codegen", "phase_diagram_repair", "python_execute", "load_latest_html_artifact"],
    )
    plan_steps = [
        PlanStep(index=1, tool_name="phase_diagram_codegen", description="Generate Python code for the requested phase diagram.", status="completed"),
        PlanStep(index=2, tool_name="python_execute", description="Execute the generated Python code.", status="completed"),
    ]
    trace = [
        ToolObservation(
            step_index=1,
            tool_name="phase_diagram_codegen",
            success=True,
            summary="Generated phase diagram code.",
            output={"generated_code": "print('ok')"},
        ),
        ToolObservation(
            step_index=2,
            tool_name="python_execute",
            success=True,
            summary="Executed generated Python code.",
            output={"stdout": "ok", "stderr": "", "html_path": "/tmp/result.html"},
        ),
    ]
    artifacts = [ArtifactRef(kind="html", name="result.html", path="/tmp/result.html", content=None)]
    return AgentRunResponse(
        success=True,
        run_id="run-123",
        route=route,
        final_message="Agent run completed successfully.",
        artifacts=artifacts,
        plan_steps=plan_steps,
        trace=trace,
        generated_code="print('ok')",
        stdout="ok",
        stderr="",
        html_content="<html><body>ok</body></html>",
        html_path="/tmp/result.html",
        termination_reason="completed",
        metadata={
            "prompt": "prompt text",
            "workspace_id": "phase_diagram",
            "selected_tool": "phase_diagram_codegen",
            "available_tools": ["phase_diagram_codegen", "phase_diagram_repair", "python_execute", "load_latest_html_artifact"],
            "reserved_tools": [],
        },
    )


def make_image_agent_response() -> AgentRunResponse:
    route = TaskRoute(
        name="phase_diagram.from_image",
        workspace_id="phase_diagram",
        reason="Detected an uploaded phase-diagram screenshot and routed it to the calibrated image parser.",
        selected_tool="phase_diagram_image_parse",
        available_tools=[
            "phase_diagram_codegen",
            "phase_diagram_image_parse",
            "phase_diagram_image_render",
            "phase_diagram_repair",
            "python_execute",
            "load_latest_html_artifact",
        ],
    )
    plan_steps = [
        PlanStep(index=1, tool_name="phase_diagram_image_parse", description="Parse the uploaded screenshot into a calibrated structured spec.", status="completed"),
        PlanStep(index=2, tool_name="phase_diagram_image_render", description="Render a deterministic HTML page from the parsed image spec.", status="completed"),
    ]
    trace = [
        ToolObservation(
            step_index=1,
            tool_name="phase_diagram_image_parse",
            success=True,
            summary="Built a structured image spec using manual calibrated mode.",
            output={"summary": "manual"},
        ),
        ToolObservation(
            step_index=2,
            tool_name="phase_diagram_image_render",
            success=True,
            summary="Rendered a calibrated HTML page from the uploaded phase-diagram screenshot.",
            output={"html_path": "/tmp/result.html"},
        ),
    ]
    artifacts = [ArtifactRef(kind="html", name="result.html", path="/tmp/result.html", content=None)]
    return AgentRunResponse(
        success=True,
        run_id="run-image-123",
        route=route,
        final_message="Agent run completed successfully.",
        artifacts=artifacts,
        plan_steps=plan_steps,
        trace=trace,
        generated_code="",
        stdout="manual",
        stderr="",
        html_content="<html><body>image</body></html>",
        html_path="/tmp/result.html",
        termination_reason="completed",
        metadata={"prompt": "vision prompt"},
    )


class StaticTool(BaseTool):
    def __init__(self, name: str, description: str, handler):
        self.name = name
        self.description = description
        self._handler = handler

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        return self._handler(input_data, context)


class BackendApiContractTests(unittest.TestCase):
    def test_health_endpoint_reports_service_status(self) -> None:
        response = health()
        payload = response.model_dump()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["app_name"], "Phase Diagram Agent API")
        self.assertIsInstance(payload["version"], str)

    def test_generate_and_run_endpoint_serializes_agent_response(self) -> None:
        fake_response = make_agent_response()

        with patch("app.main.agent_runtime.run", return_value=fake_response) as run_mock:
            response = generate_and_run(sample_request())

        run_mock.assert_called_once()

        payload = response.model_dump()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["run_id"], "run-123")
        self.assertEqual(payload["route"], "phase_diagram.generate")
        self.assertEqual(payload["route_reason"], fake_response.route.reason)
        self.assertEqual(payload["workspace_id"], "phase_diagram")
        self.assertEqual(payload["selected_tool"], "phase_diagram_codegen")
        self.assertIn("phase_diagram_codegen", payload["available_tools"])
        self.assertEqual(payload["generated_code"], "print('ok')")
        self.assertEqual(payload["prompt"], "prompt text")
        self.assertEqual(payload["termination_reason"], "completed")
        self.assertEqual(payload["plan_steps"][0]["tool_name"], "phase_diagram_codegen")
        self.assertEqual(payload["trace"][1]["tool_name"], "python_execute")

    def test_agent_catalog_exposes_phase_diagram_and_lammps_workspaces(self) -> None:
        response = agent_catalog()
        payload = response.model_dump()
        workspaces = {workspace["id"]: workspace for workspace in payload["workspaces"]}

        self.assertIn("phase_diagram", workspaces)
        self.assertIn("lammps", workspaces)
        self.assertEqual(workspaces["phase_diagram"]["status"], "active")
        self.assertEqual(workspaces["lammps"]["status"], "active")
        self.assertIn("phase_diagram_codegen", workspaces["phase_diagram"]["available_tools"])
        self.assertIn("phase_diagram_image_parse", workspaces["phase_diagram"]["available_tools"])
        self.assertIn("lammps_command_router", workspaces["lammps"]["available_tools"])
        self.assertIn("lammps_codegen", workspaces["lammps"]["reserved_tools"])

    def test_agent_manifest_exposes_route_blueprints_and_tool_contracts(self) -> None:
        response = agent_manifest()
        payload = response.model_dump()
        routes = {route["name"]: route for route in payload["routes"]}
        tools = {tool["name"]: tool for tool in payload["tools"]}

        self.assertIn("phase_diagram.generate", routes)
        self.assertIn("phase_diagram.from_image", routes)
        self.assertEqual(routes["phase_diagram.generate"]["steps"][0]["tool_name"], "phase_diagram_codegen")
        self.assertEqual(routes["phase_diagram.from_image"]["steps"][1]["tool_name"], "phase_diagram_image_render")
        self.assertIn("failure_strategy", routes["lammps.generate"])
        self.assertTrue(routes["phase_diagram.generate"]["sample_prompts"])
        self.assertIn("phase_diagram.generate", tools["phase_diagram_codegen"]["supports_routes"])
        self.assertIn("code", tools["phase_diagram_codegen"]["produces_artifacts"])

    def test_generate_from_image_endpoint_serializes_agent_response(self) -> None:
        fake_response = make_image_agent_response()

        with patch("app.main.agent_runtime.run", return_value=fake_response) as run_mock:
            response = generate_from_image(sample_image_request())

        run_mock.assert_called_once()

        payload = response.model_dump()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["route"], "phase_diagram.from_image")
        self.assertEqual(payload["selected_tool"], "phase_diagram_image_parse")
        self.assertEqual(payload["workspace_id"], "phase_diagram")
        self.assertEqual(payload["prompt"], "vision prompt")
        self.assertEqual(payload["plan_steps"][0]["tool_name"], "phase_diagram_image_parse")
        self.assertEqual(payload["plan_steps"][1]["tool_name"], "phase_diagram_image_render")


class RoutingAndPlanningTests(unittest.TestCase):
    def test_task_router_prefers_explicit_hints_and_structured_requests(self) -> None:
        router = TaskRouter(catalog_service=AgentCatalogService())

        hinted = router.route(AgentRunRequest(user_input="route by hint", task_type_hint="materials.analysis"))
        structured = router.route(AgentRunRequest(user_input="route by payload", diagram_request=sample_request()))
        from_image = router.route(AgentRunRequest(user_input="route from image", image_diagram_request=sample_image_request()))
        lammps = router.route(AgentRunRequest(user_input="Run a LAMMPS molecular dynamics simulation"))
        unknown = router.route(AgentRunRequest(user_input="no structured payload"))

        self.assertEqual(hinted.name, "materials.analysis")
        self.assertIn("explicit task_type_hint", hinted.reason)
        self.assertEqual(hinted.workspace_id, "generic")
        self.assertEqual(structured.name, "phase_diagram.generate")
        self.assertEqual(structured.workspace_id, "phase_diagram")
        self.assertEqual(structured.selected_tool, "phase_diagram_codegen")
        self.assertIn("Python code generation", structured.reason)
        self.assertEqual(from_image.name, "phase_diagram.recognize")
        self.assertEqual(from_image.selected_tool, "phase_diagram_image_parse")
        self.assertIn("uploaded phase-diagram image", from_image.reason)
        self.assertEqual(from_image.intent, "image_recognition")
        self.assertTrue(from_image.decision_source)
        self.assertEqual(lammps.name, "lammps.generate")
        self.assertEqual(lammps.workspace_id, "lammps")
        self.assertEqual(lammps.selected_tool, "lammps_command_router")
        self.assertIn("stub", lammps.reason)
        self.assertEqual(unknown.name, "generic.unknown")
        self.assertEqual(unknown.workspace_id, "generic")

    def test_planner_builds_expected_phase_diagram_plan(self) -> None:
        planner = PlannerService()
        plan = planner.build_plan(TaskRoute(name="phase_diagram.generate", workspace_id="phase_diagram", reason="ok"))

        self.assertEqual(len(plan), 9)
        self.assertEqual(
            [step.tool_name for step in plan],
            [
                "phase_diagram_codegen",
                "python_execute",
                "phase_diagram_result_review",
                "phase_diagram_repair",
                "python_execute",
                "phase_diagram_result_review",
                "phase_diagram_codegen",
                "python_execute",
                "phase_diagram_result_review",
            ],
        )
        self.assertTrue(plan[3].retryable)
        self.assertTrue(plan[6].input["force_placeholder"])
        recognize_plan = planner.build_plan(TaskRoute(name="phase_diagram.recognize", workspace_id="phase_diagram", reason="recognize"))
        self.assertEqual([step.tool_name for step in recognize_plan], ["phase_diagram_image_parse"])
        image_plan = planner.build_plan(TaskRoute(name="phase_diagram.from_image", workspace_id="phase_diagram", reason="image"))
        self.assertEqual([step.tool_name for step in image_plan], ["phase_diagram_image_parse", "phase_diagram_image_render"])
        redraw_plan = planner.build_plan(TaskRoute(name="phase_diagram.redraw_html", workspace_id="phase_diagram", reason="redraw"))
        self.assertEqual([step.tool_name for step in redraw_plan], ["phase_diagram_html_redraw", "phase_diagram_html_review"])
        lammps_plan = planner.build_plan(TaskRoute(name="lammps.generate", workspace_id="lammps", reason="stub"))
        self.assertEqual(len(lammps_plan), 1)
        self.assertEqual(lammps_plan[0].tool_name, "lammps_command_router")
        self.assertEqual(planner.build_plan(TaskRoute(name="generic.unknown", workspace_id="generic", reason="nope")), [])


class CodeGenerationQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codegen = CodeGenerationService(prompt_builder=PromptBuilder())

    def test_fecu_placeholder_preserves_expected_terminal_solid_semantics(self) -> None:
        request = sample_request(system_name="Fe-Cu")
        placeholder = self.codegen.build_placeholder_code(request)

        self.assertIn("Fe-rich terminal solid", placeholder)
        self.assertIn("Cu-rich terminal solid", placeholder)
        self.assertIn("Two-solid region", placeholder)
        self.assertIn("Solvus / miscibility boundary", placeholder)
        self.assertNotIn("Fe3C", placeholder)
        self.assertNotIn("A3", placeholder)
        self.assertNotIn("Acm", placeholder)
        self.assertNotIn("ferrite", placeholder.lower())
        self.assertNotIn("austenite", placeholder.lower())

    def test_semantic_gate_rejects_steel_drift_for_fecu(self) -> None:
        request = sample_request(system_name="Fe-Cu")
        _, issues = self.codegen.sanitize_and_validate_code(request, "print('Fe3C ferrite austenite A3')")

        self.assertTrue(issues)
        self.assertTrue(any("Fe-C / steel-style" in issue or "Fe-Cu binary output drifted" in issue for issue in issues))

    def test_placeholder_code_is_semantically_clean_for_fecu(self) -> None:
        request = sample_request(system_name="Fe-Cu")
        placeholder = self.codegen.build_placeholder_code(request)
        _, issues = self.codegen.sanitize_and_validate_code(request, placeholder)

        self.assertEqual(issues, [])


class ExecutorAndRuntimeTests(unittest.TestCase):
    def test_phase_diagram_html_review_skips_llm_for_long_complete_pages(self) -> None:
        class ExplodingLLM:
            def is_configured(self) -> bool:
                return True

            def chat_json(self, **_: object) -> dict[str, object]:
                raise AssertionError("Long HTML review should stay on heuristic guardrails.")

        service = PhaseDiagramHtmlService(llm_client=ExplodingLLM())
        request = HtmlRedrawRequest(
            message="请整理成组会展示页面并重新绘制 Fe-Cu 相图。",
            system_name="Fe-Cu",
            chart_title="Fe-Cu redraw",
            diagram_type="binary",
            notes="review regression",
        )
        html_content = textwrap.dedent(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="utf-8">
              <meta name="phase-diagram-agent-layout" content="v1">
              <title>Fe-Cu redraw</title>
              <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
            </head>
            <body>
              <main id="phase-diagram-agent-result">
                <section><h1>Fe-Cu redraw</h1><p>Materials group meeting summary for Fe-Cu.</p></section>
                <div id="chart"></div>
                <script>
                  const trace = {{ x: [0, 50, 100], y: [300, 1200, 1600], mode: "lines", name: "liquidus" }};
                  Plotly.newPlot("chart", [trace], {{ title: "Fe-Cu" }});
                  const filler = "{'x' * 5200}";
                  console.log(filler.length);
                </script>
              </main>
            </body>
            </html>
            """
        ).strip()

        review = service.review_redraw_artifact(request, html_content)

        self.assertTrue(review["passed"])
        self.assertEqual(review["review_mode"], "heuristic_redraw_review")
        self.assertEqual(review["issues"], [])

    def test_phase_diagram_image_service_manual_render_contains_layout_marker(self) -> None:
        service = PhaseDiagramImageService()
        spec, prompt = service.analyze_image(sample_image_request())
        self.assertEqual(spec.detection_mode, "manual_calibrated")
        self.assertEqual(spec.chart_title, sample_image_request().chart_title)
        self.assertIn("deterministic axes", spec.summary)
        self.assertEqual(spec.labels, [])
        self.assertEqual(spec.boundaries, [])
        self.assertTrue(prompt)

    def test_local_python_executor_normalizes_plain_html_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            executor = LocalPythonExecutor(artifact_service=artifact_service, python_executable=sys.executable)
            code = textwrap.dedent(
                """
                from pathlib import Path

                Path("result.html").write_text(
                    "<html><head><title>Plain</title></head><body><p>ok</p></body></html>",
                    encoding="utf-8",
                )
                """
            ).strip()

            result = executor.execute(run_id="run-html", code=code)

            self.assertTrue(result.success)
            self.assertIsNotNone(result.html_content)
            self.assertIn('phase-diagram-agent-layout', result.html_content or "")
            self.assertIn('normalized-page-shell', result.html_content or "")
            self.assertTrue(Path(result.html_path or "").exists())

    def test_agent_runtime_success_writes_latest_html_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            registry = ToolRegistry()
            calls: list[tuple[str, dict]] = []

            def codegen_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append(("phase_diagram_codegen", input_data))
                generated_code = "good-code"
                return ToolExecutionResult(
                    success=True,
                    summary="Generated phase diagram code.",
                    output={"generated_code": generated_code, "prompt": "prompt text"},
                    artifacts=[ArtifactRef(kind="code", name="generated_code.py", content=generated_code)],
                )

            def execute_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append(("python_execute", input_data))
                html_content = "<html><body><main id='phase-diagram-agent-result'><meta name='phase-diagram-agent-layout' content='v1'><h1>ok</h1></main></body></html>"
                html_path = str(artifact_service.get_result_path(context["run_id"]))
                return ToolExecutionResult(
                    success=True,
                    summary="Executed generated Python code.",
                    output={"stdout": "ok", "stderr": "", "html_content": html_content, "html_path": html_path},
                    artifacts=[ArtifactRef(kind="html", name="result.html", path=html_path, content=html_content)],
                )

            registry.register(StaticTool("phase_diagram_codegen", "codegen", codegen_handler))
            registry.register(StaticTool("python_execute", "executor", execute_handler))
            registry.register(
                StaticTool(
                    "phase_diagram_result_review",
                    "review",
                    lambda input_data, context: (
                        calls.append(("phase_diagram_result_review", input_data))
                        or ToolExecutionResult(
                            success=True,
                            summary="Review passed.",
                            output={
                                "review_passed": True,
                                "review_summary": "Review passed.",
                                "review_confidence": 0.91,
                                "review_issues": [],
                                "review_mode": "test",
                            },
                        )
                    ),
                )
            )
            registry.register(
                StaticTool(
                    "phase_diagram_repair",
                    "repair",
                    lambda input_data, context: ToolExecutionResult(success=False, summary="unused", output={}),
                )
            )

            runtime = AgentRuntime(
                task_router=TaskRouter(catalog_service=AgentCatalogService()),
                planner_service=PlannerService(),
                tool_registry=registry,
                artifact_service=artifact_service,
            )
            events: list = []
            response = runtime.run(
                AgentRunRequest(
                    user_input="generate a phase diagram",
                    task_type_hint="phase_diagram.generate",
                    diagram_request=sample_request(),
                ),
                event_sink=events.append,
            )

            self.assertTrue(response.success)
            self.assertEqual(response.route.name, "phase_diagram.generate")
            self.assertEqual(response.route.workspace_id, "phase_diagram")
            self.assertEqual(response.metadata["prompt"], "prompt text")
            self.assertEqual(response.metadata["workspace_id"], "phase_diagram")
            self.assertEqual(response.metadata["selected_tool"], "phase_diagram_codegen")
            self.assertEqual(response.plan_steps[0].status, "completed")
            self.assertEqual(response.plan_steps[1].status, "completed")
            self.assertEqual(response.plan_steps[2].status, "completed")
            self.assertTrue(any(artifact.kind == "html" for artifact in response.artifacts))
            self.assertIsNone(next(artifact.content for artifact in response.artifacts if artifact.kind == "html"))
            self.assertEqual(artifact_service.load_latest_html(), response.html_content)
            self.assertIsNotNone(artifact_service.load_trace_dict(response.run_id))
            self.assertEqual(artifact_service.load_trace_dict(response.run_id)["termination_reason"], "completed")
            self.assertEqual(events[0].type, "run_started")
            self.assertEqual(events[-1].type, "run_completed")
            self.assertEqual([name for name, _ in calls], ["phase_diagram_codegen", "python_execute", "phase_diagram_result_review"])

    def test_agent_runtime_repair_path_recovers_from_failed_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            registry = ToolRegistry()
            calls: list[str] = []

            def codegen_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append("codegen")
                return ToolExecutionResult(
                    success=True,
                    summary="Generated phase diagram code.",
                    output={"generated_code": "bad-code", "prompt": "prompt text"},
                    artifacts=[ArtifactRef(kind="code", name="generated_code.py", content="bad-code")],
                )

            def repair_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append("repair")
                self.assertEqual(input_data["generated_code"], "bad-code")
                self.assertIn("SyntaxError", input_data["stderr"])
                repaired_code = "good-code"
                return ToolExecutionResult(
                    success=True,
                    summary="Repaired generated code.",
                    output={"generated_code": repaired_code},
                    artifacts=[ArtifactRef(kind="code", name="repaired_code.py", content=repaired_code)],
                )

            def execute_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append("execute")
                if input_data["generated_code"] == "bad-code":
                    return ToolExecutionResult(
                        success=False,
                        summary="Execution failed.",
                        output={"stdout": "", "stderr": "SyntaxError: boom", "html_content": None, "html_path": None},
                    )
                html_content = "<html><body><h1>repaired</h1></body></html>"
                html_path = str(artifact_service.get_result_path(context["run_id"]))
                return ToolExecutionResult(
                    success=True,
                    summary="Executed generated Python code.",
                    output={"stdout": "ok", "stderr": "", "html_content": html_content, "html_path": html_path},
                    artifacts=[ArtifactRef(kind="html", name="result.html", path=html_path, content=html_content)],
                )

            registry.register(StaticTool("phase_diagram_codegen", "codegen", codegen_handler))
            registry.register(StaticTool("phase_diagram_repair", "repair", repair_handler))
            registry.register(StaticTool("python_execute", "executor", execute_handler))
            registry.register(
                StaticTool(
                    "phase_diagram_result_review",
                    "review",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="Review passed.",
                        output={
                            "review_passed": True,
                            "review_summary": "Review passed.",
                            "review_confidence": 0.88,
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
            events: list = []
            response = runtime.run(
                AgentRunRequest(
                    user_input="generate a phase diagram",
                    task_type_hint="phase_diagram.generate",
                    diagram_request=sample_request(),
                ),
                event_sink=events.append,
            )

            self.assertTrue(response.success)
            self.assertEqual(response.termination_reason, "completed")
            self.assertEqual(response.generated_code, "good-code")
            self.assertIn("ok", response.stdout)
            self.assertTrue(any(step.index == 4 and step.status == "completed" for step in response.plan_steps))
            self.assertTrue(any(step.index == 5 and step.status == "completed" for step in response.plan_steps))
            self.assertTrue(any(step.index == 6 and step.status == "completed" for step in response.plan_steps))
            self.assertEqual(calls, ["codegen", "execute", "repair", "execute"])
            self.assertTrue(any(event.type == "step_failed" for event in events))
            self.assertTrue(any(event.type == "run_completed" for event in events))

    def test_agent_runtime_image_route_renders_html_without_python_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            registry = ToolRegistry()

            registry.register(
                StaticTool(
                    "phase_diagram_image_parse",
                    "image parse",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="parsed image",
                        output={
                            "image_spec": {
                                "chart_title": "Image route",
                                "system_name": "Fe-C",
                                "filename": "phase.png",
                                "diagram_type": "binary",
                                "source_image_data_url": sample_image_request().image_data_url,
                                "x_axis": {"label": "Composition", "minimum": 0.0, "maximum": 1.0},
                                "y_axis": {"label": "Temperature (°C)", "minimum": 0.0, "maximum": 1600.0},
                                "detection_mode": "manual_calibrated",
                                "confidence": 0.5,
                                "summary": "manual image route",
                                "notes": ["note"],
                                "labels": [],
                                "boundaries": [],
                            },
                            "prompt": "vision prompt",
                            "summary": "manual image route",
                        },
                    ),
                )
            )
            registry.register(
                StaticTool(
                    "phase_diagram_image_render",
                    "image render",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="rendered image route",
                        output={
                            "html_content": "<html><body>image-route</body></html>",
                            "html_path": str(artifact_service.get_result_path(context["run_id"])),
                            "summary": "rendered image route",
                        },
                        artifacts=[ArtifactRef(kind="html", name="result.html", path=str(artifact_service.get_result_path(context["run_id"])), content="<html><body>image-route</body></html>")],
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
                AgentRunRequest(
                    user_input="Create calibrated page from screenshot",
                    task_type_hint="phase_diagram.from_image",
                    image_diagram_request=sample_image_request(),
                )
            )

            self.assertTrue(response.success)
            self.assertEqual(response.route.name, "phase_diagram.from_image")
            self.assertEqual(response.route.selected_tool, "phase_diagram_image_parse")
            self.assertEqual([step.tool_name for step in response.plan_steps], ["phase_diagram_image_parse", "phase_diagram_image_render"])
            self.assertEqual(response.html_content, "<html><body>image-route</body></html>")
            self.assertEqual(response.metadata["prompt"], "vision prompt")

    def test_agent_runtime_lammps_stub_returns_success_without_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            registry = ToolRegistry()

            registry.register(
                StaticTool(
                    "lammps_command_router",
                    "lammps stub",
                    lambda input_data, context: ToolExecutionResult(
                        success=True,
                        summary="LAMMPS stub accepted the request.",
                        output={"message": "stub ready", "user_input": input_data["user_input"]},
                        artifacts=[ArtifactRef(kind="text", name="lammps_stub_summary.txt", content="stub ready")],
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
                AgentRunRequest(
                    user_input="Run a LAMMPS molecular dynamics simulation",
                    task_type_hint="lammps.generate",
                    context={"material": "Cu"},
                )
            )

            self.assertTrue(response.success)
            self.assertEqual(response.route.workspace_id, "lammps")
            self.assertEqual(response.route.selected_tool, "lammps_command_router")
            self.assertEqual(response.termination_reason, "stub_completed")
            self.assertIn("LAMMPS stub completed", response.final_message)
            self.assertEqual(response.plan_steps[0].status, "completed")
            self.assertEqual(response.trace[0].tool_name, "lammps_command_router")
            self.assertIsNone(response.html_content)
            self.assertTrue(any(artifact.kind == "text" for artifact in response.artifacts))


if __name__ == "__main__":
    unittest.main()
