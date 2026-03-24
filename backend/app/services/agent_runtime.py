from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config import settings
from app.schemas import AgentRunRequest, AgentRunResponse, AgentStreamEvent, ArtifactRef, PlanStep, RunTrace, TaskRoute, ToolObservation
from app.services.artifact_service import ArtifactService
from app.services.agent_manifest import get_route_definition
from app.services.planner_service import PlannerService
from app.services.runtime_state import RuntimeSessionState
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry


class AgentRuntime:
    def __init__(
        self,
        task_router: TaskRouter,
        planner_service: PlannerService,
        tool_registry: ToolRegistry,
        artifact_service: ArtifactService,
    ) -> None:
        self.task_router = task_router
        self.planner_service = planner_service
        self.tool_registry = tool_registry
        self.artifact_service = artifact_service

    def _sanitize_artifacts(self, artifacts: list[ArtifactRef]) -> list[ArtifactRef]:
        sanitized: list[ArtifactRef] = []
        for artifact in artifacts:
            if artifact.kind == "html":
                sanitized.append(artifact.model_copy(update={"content": None}))
            else:
                sanitized.append(artifact)
        return sanitized

    def _sanitize_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                if key == "image_data_url" and isinstance(item, str) and item.startswith("data:image/"):
                    sanitized[key] = {
                        "omitted": True,
                        "kind": item.split(";")[0],
                        "size_bytes": len(item.encode("utf-8")),
                    }
                    continue
                sanitized[key] = self._sanitize_payload(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize_payload(item) for item in value]
        if isinstance(value, str) and value.startswith("data:image/"):
            return {"omitted": True, "kind": value.split(";")[0], "size_bytes": len(value.encode("utf-8"))}
        return value

    def _sanitize_observation_output(self, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        sanitized_output = self._sanitize_payload(output)
        if tool_name in {"python_execute", "phase_diagram_html_redraw", "phase_diagram_image_render"} and sanitized_output.get("html_content") is not None:
            html_content = sanitized_output.pop("html_content")
            html_size_bytes = len(html_content.encode("utf-8")) if isinstance(html_content, str) else 0
            sanitized_output["html_content_omitted"] = True
            sanitized_output["html_size_bytes"] = html_size_bytes
        if tool_name == "phase_diagram_image_parse":
            image_spec = sanitized_output.get("image_spec")
            if isinstance(image_spec, dict) and image_spec.get("source_image_data_url") is not None:
                data_url = image_spec.pop("source_image_data_url")
                if isinstance(data_url, str):
                    image_spec["source_image_data_url"] = {
                        "omitted": True,
                        "kind": data_url.split(";")[0],
                        "size_bytes": len(data_url.encode("utf-8")),
                    }
        return sanitized_output

    def _emit_event(self, event_sink: Callable[[AgentStreamEvent], None] | None, event: AgentStreamEvent) -> None:
        if event_sink is not None:
            event_sink(event)

    @staticmethod
    def _build_final_message(route: TaskRoute, success: bool, has_plan_steps: bool, termination_reason: str) -> str:
        if success:
            if route.workspace_id == "lammps":
                return "LAMMPS stub completed successfully. The router produced an extensible tool-chain outline, but no simulation was launched yet."
            if route.name == "phase_diagram.recognize":
                return "Phase-diagram recognition completed successfully."
            if route.name == "phase_diagram.from_image":
                return "Phase-diagram screenshot reconstruction completed successfully."
            if route.name == "phase_diagram.redraw_html":
                return "Phase-diagram HTML redraw completed successfully."
            return "Phase-diagram generation completed successfully."
        if termination_reason == "review_failed":
            return "A phase-diagram artifact was produced, but the agent review flagged quality or accuracy issues."
        if not has_plan_steps:
            if route.workspace_id == "lammps":
                return "LAMMPS workspace is reserved, but no executable tool is registered yet."
            if route.name == "generic.unknown":
                return "No supported tool could be inferred from this command."
            return f"The requested route '{route.name}' does not have an executable plan yet."
        return "Agent run did not produce the expected final artifact."

    @staticmethod
    def _base_route_metadata(route: TaskRoute) -> dict[str, Any]:
        route_definition = get_route_definition(route.name)
        return {
            "trace_version": "agent-runtime/v2",
            "workspace_id": route.workspace_id,
            "route_name": route.name,
            "selected_tool": route.selected_tool,
            "entry_tool": route.entry_tool or route.selected_tool,
            "available_tools": route.available_tools,
            "reserved_tools": route.reserved_tools,
            "input_channels": route.input_channels,
            "deliverable": route.deliverable,
            "narrative": route.narrative,
            "intent": route.intent,
            "decision_source": route.decision_source,
            "decision_confidence": route.decision_confidence,
            "failure_strategy": route_definition.failure_strategy,
            "sample_prompts": list(route_definition.sample_prompts),
        }

    def _build_tool_context(
        self,
        request: AgentRunRequest,
        route: TaskRoute,
        run_id: str,
        state: RuntimeSessionState,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "user_input": request.user_input,
            "task_type_hint": request.task_type_hint or "",
            "workspace_id": route.workspace_id,
            "route_name": route.name,
            "route_deliverable": route.deliverable,
            "entry_tool": route.entry_tool or route.selected_tool,
            "request_context": request.context,
            "state_snapshot": state.snapshot(route),
        }

    @staticmethod
    def _skip_reason(step: PlanStep, request: AgentRunRequest, state: RuntimeSessionState) -> str | None:
        if step.tool_name == "phase_diagram_codegen" and not (state.diagram_request or request.diagram_request):
            return "Missing diagram_request payload."
        if step.tool_name == "phase_diagram_html_redraw" and not request.html_redraw_request:
            return "Missing html_redraw_request payload."
        if step.tool_name == "phase_diagram_image_parse" and not request.image_diagram_request:
            return "Missing image_diagram_request payload."
        if step.tool_name == "phase_diagram_image_render" and not state.image_spec:
            return "No parsed image spec is available yet."
        if step.tool_name == "phase_diagram_repair":
            if not (state.diagram_request or request.diagram_request) or not state.generated_code or not state.stderr:
                return "Repair requires diagram_request, generated_code, and stderr from a failed execution."
            if state.repair_attempts >= settings.agent_max_repair_attempts:
                return "Repair attempt budget has been exhausted."
        if step.tool_name == "python_execute" and not state.generated_code:
            return "No generated_code is available to execute."
        if step.tool_name == "phase_diagram_result_review":
            has_generation_inputs = bool(state.diagram_request or request.diagram_request) and bool(state.generated_code and state.html_content)
            has_redraw_inputs = bool(request.html_redraw_request) and bool(state.html_content)
            if not (has_generation_inputs or has_redraw_inputs):
                return "Review requires either diagram_request + generated_code + html_content, or html_redraw_request + html_content."
        if step.tool_name == "phase_diagram_html_review":
            if not request.html_redraw_request:
                return "HTML redraw review requires html_redraw_request."
            if not state.html_content:
                return "HTML redraw review requires html_content."
        return None

    @staticmethod
    def _resolve_step_input(step: PlanStep, request: AgentRunRequest, state: RuntimeSessionState) -> dict[str, Any]:
        input_data = dict(step.input)
        if step.tool_name == "phase_diagram_codegen":
            input_data["diagram_request"] = state.diagram_request or (request.diagram_request.model_dump() if request.diagram_request else {})
        elif step.tool_name == "phase_diagram_html_redraw":
            input_data["html_redraw_request"] = request.html_redraw_request.model_dump() if request.html_redraw_request else {}
        elif step.tool_name == "phase_diagram_image_parse":
            input_data["image_diagram_request"] = request.image_diagram_request.model_dump() if request.image_diagram_request else {}
        elif step.tool_name == "phase_diagram_image_render":
            input_data["image_spec"] = state.image_spec or {}
        elif step.tool_name == "phase_diagram_repair":
            input_data.update(
                {
                    "diagram_request": state.diagram_request or (request.diagram_request.model_dump() if request.diagram_request else {}),
                    "generated_code": state.generated_code or "",
                    "stderr": state.stderr,
                }
            )
        elif step.tool_name == "phase_diagram_result_review":
            input_data.update(
                {
                    "diagram_request": state.diagram_request or (request.diagram_request.model_dump() if request.diagram_request else {}),
                    "html_redraw_request": request.html_redraw_request.model_dump() if request.html_redraw_request else {},
                    "generated_code": state.generated_code or "",
                    "html_content": state.html_content or "",
                    "stdout": state.stdout,
                    "stderr": state.stderr,
                }
            )
        elif step.tool_name == "phase_diagram_html_review":
            input_data.update(
                {
                    "html_redraw_request": request.html_redraw_request.model_dump() if request.html_redraw_request else {},
                    "html_content": state.html_content or "",
                }
            )
        elif step.tool_name == "python_execute":
            input_data["generated_code"] = state.generated_code or ""
        elif step.tool_name == "lammps_command_router":
            input_data.update({"user_input": request.user_input, "context": request.context})
        return input_data

    def _apply_result(
        self,
        route: TaskRoute,
        step: PlanStep,
        result_output: dict[str, Any],
        result_metadata: dict[str, Any],
        success: bool,
        state: RuntimeSessionState,
    ) -> None:
        state.tool_outputs[step.tool_name] = result_output
        if result_metadata:
            state.route_metadata.setdefault("tool_metadata", {})[step.tool_name] = result_metadata
        if "prompt" in result_output:
            state.prompt = result_output.get("prompt") or state.prompt

        if step.tool_name == "phase_diagram_codegen" and success:
            state.generated_code = result_output.get("generated_code")
        elif step.tool_name == "phase_diagram_html_redraw" and success:
            state.html_content = result_output.get("html_content")
            state.html_path = result_output.get("html_path")
            state.stdout = result_output.get("generation_source", state.stdout)
        elif step.tool_name == "phase_diagram_image_parse" and success:
            state.image_spec = result_output.get("image_spec")
            state.stdout = result_output.get("summary", state.stdout)
        elif step.tool_name == "phase_diagram_image_render" and success:
            state.html_content = result_output.get("html_content")
            state.html_path = result_output.get("html_path")
            state.stdout = result_output.get("summary", state.stdout)
        elif step.tool_name == "phase_diagram_repair":
            state.repair_attempts += 1
            if success:
                state.generated_code = result_output.get("generated_code")
            else:
                state.generated_code = None
                quality_issues = result_output.get("quality_issues", [])
                state.stderr = "\n".join(quality_issues) or state.stderr
        elif step.tool_name == "phase_diagram_result_review":
            state.review_passed = bool(result_output.get("review_passed"))
            state.review_summary = result_output.get("review_summary", "")
            state.review_confidence = result_output.get("review_confidence")
            state.review_issues = list(result_output.get("review_issues", []))
            state.route_metadata["review"] = {
                "passed": state.review_passed,
                "summary": state.review_summary,
                "confidence": state.review_confidence,
                "issues": state.review_issues,
                "mode": result_output.get("review_mode"),
            }
        elif step.tool_name == "phase_diagram_html_review":
            state.review_passed = bool(result_output.get("review_passed"))
            state.review_summary = result_output.get("review_summary", "")
            state.review_confidence = result_output.get("review_confidence")
            state.review_issues = list(result_output.get("review_issues", []))
            state.route_metadata["review"] = {
                "passed": state.review_passed,
                "summary": state.review_summary,
                "confidence": state.review_confidence,
                "issues": state.review_issues,
                "mode": result_output.get("review_mode"),
            }
        elif step.tool_name == "python_execute":
            state.stdout = result_output.get("stdout", "")
            state.stderr = result_output.get("stderr", "")
            state.html_content = result_output.get("html_content")
            state.html_path = result_output.get("html_path")
        elif step.tool_name == "lammps_command_router":
            state.route_metadata["lammps_outline"] = {
                "intent": result_output.get("intent"),
                "tool_chain_outline": result_output.get("tool_chain_outline", []),
                "next_actions": result_output.get("next_actions", []),
                "workspace_status": result_output.get("workspace_status"),
            }
            if result_output.get("message"):
                state.stdout = result_output["message"]

        if success and state.html_content:
            state.route_metadata["final_artifact"] = {"kind": "html", "path": state.html_path}

    def _should_stop_after_step(self, route: TaskRoute, step: PlanStep, success: bool, state: RuntimeSessionState) -> bool:
        if route.name == "phase_diagram.recognize" and step.tool_name == "phase_diagram_image_parse" and success and state.image_spec:
            return True
        if step.tool_name == "phase_diagram_image_render" and success and state.html_content:
            self.artifact_service.write_latest_html(state.html_content)
            return True
        if step.tool_name in {"phase_diagram_result_review", "phase_diagram_html_review"}:
            if success and state.html_content:
                self.artifact_service.write_latest_html(state.html_content)
                return True
        return False

    def _mark_step_skipped(
        self,
        run_id: str,
        step: PlanStep,
        event_sink: Callable[[AgentStreamEvent], None] | None,
        reason: str,
    ) -> None:
        step.status = "skipped"
        step.metadata["skip_reason"] = reason
        self._emit_event(event_sink, AgentStreamEvent(type="step_skipped", run_id=run_id, payload={"step": step.model_dump()}))

    def _build_missing_tool_observation(self, step: PlanStep) -> ToolObservation:
        return ToolObservation(
            step_index=step.index,
            tool_name=step.tool_name,
            success=False,
            summary=f"Tool '{step.tool_name}' is not registered in the current runtime.",
            input=step.input,
            output={},
            artifacts=[],
            metadata={"failure_kind": "missing_tool"},
            state_delta={},
        )

    def run(self, request: AgentRunRequest, event_sink: Callable[[AgentStreamEvent], None] | None = None) -> AgentRunResponse:
        run_id = self.artifact_service.create_run_id()
        route = self.task_router.route(request)
        plan_steps = self.planner_service.build_plan(route)
        state = RuntimeSessionState(route_metadata=self._base_route_metadata(route))
        if request.diagram_request:
            state.diagram_request = request.diagram_request.model_dump()
        if request.context.get("agent_decision"):
            state.route_metadata["agent_decision"] = self._sanitize_payload(request.context["agent_decision"])

        self._emit_event(
            event_sink,
            AgentStreamEvent(
                type="run_started",
                run_id=run_id,
                payload={
                    "route": route.model_dump(),
                    "plan_steps": [step.model_dump() for step in plan_steps],
                    "metadata": state.route_metadata,
                },
            ),
        )

        observations: list[ToolObservation] = []
        response_artifacts: list[ArtifactRef] = []
        state.route_metadata["plan_signature"] = [step.tool_name for step in plan_steps]
        state.route_metadata["plan_stages"] = [step.stage for step in plan_steps]
        state.route_metadata["plan_step_count"] = len(plan_steps)

        for step in plan_steps[: settings.agent_max_steps]:
            skip_reason = self._skip_reason(step, request, state)
            if skip_reason:
                self._mark_step_skipped(run_id, step, event_sink, skip_reason)
                continue

            step.input = self._resolve_step_input(step, request, state)
            if not self.tool_registry.has(step.tool_name):
                step.status = "failed"
                step.metadata["failure_kind"] = "missing_tool"
                observation = self._build_missing_tool_observation(step)
                observations.append(observation)
                state.termination_reason = "missing_tool"
                self._emit_event(
                    event_sink,
                    AgentStreamEvent(
                        type="step_failed",
                        run_id=run_id,
                        payload={"step": step.model_dump(), "observation": observation.model_dump()},
                    ),
                )
                break

            step.status = "running"
            self._emit_event(event_sink, AgentStreamEvent(type="step_started", run_id=run_id, payload={"step": step.model_dump()}))
            tool = self.tool_registry.get(step.tool_name)
            result = tool.run(step.input, self._build_tool_context(request, route, run_id, state))
            step.status = "completed" if result.success else "failed"

            sanitized_artifacts = self._sanitize_artifacts(result.artifacts)
            response_artifacts.extend(sanitized_artifacts)
            observation = ToolObservation(
                step_index=step.index,
                tool_name=step.tool_name,
                success=result.success,
                summary=result.summary,
                input=self._sanitize_payload(step.input),
                output=self._sanitize_observation_output(step.tool_name, result.output),
                artifacts=sanitized_artifacts,
                metadata=result.metadata,
                state_delta=result.state_delta,
            )
            observations.append(observation)
            self._apply_result(route, step, result.output, result.metadata, result.success, state)

            self._emit_event(
                event_sink,
                AgentStreamEvent(
                    type="step_completed" if result.success else "step_failed",
                    run_id=run_id,
                    payload={
                        "step": step.model_dump(),
                        "observation": observation.model_dump(),
                    },
                ),
            )

            if self._should_stop_after_step(route, step, result.success, state):
                break

        if not plan_steps:
            state.termination_reason = "no_supported_tool" if route.workspace_id != "phase_diagram" else "no_plan_steps"

        for step in plan_steps:
            if step.status == "pending":
                self._mark_step_skipped(run_id, step, event_sink, "The run completed before this step was needed.")

        lammps_stub_success = route.workspace_id == "lammps" and bool(plan_steps) and all(step.status != "failed" for step in plan_steps)
        if lammps_stub_success and not state.html_content:
            state.termination_reason = "stub_completed"
        recognition_success = route.name == "phase_diagram.recognize" and bool(state.image_spec)
        html_success = bool(state.html_content) and bool(state.html_path)
        if route.name in {"phase_diagram.generate", "phase_diagram.repair", "phase_diagram.redraw_html"}:
            if html_success and state.review_passed is False and state.termination_reason == "completed":
                state.termination_reason = "review_failed"
            elif not html_success and state.termination_reason == "completed":
                state.termination_reason = "execution_failed"

        success = (
            lammps_stub_success
            or recognition_success
            or (html_success and (route.name == "phase_diagram.from_image" or state.review_passed is not False))
        )
        final_message = self._build_final_message(route, success, bool(plan_steps), state.termination_reason)

        response_metadata = {
            **state.route_metadata,
            "prompt": state.prompt or "",
            "diagram_request": self._sanitize_payload(state.diagram_request or {}),
            "image_spec": self._sanitize_payload(state.image_spec or {}),
            "html_redraw_request": self._sanitize_payload(request.html_redraw_request.model_dump() if request.html_redraw_request else {}),
            "review": self._sanitize_payload(state.route_metadata.get("review", {})),
            "session_state": state.snapshot(route),
        }
        trace = RunTrace(
            run_id=run_id,
            route=route,
            steps=plan_steps,
            observations=observations,
            termination_reason=state.termination_reason,
            metadata=response_metadata,
        )
        self.artifact_service.write_trace(trace)

        response = AgentRunResponse(
            success=success,
            run_id=run_id,
            route=route,
            final_message=final_message,
            artifacts=response_artifacts,
            plan_steps=plan_steps,
            trace=observations,
            generated_code=state.generated_code,
            stdout=state.stdout,
            stderr=state.stderr,
            html_content=state.html_content,
            html_path=state.html_path,
            termination_reason=state.termination_reason,
            metadata=response_metadata,
        )
        self._emit_event(
            event_sink,
            AgentStreamEvent(
                type="run_completed" if success else "run_error",
                run_id=run_id,
                payload={
                    "response": response.model_dump(exclude={"html_content"}),
                    "html_ready": bool(state.html_path),
                },
            ),
        )
        return response
