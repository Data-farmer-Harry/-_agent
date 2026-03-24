from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.services.agent_catalog import AgentCatalogService
from app.services.agent_runtime import AgentRuntime
from app.services.artifact_service import ArtifactService
from app.services.executor_service import LocalPythonExecutor
from app.services.planner_service import PlannerService
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry
from app.tools.base import ToolExecutionResult

from tests.support import HtmlContractMixin, StaticTool, make_request, sample_request


class RuntimeFailurePathTests(HtmlContractMixin, unittest.TestCase):
    def test_generic_unknown_request_returns_run_error_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            runtime = AgentRuntime(
                task_router=TaskRouter(catalog_service=AgentCatalogService()),
                planner_service=PlannerService(),
                tool_registry=ToolRegistry(),
                artifact_service=artifact_service,
            )
            events = []

            response = runtime.run(make_request(user_input="please do something unsupported"), event_sink=events.append)

            self.assertFalse(response.success)
            self.assertEqual(response.route.name, "generic.unknown")
            self.assertEqual(response.termination_reason, "no_supported_tool")
            self.assertEqual(response.final_message, "No supported tool could be inferred from this command.")
            self.assertEqual(response.plan_steps, [])
            self.assertEqual(response.trace, [])
            self.assertEqual([event.type for event in events], ["run_started", "run_error"])
            trace_file = artifact_service.load_trace_dict(response.run_id)
            self.assertEqual(trace_file["termination_reason"], "no_supported_tool")

    def test_phase_from_image_without_payload_skips_steps_and_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            runtime = AgentRuntime(
                task_router=TaskRouter(catalog_service=AgentCatalogService()),
                planner_service=PlannerService(),
                tool_registry=ToolRegistry(),
                artifact_service=artifact_service,
            )
            events = []

            response = runtime.run(
                make_request(user_input="render from image", task_type_hint="phase_diagram.from_image"),
                event_sink=events.append,
            )

            self.assertFalse(response.success)
            self.assertEqual([step.status for step in response.plan_steps], ["skipped", "skipped"])
            self.assertEqual(response.termination_reason, "completed")
            self.assertIn("did not produce", response.final_message)
            self.assertEqual([event.type for event in events], ["run_started", "step_skipped", "step_skipped", "run_error"])

    def test_runtime_falls_back_to_placeholder_codegen_after_failed_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            registry = ToolRegistry()
            calls: list[tuple[str, dict]] = []

            def codegen_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append(("codegen", dict(input_data)))
                generated_code = "placeholder-code" if input_data.get("force_placeholder") else "bad-code"
                summary = "Generated deterministic placeholder phase diagram code." if input_data.get("force_placeholder") else "Generated phase diagram code."
                return ToolExecutionResult(success=True, summary=summary, output={"generated_code": generated_code, "prompt": "prompt text"})

            def repair_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append(("repair", dict(input_data)))
                return ToolExecutionResult(
                    success=False,
                    summary="Repaired code still failed semantic validation.",
                    output={"quality_issues": ["repair still invalid"]},
                )

            def execute_handler(input_data: dict, context: dict) -> ToolExecutionResult:
                calls.append(("execute", dict(input_data)))
                if input_data["generated_code"] == "bad-code":
                    return ToolExecutionResult(
                        success=False,
                        summary="Execution failed.",
                        output={"stdout": "", "stderr": "RuntimeError: bad placeholder", "html_content": None, "html_path": None},
                    )
                html_path = str(artifact_service.get_result_path(context["run_id"]))
                html_content = "<html><body><main id='phase-diagram-agent-result'><meta name='phase-diagram-agent-layout' content='v1'><h1>fallback</h1></main></body></html>"
                return ToolExecutionResult(
                    success=True,
                    summary="Executed generated Python code.",
                    output={"stdout": "ok", "stderr": "", "html_content": html_content, "html_path": html_path},
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
                            "review_confidence": 0.86,
                            "review_issues": [],
                            "review_mode": "test",
                        },
                    )
                    if input_data.get("generated_code") == "placeholder-code"
                    else ToolExecutionResult(
                        success=False,
                        summary="Review unavailable.",
                        output={
                            "review_passed": False,
                            "review_summary": "Review unavailable.",
                            "review_confidence": 0.2,
                            "review_issues": ["placeholder fallback not reached yet"],
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
                    user_input="Generate a phase diagram",
                    task_type_hint="phase_diagram.generate",
                    diagram_request=sample_request(),
                ),
                event_sink=events.append,
            )

            self.assertTrue(response.success)
            self.assertEqual(
                [step.status for step in response.plan_steps],
                ["completed", "failed", "skipped", "failed", "skipped", "skipped", "completed", "completed", "completed"],
            )
            self.assertEqual(response.generated_code, "placeholder-code")
            self.assertEqual(response.termination_reason, "completed")
            self.assert_html_contract(response.html_content or "", title_fragment="fallback")
            self.assertEqual(
                [name for name, _ in calls],
                ["codegen", "execute", "repair", "codegen", "execute"],
            )
            self.assertTrue(calls[3][1]["force_placeholder"])
            repair_trace = next(observation for observation in response.trace if observation.tool_name == "phase_diagram_repair")
            self.assertEqual(repair_trace.output["quality_issues"], ["repair still invalid"])
            self.assertIn("step_failed", [event.type for event in events])
            self.assertIn("step_skipped", [event.type for event in events])

    def test_local_python_executor_reports_missing_result_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            executor = LocalPythonExecutor(artifact_service=artifact_service, python_executable=sys.executable)
            code = textwrap.dedent(
                """
                print("finished without html")
                """
            ).strip()

            result = executor.execute(run_id="run-missing-html", code=code)

            self.assertFalse(result.success)
            self.assertIsNone(result.html_content)
            self.assertIn("result.html was not created", result.stderr)


if __name__ == "__main__":
    unittest.main()
