from __future__ import annotations

from app.runtimes.lammps import LammpsRuntime
from app.runtimes.phase_diagram import PhaseDiagramRuntime
from app.state import AgentGraphState, AgentRunResponse, ConversationTurn, LastRunContext


class ComputeAgent:
    def __init__(self, *, phase_diagram_runtime: PhaseDiagramRuntime, lammps_runtime: LammpsRuntime) -> None:
        self.phase_diagram_runtime = phase_diagram_runtime
        self.lammps_runtime = lammps_runtime

    @staticmethod
    def _artifact_names(response: AgentRunResponse) -> list[str]:
        return [artifact.name for artifact in response.artifacts]

    def _build_phase_context(self, state: AgentGraphState, response: AgentRunResponse) -> LastRunContext:
        timeline = response.trace
        request_step = next((item for item in timeline if item.tool_name == "request_interpreter"), None)
        diagram_request = request_step.output.get("diagram_request", {}) if request_step else {}
        review = (response.metadata.get("review") or {}) if response.metadata else {}
        return LastRunContext(
            run_id=response.run_id,
            route_name=response.route.name,
            compute_domain="phase_diagram",
            system_name=str(diagram_request.get("system_name") or ""),
            final_message=response.final_message[:1200],
            generated_code_preview=(response.generated_code or "")[:2500],
            review_summary=str(review.get("summary") or ""),
            selected_tool=response.route.selected_tool or "",
            generation_source=str(response.metadata.get("generation_source") or ""),
            request_summary=(
                f"{diagram_request.get('system_name', '')} {diagram_request.get('diagram_type', '')} "
                f"{diagram_request.get('temperature_min', '')}-{diagram_request.get('temperature_max', '')} K"
            ).strip(),
            review_passed=review.get("passed") if isinstance(review.get("passed"), bool) else None,
            review_issues=[str(item) for item in review.get("issues", [])] if isinstance(review.get("issues"), list) else [],
            review_advisory_issues=[str(item) for item in review.get("advisory_issues", [])]
            if isinstance(review.get("advisory_issues"), list)
            else [],
            trace_summary=[f"{item.tool_name}: {item.summary}" for item in timeline[-8:]],
            recognition_summary=state.get("recognition_result").raw_summary if state.get("recognition_result") else "",
            artifact_names=self._artifact_names(response),
        )

    def _build_lammps_context(self, state: AgentGraphState, response: AgentRunResponse) -> LastRunContext:
        metrics = response.summary.get("metrics", {}) if isinstance(response.summary, dict) else {}
        validation = response.summary.get("validation", {}) if isinstance(response.summary, dict) else {}
        request_payload = next((item.output.get("request") for item in response.trace if item.tool_name == "lammps_request_interpreter"), {}) or {}
        review = (response.metadata.get("review") or {}) if response.metadata else {}
        request_summary = (
            f"{request_payload.get('material', '')} {request_payload.get('task_type', '')} "
            f"{request_payload.get('temperature', '')} K / {request_payload.get('steps', '')} steps"
        ).strip()
        if metrics:
            request_summary = f"{request_summary} | metrics={metrics}" if request_summary else f"metrics={metrics}"
        if validation.get("warnings"):
            request_summary = f"{request_summary} | warnings={validation.get('warnings')}"
        return LastRunContext(
            run_id=response.run_id,
            route_name=response.route.name,
            compute_domain="lammps",
            system_name=str(request_payload.get("material") or ""),
            final_message=response.final_message[:1200],
            generated_code_preview=(response.generated_code or "")[:2500],
            review_summary=str(review.get("summary") or ""),
            selected_tool=response.route.selected_tool or "",
            generation_source=str(response.metadata.get("run_mode") or ""),
            request_summary=request_summary,
            review_passed=review.get("passed") if isinstance(review.get("passed"), bool) else None,
            review_issues=[str(item) for item in review.get("issues", [])] if isinstance(review.get("issues"), list) else [],
            review_advisory_issues=[str(item) for item in review.get("advisory_issues", [])]
            if isinstance(review.get("advisory_issues"), list)
            else [],
            trace_summary=[f"{item.tool_name}: {item.summary}" for item in response.trace[-8:]],
            recognition_summary=state.get("recognition_result").raw_summary if state.get("recognition_result") else "",
            artifact_names=self._artifact_names(response),
        )

    @staticmethod
    def _build_messages(state: AgentGraphState, response: AgentRunResponse) -> list[ConversationTurn]:
        _ = response
        return list(state.get("messages", []))

    def run(self, state: AgentGraphState, decision: dict[str, object]) -> dict[str, object]:
        compute_domain = str(state.get("compute_domain") or decision.get("compute_domain") or "none")
        if compute_domain == "phase_diagram":
            response = self.phase_diagram_runtime.run(
                run_id=state["run_id"],
                request=state["request"],
                recognition_result=state.get("recognition_result"),
                decision=decision,
                event_sink=state.get("event_sink"),
                existing_plan_steps=state.get("plan_steps", []),
                existing_trace=state.get("trace", []),
                existing_artifacts=state.get("artifact_messages", []),
                shared_memory_context=state.get("shared_memory_context", {}),
            )
            response.conversation_id = state["conversation_id"]
            last_run_context = self._build_phase_context(state, response)
            return {
                "route": response.route,
                "phase_diagram_result": response,
                "plan_steps": response.plan_steps,
                "trace": response.trace,
                "artifact_messages": response.artifacts,
                "messages": self._build_messages(state, response),
                "last_run_context": last_run_context,
                "success": response.success,
                "termination_reason": response.termination_reason,
                "response_metadata": response.metadata,
                "error": "" if response.success else response.final_message,
            }

        if compute_domain == "lammps":
            response = self.lammps_runtime.run(
                run_id=state["run_id"],
                request=state["request"],
                recognition_result=state.get("recognition_result"),
                decision=decision,
                event_sink=state.get("event_sink"),
                existing_plan_steps=state.get("plan_steps", []),
                existing_trace=state.get("trace", []),
                existing_artifacts=state.get("artifact_messages", []),
            )
            response.conversation_id = state["conversation_id"]
            last_run_context = self._build_lammps_context(state, response)
            return {
                "route": response.route,
                "lammps_result": response,
                "plan_steps": response.plan_steps,
                "trace": response.trace,
                "artifact_messages": response.artifacts,
                "messages": self._build_messages(state, response),
                "last_run_context": last_run_context,
                "success": response.success,
                "termination_reason": response.termination_reason,
                "response_metadata": response.metadata,
                "error": "" if response.success else response.final_message,
            }

        raise RuntimeError("ComputeAgent received no valid compute_domain, so it could not select a runtime.")
