from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.config import settings
from app.thermo.prompts import PromptBuilder
from app.state import (
    AgentChatRequest,
    AgentRunResponse,
    AgentStreamEvent,
    ArtifactRef,
    DiagramRequest,
    PlanStep,
    RecognitionResult,
    RunTrace,
    TaskRoute,
    ToolObservation,
)
from app.core.artifacts import ArtifactService
from app.runtimes.telemetry import build_runtime_execution_profile, initialize_runtime_state
from app.thermo.codegen import CodeGenerationService
from app.core.executor import LocalPythonExecutor
from app.thermo.service import PhaseDiagramAgentService
from app.thermo.accuracy import build_thermo_accuracy_report
from app.thermo.registry import get_thermo_database_card


class PhaseDiagramRuntime:
    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        codegen_service: CodeGenerationService | None = None,
        executor: LocalPythonExecutor | None = None,
        phase_agent_service: PhaseDiagramAgentService | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.codegen_service = codegen_service or CodeGenerationService(prompt_builder=PromptBuilder())
        self.phase_agent_service = phase_agent_service or PhaseDiagramAgentService(codegen_service=self.codegen_service)
        self.executor = executor or LocalPythonExecutor(
            artifact_service=self.artifact_service,
            python_executable=settings.python_executable,
        )

    @staticmethod
    def _merge_artifacts(current: list[ArtifactRef], new_items: list[ArtifactRef]) -> list[ArtifactRef]:
        merged = list(current)
        existing_keys = {(item.kind, item.name, item.path) for item in merged}
        for item in new_items:
            key = (item.kind, item.name, item.path)
            if key in existing_keys:
                continue
            merged.append(item)
            existing_keys.add(key)
        return merged

    @staticmethod
    def _build_accuracy_payload(request: DiagramRequest, system_name: str) -> dict[str, Any]:
        card = get_thermo_database_card(system_name)
        if card is None:
            return {"available": False, "passed": False}
        try:
            report = build_thermo_accuracy_report(
                card,
                temperature_min=request.temperature_min,
                temperature_max=request.temperature_max,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "available": True,
                "passed": False,
                "system_name": system_name,
                "endpoint_estimates": [],
                "stable_phases_seen": [],
                "missing_required_phases": [],
                "error": str(exc),
            }
        return {
            "available": True,
            "passed": report.passes,
            "system_name": report.system_name,
            "endpoint_estimates": [asdict(item) | {"midpoint_K": item.midpoint_K, "passes": item.passes} for item in report.endpoint_estimates],
            "stable_phases_seen": list(report.stable_phases_seen),
            "missing_required_phases": list(report.missing_required_phases),
        }

    @staticmethod
    def _valid_axis_range(minimum: float | None, maximum: float | None) -> tuple[float, float] | None:
        if minimum is None or maximum is None:
            return None
        if maximum <= minimum:
            return None
        return float(minimum), float(maximum)

    @classmethod
    def _recognition_notes(cls, recognition_result: RecognitionResult) -> str:
        parts: list[str] = []
        if recognition_result.system:
            parts.append(f"recognized_system={recognition_result.system}")
        if recognition_result.diagram_type:
            parts.append(f"recognized_diagram_type={recognition_result.diagram_type}")
        x_axis = recognition_result.x_axis
        y_axis = recognition_result.y_axis
        if x_axis.label or x_axis.unit or x_axis.minimum is not None or x_axis.maximum is not None:
            parts.append(
                "recognized_x_axis="
                f"{x_axis.label or 'unknown'}"
                f"{f' [{x_axis.unit}]' if x_axis.unit else ''}"
                f"{f' {x_axis.minimum}-{x_axis.maximum}' if x_axis.minimum is not None and x_axis.maximum is not None else ''}"
            )
        if y_axis.label or y_axis.unit or y_axis.minimum is not None or y_axis.maximum is not None:
            parts.append(
                "recognized_y_axis="
                f"{y_axis.label or 'unknown'}"
                f"{f' [{y_axis.unit}]' if y_axis.unit else ''}"
                f"{f' {y_axis.minimum}-{y_axis.maximum}' if y_axis.minimum is not None and y_axis.maximum is not None else ''}"
            )
        if recognition_result.phases:
            parts.append(f"recognized_phases={', '.join(recognition_result.phases[:8])}")
        if recognition_result.labels:
            parts.append(f"recognized_labels={', '.join(recognition_result.labels[:8])}")
        if recognition_result.critical_points:
            point_summary = []
            for point in recognition_result.critical_points[:5]:
                point_summary.append(
                    f"{point.label or 'point'}(x={point.composition if point.composition is not None else '?'},T={point.temperature if point.temperature is not None else '?'})"
                )
            parts.append(f"recognized_critical_points={'; '.join(point_summary)}")
        if recognition_result.raw_summary:
            parts.append(f"recognition_summary={recognition_result.raw_summary}")
        return " | ".join(part.strip() for part in parts if part.strip())

    def _record_step(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        success: bool,
        summary: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        description: str,
        stage: str,
        retryable: bool = False,
        artifacts: list[ArtifactRef] | None = None,
        metadata: dict[str, Any] | None = None,
        state_delta: dict[str, Any] | None = None,
    ) -> None:
        step_index = len(state["plan_steps"]) + 1
        step = PlanStep(
            index=step_index,
            tool_name=tool_name,
            input=input_data,
            status="running",
            retryable=retryable,
            description=description,
            stage=stage,
            metadata=metadata or {},
        )
        self._emit(
            state,
            AgentStreamEvent(
                type="step_started",
                run_id=state["run_id"],
                payload={"plan_step": step.model_dump(mode="json")},
            ),
        )
        final_step = step.model_copy(update={"status": "completed" if success else "failed"})
        observation = ToolObservation(
            step_index=step_index,
            tool_name=tool_name,
            success=success,
            summary=summary,
            input=input_data,
            output=output_data,
            artifacts=artifacts or [],
            metadata=metadata or {},
            state_delta=state_delta or {},
        )
        state["plan_steps"].append(final_step)
        state["trace"].append(observation)
        state["artifacts"] = self._merge_artifacts(state["artifacts"], artifacts or [])
        self._emit(
            state,
            AgentStreamEvent(
                type="step_completed" if success else "step_failed",
                run_id=state["run_id"],
                payload={
                    "plan_step": final_step.model_dump(mode="json"),
                    "observation": observation.model_dump(mode="json"),
                },
            ),
        )

    @staticmethod
    def _emit(state: dict[str, Any], event: AgentStreamEvent) -> None:
        sink = state.get("event_sink")
        if sink:
            sink(event)

    @staticmethod
    def _success_message(generation_source: str) -> str:
        if generation_source.startswith("llm_codegen"):
            return (
                "相图已生成。本轮 agent 已完成“热力学数据库检索 -> LLM 写 Python -> 本地 Python 执行 -> LLM 自检”。"
                "当前结果来自 pycalphad + TDB 的真实计算链路。"
            )
        return (
            "相图已生成。本轮 agent 已完成“热力学数据库检索 -> 本地 wrapper 生成 -> 本地 Python 执行 -> 结果自检”。"
            "当前结果来自 pycalphad + TDB 的真实计算链路。"
        )

    @staticmethod
    def _build_result_profile(state: dict[str, Any], success: bool) -> dict[str, Any]:
        review = state.get("review", {}) or {}
        accuracy = state.get("accuracy", {}) or {}
        planning = state.get("planning", {}) or {}
        thermo_lookup = state.get("thermo_lookup", {}) or {}
        generation_source = str(state.get("generation_source", "") or "")

        warnings = [str(item) for item in review.get("advisory_issues", []) if str(item).strip()]
        if accuracy.get("available") and not accuracy.get("passed"):
            warnings.append("Thermodynamic accuracy gate did not pass for this TDB-backed calculation.")
        if review.get("issues"):
            warnings.extend(str(item) for item in review.get("issues", []) if str(item).strip())

        assumptions = [
            "Result is produced by pycalphad equilibrium calculation using a registered thermodynamic database.",
            "Thin Python wrapper is generated by the agent, while the thermodynamic solve stays in the local calculation engine.",
        ]
        if planning.get("temperature_hint_source"):
            assumptions.append(f"Temperature range parsed from: {planning.get('temperature_hint_source')}.")

        evidence = []
        database_name = str(thermo_lookup.get("database_name") or "")
        if database_name:
            evidence.append(f"Database: {database_name}")
        if generation_source:
            evidence.append(f"Wrapper source: {generation_source}")
        if accuracy.get("available"):
            evidence.append(
                f"Accuracy gate: {'passed' if accuracy.get('passed') else 'failed'}"
            )

        confidence = review.get("confidence")
        try:
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_value = None
        if confidence_value is None and accuracy.get("available"):
            confidence_value = 0.94 if accuracy.get("passed") else 0.42

        if success and accuracy.get("passed"):
            trust_level = "high"
            trust_statement = "This is a TDB-backed calculated result that passed both review and the thermodynamic accuracy gate."
        elif success:
            trust_level = "medium"
            trust_statement = "This is a TDB-backed calculated result, but it should be interpreted with review warnings in mind."
        else:
            trust_level = "low"
            trust_statement = "This run did not complete a trustworthy calculated result and should be treated as diagnostic output."

        return {
            "category": "Calculated",
            "source_label": "pycalphad + TDB",
            "mode_label": "tdb_equilibrium_calculation",
            "trust_level": trust_level,
            "confidence": round(confidence_value, 2) if confidence_value is not None else None,
            "trust_statement": trust_statement,
            "assumptions": assumptions,
            "warnings": warnings,
            "evidence": evidence,
        }

    def _build_request(self, request: AgentChatRequest, recognition_result: RecognitionResult | None) -> tuple[DiagramRequest, dict[str, Any]]:
        overrides = {
            "system_name": request.system_name,
            "diagram_type": request.diagram_type,
            "temperature_min": request.temperature_min,
            "temperature_max": request.temperature_max,
            "pressure": request.pressure,
            "step_size": request.step_size,
            "notes": request.notes,
        }
        recognition_note = ""
        if recognition_result:
            if recognition_result.system and not overrides["system_name"]:
                overrides["system_name"] = recognition_result.system
            if recognition_result.diagram_type:
                overrides["diagram_type"] = recognition_result.diagram_type
            recognized_temperature = self._valid_axis_range(
                recognition_result.y_axis.minimum,
                recognition_result.y_axis.maximum,
            )
            if recognized_temperature:
                default_temp_range = (
                    request.temperature_min == AgentChatRequest.model_fields["temperature_min"].default
                    and request.temperature_max == AgentChatRequest.model_fields["temperature_max"].default
                )
                if default_temp_range:
                    overrides["temperature_min"], overrides["temperature_max"] = recognized_temperature
            recognition_note = self._recognition_notes(recognition_result)
        diagram_request, planning = self.phase_agent_service.infer_request_from_chat(request.message, overrides)
        if recognition_note and recognition_note not in (diagram_request.notes or ""):
            diagram_request = diagram_request.model_copy(
                update={"notes": f"{diagram_request.notes}\n{recognition_note}".strip()}
            )
        return diagram_request, planning

    def _build_state(
        self,
        *,
        run_id: str,
        decision: dict[str, Any] | None,
        event_sink=None,
        existing_plan_steps: list[PlanStep] | None = None,
        existing_trace: list[ToolObservation] | None = None,
        existing_artifacts: list[ArtifactRef] | None = None,
    ) -> dict[str, Any]:
        route = TaskRoute(
            name="phase_diagram.generate" if (decision or {}).get("route_name") != "mixed.request" else "mixed.request",
            workspace_id="materials_agent",
            reason=str((decision or {}).get("reason") or "Supervisor selected the local phase-diagram runtime."),
            selected_tool="thermo_database_lookup",
            intent=str((decision or {}).get("intent") or "generate_phase_diagram"),
            decision_source=str((decision or {}).get("source") or "phase_diagram_runtime"),
            decision_confidence=float((decision or {}).get("confidence")) if (decision or {}).get("confidence") is not None else None,
            compute_domain="phase_diagram",
        )
        state = {
            "run_id": run_id,
            "route": route,
            "plan_steps": list(existing_plan_steps or []),
            "trace": list(existing_trace or []),
            "artifacts": list(existing_artifacts or []),
            "thermo_lookup": {},
            "planning": {},
            "generated_code": "",
            "generation_source": "",
            "stdout": "",
            "stderr": "",
            "html_content": None,
            "html_path": None,
            "review": {},
            "accuracy": {"available": False, "passed": False},
            "current_code_filename": settings.code_file_name,
            "event_sink": event_sink,
        }
        initialize_runtime_state(
            state,
            runtime_name="PhaseDiagramRuntime",
            capability_tags=[
                "pycalphad",
                "tdb_registry",
                "llm_codegen",
                "local_python_execute",
                "review_repair_loop",
            ],
        )
        return state

    def _run_with_structured_request(
        self,
        *,
        state: dict[str, Any],
        request_message: str,
        conversation_id: str,
        diagram_request: DiagramRequest,
        planning: dict[str, Any],
        recognition_result: RecognitionResult | None = None,
    ) -> AgentRunResponse:
        recognition_note = self._recognition_notes(recognition_result) if recognition_result else ""
        state["diagram_request"] = diagram_request
        state["planning"] = planning
        self._record_step(
            state,
            tool_name="request_interpreter",
            success=True,
            summary=(
                f"任务已解析为 {diagram_request.system_name} {diagram_request.diagram_type} 相图，"
                f"温度范围 {diagram_request.temperature_min:.0f}-{diagram_request.temperature_max:.0f} K。"
            ),
            input_data={"message": request_message or diagram_request.system_name},
            output_data={"diagram_request": diagram_request.model_dump(mode="json"), "planning": planning},
            description="Interpret the user request into a structured phase-diagram task.",
            stage="chat_to_request",
            state_delta={"system_name": diagram_request.system_name},
        )

        thermo_query_text = "\n".join(
            part
            for part in (
                request_message,
                diagram_request.system_name,
                diagram_request.notes,
                recognition_note,
            )
            if part and part.strip()
        )
        thermo_card, retrieval = self.phase_agent_service.lookup_registered_database(
            diagram_request.system_name,
            query_text=thermo_query_text,
        )
        state["thermo_lookup"] = retrieval
        if thermo_card is None:
            self._record_step(
                state,
                tool_name="thermo_database_lookup",
                success=False,
                summary=f"未找到 {diagram_request.system_name} 对应的热力学数据库注册项，无法进入真实 TDB 计算。",
                input_data={"system_name": diagram_request.system_name},
                output_data=retrieval,
                description="Retrieve a registered thermodynamic database before code generation.",
                stage="thermo_registry_lookup",
                state_delta={"registry_match": False},
            )
            return self._finalize(
                state,
                success=False,
                final_message="这次没有进入真实热力学计算，因为当前 registry 里还没有这个体系对应的 TDB 文件。我已保留 trace，方便继续补库。",
                termination_reason="thermo_database_not_found",
                conversation_id=conversation_id,
                recognition_result=recognition_result,
            )

        self._record_step(
            state,
            tool_name="thermo_database_lookup",
            success=True,
            summary=(
                f"已命中热力学数据库 {thermo_card['database_name']}，准备进入 pycalphad 计算代码生成。"
                if retrieval.get("selection_strategy") != "rag_auto_select"
                else f"thermo RAG 已召回 {thermo_card['system_name']}，并选中 {thermo_card['database_name']} 进入 pycalphad 计算。"
            ),
            input_data={"system_name": diagram_request.system_name},
            output_data=retrieval,
            description="Retrieve a registered thermodynamic database before code generation.",
            stage="thermo_registry_lookup",
            state_delta={
                "registry_match": True,
                "database_name": thermo_card["database_name"],
                "lookup_mode": retrieval.get("lookup_mode", retrieval.get("selection_strategy", "exact")),
            },
        )

        max_codegen_attempts = max(1, min(3, settings.agent_max_steps // 3 or 1))
        max_repair_attempts = max(1, settings.agent_max_repair_attempts)
        codegen_attempt = 0
        repair_attempt = 0

        while codegen_attempt < max_codegen_attempts:
            codegen_attempt += 1
            try:
                generated_code, generation_source = self.codegen_service.generate_code_with_source(diagram_request)
                state["generated_code"] = generated_code
                state["generation_source"] = generation_source
                code_artifact_name = f"generated_code_attempt_{codegen_attempt}.py"
                state["current_code_filename"] = code_artifact_name
                code_artifact = self.artifact_service.build_artifact_ref(
                    kind="code",
                    name=code_artifact_name,
                    path=self.artifact_service.get_code_path(state["run_id"], code_artifact_name),
                    url=self.artifact_service.build_artifact_url(state["run_id"], code_artifact_name),
                    content=generated_code,
                    metadata={"generation_source": generation_source, "attempt": codegen_attempt},
                )
                self._record_step(
                    state,
                    tool_name="phase_diagram_codegen",
                    success=True,
                    summary="LLM 已生成可执行的 Python 相图 wrapper 代码。",
                    input_data={"system_name": diagram_request.system_name, "attempt": codegen_attempt},
                    output_data={"generation_source": generation_source, "code_length": len(generated_code)},
                    description="Generate thin Python wrapper code for the local pycalphad calculation helper.",
                    stage=f"codegen_attempt_{codegen_attempt}",
                    retryable=True,
                    artifacts=[code_artifact],
                    state_delta={"generation_source": generation_source},
                )
            except Exception as exc:  # noqa: BLE001
                self._record_step(
                    state,
                    tool_name="phase_diagram_codegen",
                    success=False,
                    summary=f"代码生成失败：{exc}",
                    input_data={"system_name": diagram_request.system_name, "attempt": codegen_attempt},
                    output_data={"error": str(exc)},
                    description="Generate thin Python wrapper code for the local pycalphad calculation helper.",
                    stage=f"codegen_attempt_{codegen_attempt}",
                    retryable=True,
                )
                continue

            while repair_attempt <= max_repair_attempts:
                result = self.executor.execute(
                    run_id=state["run_id"],
                    code=state["generated_code"],
                    code_filename=state.get("current_code_filename"),
                )
                state["stdout"] = result.stdout
                state["stderr"] = result.stderr
                state["html_content"] = result.html_content
                state["html_path"] = result.html_path
                html_artifacts: list[ArtifactRef] = []
                if result.html_content:
                    html_artifacts.append(
                        self.artifact_service.build_artifact_ref(
                            kind="html",
                            name="result.html",
                            path=result.html_path,
                            url=self.artifact_service.build_artifact_url(state["run_id"], "result.html"),
                            metadata={"success": result.success},
                        )
                    )

                self._record_step(
                    state,
                    tool_name="python_execute",
                    success=result.success,
                    summary="本地 Python 已执行完成，准备进入自检。" if result.success else "本地 Python 执行失败，准备进入修复。",
                    input_data={"run_id": state["run_id"]},
                    output_data={"stdout": result.stdout[:1200], "stderr": result.stderr[:1200], "html_path": result.html_path},
                    description="Execute the generated Python wrapper locally.",
                    stage=f"execute_after_codegen_{codegen_attempt}",
                    retryable=True,
                    artifacts=html_artifacts,
                    state_delta={"html_ready": bool(result.html_content), "stderr_present": bool(result.stderr.strip())},
                )

                if not result.success:
                    if repair_attempt >= max_repair_attempts:
                        break
                    repaired = self.codegen_service.repair_code(
                        diagram_request,
                        state["generated_code"],
                        result.stderr or "Python execution failed.",
                    )
                    if not repaired:
                        break
                    repaired, quality_errors = self.codegen_service.sanitize_and_validate_code(diagram_request, repaired)
                    if quality_errors:
                        break
                    repair_attempt += 1
                    state["generated_code"] = repaired
                    repair_artifact_name = f"repaired_code_attempt_{repair_attempt}.py"
                    state["current_code_filename"] = repair_artifact_name
                    repair_artifact = self.artifact_service.build_artifact_ref(
                        kind="code",
                        name=repair_artifact_name,
                        path=self.artifact_service.get_code_path(state["run_id"], repair_artifact_name),
                        url=self.artifact_service.build_artifact_url(state["run_id"], repair_artifact_name),
                        content=repaired,
                        metadata={"repair_attempt": repair_attempt},
                    )
                    self._record_step(
                        state,
                        tool_name="phase_diagram_repair",
                        success=True,
                        summary="LLM 已根据执行/审查反馈修复代码，准备重新执行。",
                        input_data={"repair_attempt": repair_attempt},
                        output_data={"repair_context": (result.stderr or "review failed")[:1200]},
                        description="Repair the generated Python code using execution and review feedback.",
                        stage=f"repair_attempt_{repair_attempt}",
                        retryable=True,
                        artifacts=[repair_artifact],
                    )
                    continue

                review = self.phase_agent_service.review_generated_artifact(
                    request=diagram_request,
                    generated_code=state["generated_code"],
                    html_content=result.html_content or "",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
                accuracy = self._build_accuracy_payload(diagram_request, diagram_request.system_name)
                state["accuracy"] = accuracy
                if accuracy.get("available") and not accuracy.get("passed"):
                    review["passed"] = False
                    review["issues"] = [*review.get("issues", []), "Thermodynamic accuracy gate failed for the selected TDB system."]
                    if accuracy.get("error"):
                        review["issues"].append(f"Accuracy check error: {accuracy['error']}")
                    review["summary"] = "Agent review blocked this result because the thermodynamic accuracy gate did not pass."

                state["review"] = review
                self._record_step(
                    state,
                    tool_name="phase_diagram_result_review",
                    success=bool(review.get("passed")),
                    summary=str(review.get("summary") or ""),
                    input_data={"run_id": state["run_id"]},
                    output_data={
                        "review_passed": bool(review.get("passed")),
                        "review_confidence": review.get("confidence"),
                        "review_issues": review.get("issues", []),
                        "review_advisory_issues": review.get("advisory_issues", []),
                        "review_mode": review.get("review_mode", ""),
                        "accuracy": accuracy,
                    },
                    description="Review the wrapper code, artifact, and thermodynamic accuracy gate.",
                    stage=f"review_after_codegen_{codegen_attempt}",
                    retryable=True,
                    state_delta={"review_passed": bool(review.get("passed"))},
                )
                if review.get("passed"):
                    return self._finalize(
                        state,
                        success=True,
                        final_message=self._success_message(state.get("generation_source", "")),
                        termination_reason="review_passed",
                        conversation_id=conversation_id,
                        recognition_result=recognition_result,
                    )

                if repair_attempt >= max_repair_attempts:
                    break
                repaired = self.codegen_service.repair_code(
                    diagram_request,
                    state["generated_code"],
                    "\n".join(review.get("issues", []) or [str(review.get("summary") or "")]),
                )
                if not repaired:
                    break
                repaired, quality_errors = self.codegen_service.sanitize_and_validate_code(diagram_request, repaired)
                if quality_errors:
                    break
                repair_attempt += 1
                state["generated_code"] = repaired
                repair_artifact_name = f"repaired_code_attempt_{repair_attempt}.py"
                state["current_code_filename"] = repair_artifact_name
                repair_artifact = self.artifact_service.build_artifact_ref(
                    kind="code",
                    name=repair_artifact_name,
                    path=self.artifact_service.get_code_path(state["run_id"], repair_artifact_name),
                    url=self.artifact_service.build_artifact_url(state["run_id"], repair_artifact_name),
                    content=repaired,
                    metadata={"repair_attempt": repair_attempt},
                )
                self._record_step(
                    state,
                    tool_name="phase_diagram_repair",
                    success=True,
                    summary="LLM 已根据执行/审查反馈修复代码，准备重新执行。",
                    input_data={"repair_attempt": repair_attempt},
                    output_data={"repair_context": str(review.get("summary") or "")[:1200]},
                    description="Repair the generated Python code using execution and review feedback.",
                    stage=f"repair_attempt_{repair_attempt}",
                    retryable=True,
                    artifacts=[repair_artifact],
                )

        return self._finalize(
            state,
            success=False,
            final_message="这次相图没有通过完整自检，我已保留代码、trace 和错误信息，方便继续修复。",
            termination_reason="review_failed",
            conversation_id=conversation_id,
            recognition_result=recognition_result,
        )

    def run(
        self,
        *,
        run_id: str,
        request: AgentChatRequest,
        recognition_result: RecognitionResult | None = None,
        decision: dict[str, Any] | None = None,
        event_sink=None,
        existing_plan_steps: list[PlanStep] | None = None,
        existing_trace: list[ToolObservation] | None = None,
        existing_artifacts: list[ArtifactRef] | None = None,
    ) -> AgentRunResponse:
        state = self._build_state(
            run_id=run_id,
            decision=decision,
            event_sink=event_sink,
            existing_plan_steps=existing_plan_steps,
            existing_trace=existing_trace,
            existing_artifacts=existing_artifacts,
        )
        diagram_request, planning = self._build_request(request, recognition_result)
        return self._run_with_structured_request(
            state=state,
            request_message=request.message,
            conversation_id=request.conversation_id,
            diagram_request=diagram_request,
            planning=planning,
            recognition_result=recognition_result,
        )

    def run_structured(
        self,
        *,
        run_id: str,
        diagram_request: DiagramRequest | dict[str, Any],
        conversation_id: str = "mcp-phase-diagram-structured",
        request_message: str = "",
        recognition_result: RecognitionResult | None = None,
        decision: dict[str, Any] | None = None,
        event_sink=None,
        existing_plan_steps: list[PlanStep] | None = None,
        existing_trace: list[ToolObservation] | None = None,
        existing_artifacts: list[ArtifactRef] | None = None,
    ) -> AgentRunResponse:
        state = self._build_state(
            run_id=run_id,
            decision=decision,
            event_sink=event_sink,
            existing_plan_steps=existing_plan_steps,
            existing_trace=existing_trace,
            existing_artifacts=existing_artifacts,
        )
        structured_request = (
            diagram_request if isinstance(diagram_request, DiagramRequest) else DiagramRequest.model_validate(diagram_request)
        )
        planning = {
            "source": "structured_request",
            "message": request_message,
            "llm_confidence": 1.0,
            "selection_mode": "direct_structured_execute",
        }
        return self._run_with_structured_request(
            state=state,
            request_message=request_message,
            conversation_id=conversation_id,
            diagram_request=structured_request,
            planning=planning,
            recognition_result=recognition_result,
        )

    def _finalize(
        self,
        state: dict[str, Any],
        *,
        success: bool,
        final_message: str,
        termination_reason: str,
        conversation_id: str,
        recognition_result: RecognitionResult | None,
    ) -> AgentRunResponse:
        result_profile = self._build_result_profile(state, success)
        runtime_profile = build_runtime_execution_profile(
            state,
            success=success,
            termination_reason=termination_reason,
            result_profile=result_profile,
        )
        trace_model = RunTrace(
            run_id=state["run_id"],
            route=state["route"],
            steps=state["plan_steps"],
            observations=state["trace"],
            termination_reason=termination_reason,
            metadata={
                "planning": state.get("planning", {}),
                "thermo_lookup": state.get("thermo_lookup", {}),
                "generation_source": state.get("generation_source", ""),
                "review": state.get("review", {}),
                "accuracy": state.get("accuracy", {}),
                "runtime_profile": runtime_profile,
            },
        )
        trace_path = self.artifact_service.write_trace(trace_model)
        trace_artifact = self.artifact_service.build_artifact_ref(
            kind="json",
            name="trace.json",
            path=trace_path,
            url=self.artifact_service.build_artifact_url(state["run_id"], "trace.json"),
        )
        artifacts = self._merge_artifacts(state["artifacts"], [trace_artifact])
        summary = {
            "system_name": state["diagram_request"].system_name,
            "diagram_type": state["diagram_request"].diagram_type,
            "temperature_range": [state["diagram_request"].temperature_min, state["diagram_request"].temperature_max],
            "generation_source": state.get("generation_source", ""),
            "accuracy": state.get("accuracy", {}),
            "review": state.get("review", {}),
            "planning": state.get("planning", {}),
            "result_profile": result_profile,
            "runtime_profile": runtime_profile,
        }
        response = AgentRunResponse(
            success=success,
            run_id=state["run_id"],
            conversation_id=conversation_id,
            route=state["route"],
            final_message=final_message,
            artifacts=artifacts,
            plan_steps=state["plan_steps"],
            trace=state["trace"],
            generated_code=state.get("generated_code") or None,
            stdout=state.get("stdout") or "",
            stderr=state.get("stderr") or "",
            html_content=state.get("html_content"),
            html_path=state.get("html_path"),
            termination_reason=termination_reason,
            metadata={
                "planning": state.get("planning", {}),
                "thermo_lookup": state.get("thermo_lookup", {}),
                "generation_source": state.get("generation_source", ""),
                "review": state.get("review", {}),
                "accuracy": state.get("accuracy", {}),
                "result_profile": result_profile,
                "runtime_profile": runtime_profile,
            },
            recognition_result=recognition_result,
            current_context_summary="",
            summary=summary,
            run_status="completed" if success else "failed",
        )
        self.artifact_service.write_run_summary(response)
        return response
