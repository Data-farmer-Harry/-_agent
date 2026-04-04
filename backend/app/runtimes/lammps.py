from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.artifacts import ArtifactService
from app.core.cancellation import RunCancelledError, clear_cancellation
from app.core.llm import LLMClient, LLMRequiredError
from app.lammps.attachments import infer_request_overrides, persist_uploaded_assets
from app.lammps.config import LammpsConfig, lammps_config_public_payload, load_lammps_config
from app.lammps.postprocess import convert_dump, generate_diffusion_trajectory_if_applicable, generate_plot
from app.lammps.registry import get_lammps_registry_payload, get_supported_materials, get_supported_potentials, get_supported_tasks
from app.lammps.runner import run_lammps, run_mock
from app.lammps.template import get_lammps_form_schema, generate_lammps_input
from app.lammps.validator import validate_request
from app.state import (
    AgentChatRequest,
    AgentRunResponse,
    AgentStreamEvent,
    ArtifactRef,
    LammpsRequest,
    PlanStep,
    RecognitionResult,
    RunRecordSummary,
    RunStatus,
    RunTrace,
    TaskRoute,
    ToolObservation,
)
from app.utils.path_utils import write_json_file


class LammpsRuntime:
    MATERIAL_ALIASES = {
        "al": "Al",
        "aluminum": "Al",
        "aluminium": "Al",
        "铝": "Al",
        "cu": "Cu",
        "copper": "Cu",
        "铜": "Cu",
        "ni": "Ni",
        "nickel": "Ni",
        "镍": "Ni",
    }

    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        llm_client: LLMClient | None = None,
        config_loader=load_lammps_config,
    ) -> None:
        self.artifact_service = artifact_service
        self.llm_client = llm_client or LLMClient()
        self.config_loader = config_loader

    @staticmethod
    def _emit(state: dict[str, Any], event: AgentStreamEvent) -> None:
        sink = state.get("event_sink")
        if sink:
            sink(event)

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
            AgentStreamEvent(type="step_started", run_id=state["run_id"], payload={"plan_step": step.model_dump(mode="json")}),
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

    def _build_artifact(
        self,
        *,
        run_id: str,
        kind: str,
        name: str,
        path: Path | str | None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        return self.artifact_service.build_artifact_ref(
            kind=kind,
            name=name,
            path=path,
            url=self.artifact_service.build_artifact_url(run_id, name) if path else None,
            content=content,
            metadata=metadata or {},
        )

    @staticmethod
    def _is_infrastructure_issue(error_text: str) -> bool:
        lowered = (error_text or "").lower()
        return any(
            marker in lowered
            for marker in (
                "lammps executable not found",
                "potentials_dir is not configured",
                "potential file not found",
                "structure file not found",
            )
        )

    @classmethod
    def _heuristic_request(cls, message: str, notes: str, attachment_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = message.lower()
        material = ""
        for alias, normalized in cls.MATERIAL_ALIASES.items():
            if alias.isascii():
                if re.search(rf"\b{re.escape(alias)}\b", raw):
                    material = normalized
                    break
            elif alias in raw:
                material = normalized
                break
        task_type = "heating" if any(token in raw for token in ("heat", "heating", "升温")) else "equilibration"
        potential_family = "lj" if "lj" in raw or "lennard-jones" in raw else "eam"
        temperature_match = re.search(r"(\d{2,5})\s*(k|kelvin)", raw)
        steps_match = re.search(r"(\d{3,7})\s*steps?", raw) or re.search(r"步数\s*(\d{3,7})", raw)
        request_payload = {
            "material": material,
            "potential_family": potential_family,
            "task_type": task_type,
            "temperature": int(temperature_match.group(1)) if temperature_match else 900,
            "steps": int(steps_match.group(1)) if steps_match else 5000,
            "ensemble": "NVT",
            "box_size": 4,
            "time_step": 0.001,
            "dump_file": "dump.atom",
            "notes": notes or message.strip(),
        }
        if attachment_overrides:
            request_payload.update({key: value for key, value in attachment_overrides.items() if value})
            if attachment_overrides.get("custom_potential_path"):
                request_payload["potential_family"] = "eam"
        return request_payload

    def _parse_request(
        self,
        request: AgentChatRequest,
        *,
        attachment_overrides: dict[str, Any] | None = None,
        attachment_context: list[dict[str, Any]] | None = None,
    ) -> tuple[LammpsRequest, dict[str, Any]]:
        heuristic = self._heuristic_request(request.message, request.notes, attachment_overrides)
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ComputeAgent", capability="LAMMPS 结构化请求解析")
            return LammpsRequest.model_validate(heuristic), {"source": "heuristic_request_interpreter", "confidence": 0.55}

        registry = get_lammps_registry_payload()
        try:
            payload = self.llm_client.chat_json(
                system_prompt=(
                    "You are the LammpsRuntime request interpreter for a true multi-agent materials system. "
                    "Convert the user request into conservative JSON for a single-metal LAMMPS demo. "
                    "Return JSON only with keys: material, potential_family, task_type, temperature, steps, ensemble, box_size, initial_temp, time_step, dump_file, custom_potential_path, custom_structure_path, custom_structure_format, notes, confidence."
                ),
                user_prompt=(
                    f"User message:\n{request.message}\n\n"
                    f"Caller notes:\n{request.notes}\n\n"
                    f"Uploaded assets:\n{json.dumps(attachment_context or [], ensure_ascii=False)}\n\n"
                    f"Registry:\n{json.dumps(registry, ensure_ascii=False)}\n\n"
                    f"Heuristic baseline:\n{json.dumps(heuristic, ensure_ascii=False)}"
                ),
                max_tokens=900,
                temperature=0.1,
            )
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"ComputeAgent 在解析 LAMMPS 请求时调用 LLM 失败：{exc}") from exc
            return LammpsRequest.model_validate(heuristic), {"source": "heuristic_request_interpreter", "confidence": 0.55}

        if not payload:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("ComputeAgent 需要结构化 LLM 结果来解析 LAMMPS 请求，但本次没有得到有效 JSON。")
            return LammpsRequest.model_validate(heuristic), {"source": "heuristic_request_interpreter", "confidence": 0.55}

        merged = {**heuristic, **{key: value for key, value in payload.items() if value not in (None, "")}}
        structured = LammpsRequest.model_validate(merged)
        confidence = payload.get("confidence", 0.82)
        try:
            score = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            score = 0.82
        return structured, {"source": "llm_request_interpreter", "confidence": score}

    def _repair_request(
        self,
        *,
        request: LammpsRequest,
        issues: list[str],
        stage: str,
    ) -> LammpsRequest | None:
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ComputeAgent", capability="LAMMPS 请求修复")
            return None
        try:
            payload = self.llm_client.chat_json(
                system_prompt=(
                    "You repair a structured LAMMPS request after validation/execution/review feedback. "
                    "Return JSON only with the same request keys. Be conservative and prefer smaller, safer demo requests."
                ),
                user_prompt=(
                    f"Current request:\n{request.model_dump_json()}\n\n"
                    f"Stage:\n{stage}\n\n"
                    f"Issues:\n{json.dumps(issues, ensure_ascii=False)}"
                ),
                max_tokens=900,
                temperature=0.1,
            )
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"ComputeAgent 在修复 LAMMPS 请求时调用 LLM 失败：{exc}") from exc
            return None
        if not payload:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("ComputeAgent 需要 LLM 修复 LAMMPS 请求，但本次没有得到有效 JSON。")
            return None
        merged = {**request.model_dump(mode="json"), **{key: value for key, value in payload.items() if value not in (None, "")}}
        return LammpsRequest.model_validate(merged)

    def _build_report(
        self,
        output_dir: Path,
        request_payload: dict[str, Any],
        user_query: str,
        metrics: dict[str, float],
        mode: str,
        error: str,
        config: LammpsConfig,
        diffusion_status: dict[str, object],
    ) -> Path:
        risk = error if error else "No blocking errors detected."
        diffusion_section = f"""## 扩散轨迹图
supported_task={diffusion_status.get('supported_task')}
backend={diffusion_status.get('backend')}
generated={diffusion_status.get('generated')}
reason={diffusion_status.get('reason')}
animation_path={diffusion_status.get('animation_path', '')}
video_path={diffusion_status.get('video_path', '')}
"""
        report = f"""# MD Agent Run Report

## 用户目标
{user_query}

## 归一化参数
{request_payload}

## 执行模式
{mode}

## LAMMPS 配置
command={config.lammps_command}
potentials_dir={config.potentials_dir}

## 关键热力学结果
{metrics}

{diffusion_section}

## 失败信息或风险提示
{risk}

## 后续建议
- 若当前为 mock 模式，请配置 `LAMMPS_CMD` 与 `POTENTIALS_DIR` 后重试。
- 若需要更多体系，请扩展 LAMMPS registry 与输入模板。
"""
        report_path = output_dir / "report.md"
        report_path.write_text(report, encoding="utf-8")
        return report_path

    def _review_result(
        self,
        *,
        request: LammpsRequest,
        mode: str,
        artifacts: list[ArtifactRef],
        metrics: dict[str, Any],
        validation: dict[str, Any],
        error: str,
        input_script: str,
    ) -> dict[str, Any]:
        blocking_issues: list[str] = []
        advisory_issues: list[str] = []
        artifact_names = [artifact.name for artifact in artifacts]
        for required in ("in.lammps", "thermo.csv", "plot.png", "report.md"):
            if required not in artifact_names:
                blocking_issues.append(f"Missing required artifact: {required}.")
        if validation.get("errors"):
            blocking_issues.extend(str(item) for item in validation["errors"])
        if error and mode == "real":
            blocking_issues.append(error)
        if mode == "mock":
            advisory_issues.append("This LAMMPS run used mock fallback instead of a real local executable.")

        review_mode = "heuristic_guardrail"
        llm_summary = ""
        llm_confidence: float | None = None
        if self.llm_client.is_configured():
            try:
                payload = self.llm_client.chat_json(
                    system_prompt=(
                        "You are reviewing a LAMMPS runtime agent run. "
                        "Return JSON only with keys: summary, confidence, passed, blocking_issues, advisory_issues. "
                        "Only mark blocking issues for invalid request parameters, missing core artifacts, execution failure, or obviously inconsistent outputs."
                    ),
                    user_prompt=(
                        f"Request:\n{request.model_dump_json()}\n\n"
                        f"Mode:\n{mode}\n\n"
                        f"Validation:\n{json.dumps(validation, ensure_ascii=False)}\n\n"
                        f"Metrics:\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
                        f"Artifacts:\n{json.dumps(artifact_names, ensure_ascii=False)}\n\n"
                        f"Execution error:\n{error}\n\n"
                        f"in.lammps preview:\n{input_script[:2500]}"
                    ),
                    max_tokens=900,
                    temperature=0.1,
                )
            except RuntimeError as exc:
                if settings.require_llm_for_agents:
                    raise LLMRequiredError(f"ComputeAgent 在审查 LAMMPS 结果时调用 LLM 失败：{exc}") from exc
                payload = None
            if payload:
                review_mode = "llm_plus_heuristic_guardrail"
                llm_summary = str(payload.get("summary") or "").strip()
                try:
                    llm_confidence = max(0.0, min(float(payload.get("confidence", 0.78)), 1.0))
                except (TypeError, ValueError):
                    llm_confidence = None
                for issue in payload.get("blocking_issues", []) if isinstance(payload.get("blocking_issues"), list) else []:
                    text = str(issue).strip()
                    if text and text not in blocking_issues:
                        blocking_issues.append(text)
                for issue in payload.get("advisory_issues", []) if isinstance(payload.get("advisory_issues"), list) else []:
                    text = str(issue).strip()
                    if text and text not in advisory_issues:
                        advisory_issues.append(text)
            elif settings.require_llm_for_agents:
                raise LLMRequiredError("ComputeAgent 需要 LLM 审查 LAMMPS 结果，但本次没有得到有效 JSON。")
        elif settings.require_llm_for_agents:
            self.llm_client.require_configured(agent_name="ComputeAgent", capability="LAMMPS 结果审查")

        passed = not blocking_issues
        confidence = max(0.2, 0.93 - 0.18 * len(blocking_issues) - 0.03 * len(advisory_issues))
        if llm_confidence is not None:
            confidence = min(confidence, llm_confidence) if blocking_issues else max(confidence, llm_confidence)
        summary = llm_summary or (
            "LAMMPS runtime review passed. The request, artifacts, and output summary are consistent."
            if passed
            else f"LAMMPS runtime review found {len(blocking_issues)} blocking issue(s)."
        )
        return {
            "passed": passed,
            "summary": summary,
            "confidence": round(confidence, 2),
            "issues": blocking_issues,
            "advisory_issues": advisory_issues,
            "review_mode": review_mode,
            "mode": mode,
        }

    def _write_run_record(
        self,
        *,
        run_id: str,
        conversation_id: str,
        route: TaskRoute,
        status: RunStatus,
        final_message: str,
        summary: dict[str, Any],
        artifacts: list[ArtifactRef],
        trace: list[ToolObservation],
        metadata: dict[str, Any],
    ) -> None:
        record = RunRecordSummary(
            run_id=run_id,
            conversation_id=conversation_id,
            status=status,
            route=route,
            final_message=final_message,
            summary=summary,
            artifacts=artifacts,
            trace=trace,
            metadata=metadata,
        )
        write_json_file(self.artifact_service.get_summary_path(run_id), record.model_dump(mode="json"))

    def _write_running_progress(
        self,
        *,
        run_id: str,
        conversation_id: str,
        route: TaskRoute,
        final_message: str,
        progress_stage: str,
        progress_percent: int,
        progress_message: str,
        summary: dict[str, Any],
        artifacts: list[ArtifactRef],
        trace: list[ToolObservation],
        metadata: dict[str, Any],
    ) -> None:
        self._write_run_record(
            run_id=run_id,
            conversation_id=conversation_id,
            route=route,
            status="running",
            final_message=final_message,
            summary={
                **summary,
                "progress": {
                    "stage": progress_stage,
                    "percent": progress_percent,
                    "message": progress_message,
                },
            },
            artifacts=artifacts,
            trace=trace,
            metadata=metadata,
        )

    @staticmethod
    def _build_result_profile(state: dict[str, Any], success: bool) -> dict[str, Any]:
        review = state.get("review", {}) or {}
        summary = state.get("summary", {}) or {}
        config = state.get("config", {}) or {}
        run_mode = str(state.get("run_mode", "") or "")
        ovito_status = (((summary.get("postprocess") or {}) if isinstance(summary, dict) else {}).get("ovito_status") or {})

        warnings = [str(item) for item in review.get("advisory_issues", []) if str(item).strip()]
        if run_mode == "mock":
            warnings.append("This result used mock fallback instead of a real local LAMMPS execution.")
        if not ovito_status.get("generated"):
            reason = str(ovito_status.get("reason") or "").strip()
            warnings.append(f"OVITO animation not fully available{f': {reason}' if reason else '.'}")
        if review.get("issues"):
            warnings.extend(str(item) for item in review.get("issues", []) if str(item).strip())

        assumptions = [
            "LAMMPS input is generated from a structured request and registry-backed template/tooling.",
            "Reported media and metrics come from the local execution workspace for this run.",
        ]
        evidence = []
        if config.get("lammps_command"):
            evidence.append(f"LAMMPS command: {config.get('lammps_command')}")
        if config.get("potentials_dir"):
            evidence.append(f"Potentials dir: {config.get('potentials_dir')}")
        if summary.get("metrics"):
            evidence.append(f"Metrics count: {len(summary.get('metrics', {}))}")

        confidence = review.get("confidence")
        try:
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_value = None
        if confidence_value is None:
            confidence_value = 0.9 if run_mode == "real" and success else 0.55 if success else 0.3
        if run_mode == "mock":
            confidence_value = min(confidence_value, 0.55)

        if success and run_mode == "real":
            trust_level = "high"
            trust_statement = "This result comes from a real local LAMMPS execution with post-processing and final review."
        elif success:
            trust_level = "medium"
            trust_statement = "This result is usable for workflow validation, but parts of it rely on fallback or partial tooling."
        else:
            trust_level = "low"
            trust_statement = "This run did not complete a trustworthy simulation result and should be treated as diagnostic output."

        return {
            "category": "LAMMPS Simulated" if run_mode == "real" else "LAMMPS Fallback",
            "source_label": "LAMMPS local execution" if run_mode == "real" else "mock / partial runtime",
            "mode_label": run_mode or "unknown",
            "trust_level": trust_level,
            "confidence": round(confidence_value, 2),
            "trust_statement": trust_statement,
            "assumptions": assumptions,
            "warnings": warnings,
            "evidence": evidence,
        }

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
        _ = recognition_result
        route = TaskRoute(
            name="lammps.generate",
            workspace_id="materials_agent",
            reason=str((decision or {}).get("reason") or "Supervisor selected the LAMMPS runtime."),
            selected_tool="lammps_request_interpreter",
            intent=str((decision or {}).get("intent") or "run_md_simulation"),
            decision_source=str((decision or {}).get("source") or "lammps_runtime"),
            decision_confidence=float((decision or {}).get("confidence")) if (decision or {}).get("confidence") is not None else None,
            compute_domain="lammps",
        )
        state: dict[str, Any] = {
            "run_id": run_id,
            "route": route,
            "plan_steps": list(existing_plan_steps or []),
            "trace": list(existing_trace or []),
            "artifacts": list(existing_artifacts or []),
            "request_payload": None,
            "parse_result": {},
            "validation": {},
            "config": {},
            "metrics": {},
            "generated_input": "",
            "run_mode": "draft",
            "error": "",
            "review": {},
            "summary": {},
            "event_sink": event_sink,
        }
        clear_cancellation(run_id)
        config = self.config_loader()
        state["config"] = lammps_config_public_payload()
        request_attempt: LammpsRequest | None = None
        parse_info: dict[str, Any] = {}
        max_retries = max(0, config.max_retries)
        output_dir = self.artifact_service.get_run_dir(run_id)
        uploaded_attachments = persist_uploaded_assets(request.uploaded_assets, output_dir)
        attachment_overrides = infer_request_overrides(uploaded_attachments)
        attachment_artifacts = [
            self._build_artifact(
                run_id=run_id,
                kind="image" if str(item["media_type"]).startswith("image/") else "text",
                name=str(item["stored_name"]),
                path=str(item["path"]),
                metadata={"category": item["category"], "original_name": item["original_name"]},
            )
            for item in uploaded_attachments
        ]
        if attachment_artifacts:
            state["artifacts"] = self._merge_artifacts(state["artifacts"], attachment_artifacts)

        for attempt in range(max_retries + 1):
            if request_attempt is None:
                request_attempt, parse_info = self._parse_request(
                    request,
                    attachment_overrides=attachment_overrides,
                    attachment_context=uploaded_attachments,
                )
            state["request_payload"] = request_attempt
            state["parse_result"] = parse_info
            self._record_step(
                state,
                tool_name="lammps_request_interpreter",
                success=True,
                summary=f"已将请求解析为 {request_attempt.material or 'unknown'} / {request_attempt.task_type} / {request_attempt.temperature} K / {request_attempt.steps} steps。",
                input_data={"message": request.message},
                output_data={"request": request_attempt.model_dump(mode="json"), "parse_info": parse_info},
                description="Interpret the natural-language MD request into a structured LAMMPS request.",
                stage=f"lammps_request_interpreter_{attempt + 1}",
                retryable=attempt < max_retries,
                artifacts=attachment_artifacts if attempt == 0 else None,
                state_delta={"compute_domain": "lammps"},
            )

            registry = get_lammps_registry_payload()
            registry_success = (
                request_attempt.material in get_supported_materials()
                and request_attempt.potential_family in get_supported_potentials()
                and request_attempt.task_type in get_supported_tasks()
            )
            self._record_step(
                state,
                tool_name="lammps_registry_lookup",
                success=registry_success,
                summary="已命中 LAMMPS registry。" if registry_success else "LAMMPS registry 中没有匹配到当前材料/势函数/任务组合。",
                input_data={"material": request_attempt.material, "potential_family": request_attempt.potential_family, "task_type": request_attempt.task_type},
                output_data={"registry": registry},
                description="Check the LAMMPS demo registry before generating the input script.",
                stage=f"lammps_registry_lookup_{attempt + 1}",
                retryable=attempt < max_retries,
            )
            if not registry_success:
                issues = ["LAMMPS registry miss for material/potential/task."]
                if attempt < max_retries:
                    repaired = self._repair_request(request=request_attempt, issues=issues, stage="registry_lookup")
                    if repaired is not None:
                        request_attempt = repaired
                        parse_info = {"source": "llm_request_repair", "confidence": 0.7}
                        continue
                return self._finalize(
                    state,
                    success=False,
                    final_message="当前 LAMMPS registry 还不支持这个材料/势函数/任务组合，所以这轮没有进入真实执行。",
                    termination_reason="lammps_registry_not_found",
                    conversation_id=request.conversation_id,
                    run_status="failed",
                )

            request_payload = request_attempt.model_dump(mode="json")
            validation = validate_request(request_payload)
            state["validation"] = validation
            self._record_step(
                state,
                tool_name="lammps_validation",
                success=bool(validation.get("is_reasonable")),
                summary="参数校验通过。" if validation.get("is_reasonable") else "参数校验未通过，准备修复或终止。",
                input_data=request_payload,
                output_data=validation,
                description="Validate the structured LAMMPS request before script generation.",
                stage=f"lammps_validation_{attempt + 1}",
                retryable=attempt < max_retries,
            )
            if not validation.get("is_reasonable"):
                issues = [*validation.get("errors", []), *([f"missing: {', '.join(validation.get('missing_fields', []))}"] if validation.get("missing_fields") else [])]
                if attempt < max_retries:
                    repaired = self._repair_request(request=request_attempt, issues=issues, stage="validation")
                    if repaired is not None:
                        request_attempt = repaired
                        parse_info = {"source": "llm_request_repair", "confidence": 0.74}
                        self._record_step(
                            state,
                            tool_name="lammps_repair",
                            success=True,
                            summary="LLM 已根据校验反馈修复请求，准备重试。",
                            input_data={"issues": issues},
                            output_data={"request": repaired.model_dump(mode="json")},
                            description="Repair the structured LAMMPS request using validation feedback.",
                            stage=f"lammps_repair_after_validation_{attempt + 1}",
                            retryable=True,
                        )
                        continue
                return self._finalize(
                    state,
                    success=False,
                    final_message="这轮 LAMMPS 任务没有通过参数校验，我已保留 trace 和错误信息，方便继续调整请求。",
                    termination_reason="lammps_validation_failed",
                    conversation_id=request.conversation_id,
                    run_status="failed",
                )

            input_path = generate_lammps_input(request_payload, output_dir, potentials_dir=config.potentials_dir)
            generated_input = input_path.read_text(encoding="utf-8")
            state["generated_input"] = generated_input
            input_artifact = self._build_artifact(
                run_id=run_id,
                kind="code",
                name="in.lammps",
                path=input_path,
                content=generated_input,
                metadata={"attempt": attempt + 1},
            )
            request_artifact_path = output_dir / "request.json"
            write_json_file(
                request_artifact_path,
                {
                    "original_query": request.message,
                    "normalized_request": request_payload,
                    "uploaded_attachments": uploaded_attachments,
                },
            )
            request_artifact = self._build_artifact(run_id=run_id, kind="json", name="request.json", path=request_artifact_path)
            self._record_step(
                state,
                tool_name="lammps_input_codegen",
                success=True,
                summary="已生成 LAMMPS 输入脚本。",
                input_data=request_payload,
                output_data={"input_path": str(input_path)},
                description="Generate the LAMMPS input script from the validated structured request.",
                stage=f"lammps_input_codegen_{attempt + 1}",
                artifacts=[input_artifact, request_artifact],
            )

            self._write_running_progress(
                run_id=run_id,
                conversation_id=request.conversation_id,
                route=route,
                final_message="LAMMPS 任务执行中。",
                progress_stage="running_lammps",
                progress_percent=28,
                progress_message="正在执行 LAMMPS 模拟。",
                summary={"mode": "real", "request": request_payload},
                artifacts=state["artifacts"],
                trace=state["trace"],
                metadata={"parse_result": parse_info, "validation": validation, "config": state["config"]},
            )

            mode = "real"
            error = ""
            try:
                if config.force_mock:
                    raise RuntimeError("Mock mode forced by USE_MOCK=true.")
                mode, error, metrics = run_lammps(input_path, output_dir, request_payload, config, run_id)
            except RunCancelledError:
                return self._finalize(
                    state,
                    success=False,
                    final_message="这轮 LAMMPS 任务已被取消。",
                    termination_reason="cancelled",
                    conversation_id=request.conversation_id,
                    run_status="cancelled",
                )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                state["error"] = error
                if not config.allow_mock_fallback and not config.force_mock:
                    self._record_step(
                        state,
                        tool_name="lammps_execute",
                        success=False,
                        summary=f"LAMMPS 执行失败：{error}",
                        input_data={"input_path": str(input_path)},
                        output_data={"error": error},
                        description="Execute the generated LAMMPS input locally.",
                        stage=f"lammps_execute_{attempt + 1}",
                        retryable=attempt < max_retries,
                    )
                    if attempt < max_retries:
                        repaired = self._repair_request(request=request_attempt, issues=[error], stage="execution")
                        if repaired is not None:
                            request_attempt = repaired
                            parse_info = {"source": "llm_request_repair", "confidence": 0.71}
                            self._record_step(
                                state,
                                tool_name="lammps_repair",
                                success=True,
                                summary="LLM 已根据执行错误修复请求，准备重试。",
                                input_data={"issues": [error]},
                                output_data={"request": repaired.model_dump(mode="json")},
                                description="Repair the structured LAMMPS request using execution feedback.",
                                stage=f"lammps_repair_after_execution_{attempt + 1}",
                                retryable=True,
                            )
                            continue
                    return self._finalize(
                        state,
                        success=False,
                        final_message="本地 LAMMPS 执行失败，我已保留输入脚本和错误信息，方便继续修复。",
                        termination_reason="lammps_execution_failed",
                        conversation_id=request.conversation_id,
                        run_status="failed",
                    )
                mode = "mock"
                metrics = run_mock(output_dir, request_payload, error)

            state["metrics"] = metrics
            state["run_mode"] = mode
            self._write_running_progress(
                run_id=run_id,
                conversation_id=request.conversation_id,
                route=route,
                final_message="LAMMPS 已执行完成，正在整理后处理产物。",
                progress_stage="postprocess",
                progress_percent=72,
                progress_message="LAMMPS 执行完成，正在生成热力学图、轨迹和报告。",
                summary={"mode": mode, "request": request_payload, "metrics": metrics},
                artifacts=state["artifacts"],
                trace=state["trace"],
                metadata={"parse_result": parse_info, "validation": validation, "config": state["config"]},
            )
            self._record_step(
                state,
                tool_name="lammps_execute",
                success=True,
                summary="LAMMPS 已完成执行。" if mode == "real" else "真实执行失败，已回退到 mock 产物以保留完整结果面板。",
                input_data={"input_path": str(input_path)},
                output_data={"mode": mode, "metrics": metrics, "error": error},
                description="Execute the generated LAMMPS input locally.",
                stage=f"lammps_execute_{attempt + 1}",
                retryable=attempt < max_retries,
                state_delta={"run_mode": mode},
            )

            dump_file_name = str(request_payload.get("dump_file") or "dump.atom")
            structure_path = convert_dump(output_dir, dump_file_name)
            plot_path = generate_plot(output_dir)
            diffusion_status = generate_diffusion_trajectory_if_applicable(
                output_dir,
                request_payload,
                mode,
                run_id=run_id,
                progress_callback=None,
            )
            report_path = self._build_report(output_dir, request_payload, request.message, metrics, mode, error, config, diffusion_status)

            postprocess_artifacts = [
                self._build_artifact(run_id=run_id, kind="csv", name="thermo.csv", path=output_dir / "thermo.csv"),
                self._build_artifact(run_id=run_id, kind="image", name="plot.png", path=plot_path),
                self._build_artifact(run_id=run_id, kind="markdown", name="report.md", path=report_path),
                self._build_artifact(run_id=run_id, kind="json", name="structure_summary.json", path=structure_path),
                self._build_artifact(run_id=run_id, kind="text", name="run.log", path=output_dir / "run.log"),
            ]
            trajectory_path = output_dir / dump_file_name
            if trajectory_path.exists():
                postprocess_artifacts.append(
                    self._build_artifact(
                        run_id=run_id,
                        kind="text",
                        name=dump_file_name,
                        path=trajectory_path,
                        metadata={"artifact_role": "trajectory"},
                    )
                )
            if diffusion_status.get("generated"):
                postprocess_artifacts.extend(
                    [
                        self._build_artifact(run_id=run_id, kind="image", name="diffusion_trajectory.png", path=Path(str(diffusion_status["image_path"]))),
                        self._build_artifact(run_id=run_id, kind="image", name="diffusion_trajectory_3d.gif", path=Path(str(diffusion_status["animation_path"]))),
                        self._build_artifact(run_id=run_id, kind="video", name="ovito.mp4", path=Path(str(diffusion_status["video_path"]))),
                        self._build_artifact(run_id=run_id, kind="json", name="diffusion_metadata.json", path=Path(str(diffusion_status["metadata_path"]))),
                    ]
                )
            self._record_step(
                state,
                tool_name="lammps_postprocess",
                success=True,
                summary="已生成热力学图、报告和可用的轨迹产物。",
                input_data={"mode": mode},
                output_data={"diffusion_status": diffusion_status, "metrics": metrics},
                description="Post-process the LAMMPS outputs into plots, markdown, and trajectory artifacts.",
                stage=f"lammps_postprocess_{attempt + 1}",
                artifacts=postprocess_artifacts,
            )

            review = self._review_result(
                request=request_attempt,
                mode=mode,
                artifacts=state["artifacts"],
                metrics=metrics,
                validation=validation,
                error=error,
                input_script=generated_input,
            )
            state["review"] = review
            summary_payload = {
                "mode": mode,
                "request": request_payload,
                "metrics": metrics,
                "validation": validation,
                "config": state["config"],
                "postprocess": {"ovito_status": diffusion_status},
            }
            state["summary"] = summary_payload
            self._write_running_progress(
                run_id=run_id,
                conversation_id=request.conversation_id,
                route=route,
                final_message="LAMMPS 后处理已完成，正在进行结果审查。",
                progress_stage="review",
                progress_percent=90,
                progress_message="后处理完成，正在执行最终审查。",
                summary=summary_payload,
                artifacts=state["artifacts"],
                trace=state["trace"],
                metadata={"parse_result": parse_info, "validation": validation, "config": state["config"], "run_mode": mode},
            )
            self._record_step(
                state,
                tool_name="lammps_result_review",
                success=bool(review.get("passed")),
                summary=str(review.get("summary") or ""),
                input_data={"mode": mode},
                output_data={"review": review, "summary": summary_payload},
                description="Review the LAMMPS input, outputs, and artifact contract before returning to the user.",
                stage=f"lammps_result_review_{attempt + 1}",
                retryable=attempt < max_retries,
            )
            if review.get("passed"):
                final_message = (
                    "LAMMPS 任务已完成。本轮 agent 已完成“LLM 解析 -> registry/validation -> 输入脚本生成 -> 本地执行 -> 后处理 -> LLM 审查”。"
                    if mode == "real"
                    else "LAMMPS 任务已完成，但这轮为了保留完整结果面板回退到了 mock 模式。"
                )
                return self._finalize(
                    state,
                    success=True,
                    final_message=final_message,
                    termination_reason="review_passed",
                    conversation_id=request.conversation_id,
                    run_status="completed",
                )

            issues = [str(item) for item in review.get("issues", [])]
            if mode == "mock" and self._is_infrastructure_issue(error):
                return self._finalize(
                    state,
                    success=False,
                    final_message="LAMMPS 请求已经被正确解析并生成了输入脚本，但当前机器缺少真实本地执行所需的 LAMMPS 环境，因此这轮只保留了 mock 产物供你查看，不会再偷偷改写原始请求参数。",
                    termination_reason="lammps_runtime_dependency_missing",
                    conversation_id=request.conversation_id,
                    run_status="failed",
                )
            if attempt < max_retries:
                repaired = self._repair_request(request=request_attempt, issues=issues, stage="review")
                if repaired is not None:
                    request_attempt = repaired
                    parse_info = {"source": "llm_request_repair", "confidence": 0.72}
                    self._record_step(
                        state,
                        tool_name="lammps_repair",
                        success=True,
                        summary="LLM 已根据结果审查反馈修复请求，准备重试。",
                        input_data={"issues": issues},
                        output_data={"request": repaired.model_dump(mode="json")},
                        description="Repair the structured LAMMPS request using result-review feedback.",
                        stage=f"lammps_repair_after_review_{attempt + 1}",
                        retryable=True,
                    )
                    continue

            return self._finalize(
                state,
                success=False,
                final_message="这轮 LAMMPS 结果没有通过完整自检，我已保留输入脚本、trace 和产物，方便继续修复。",
                termination_reason="review_failed",
                conversation_id=request.conversation_id,
                run_status="failed",
            )

        return self._finalize(
            state,
            success=False,
            final_message="这轮 LAMMPS 任务没有通过完整流程，我已保留 trace 和产物供继续排查。",
            termination_reason="review_failed",
            conversation_id=request.conversation_id,
            run_status="failed",
        )

    def _finalize(
        self,
        state: dict[str, Any],
        *,
        success: bool,
        final_message: str,
        termination_reason: str,
        conversation_id: str,
        run_status: RunStatus,
    ) -> AgentRunResponse:
        trace_model = RunTrace(
            run_id=state["run_id"],
            route=state["route"],
            steps=state["plan_steps"],
            observations=state["trace"],
            termination_reason=termination_reason,
            metadata={
                "parse_result": state.get("parse_result", {}),
                "validation": state.get("validation", {}),
                "config": state.get("config", {}),
                "run_mode": state.get("run_mode", ""),
                "review": state.get("review", {}),
                "summary": state.get("summary", {}),
            },
        )
        trace_path = self.artifact_service.write_trace(trace_model)
        trace_artifact = self._build_artifact(run_id=state["run_id"], kind="json", name="trace.json", path=trace_path)
        artifacts = self._merge_artifacts(state["artifacts"], [trace_artifact])
        response = AgentRunResponse(
            success=success,
            run_id=state["run_id"],
            conversation_id=conversation_id,
            route=state["route"],
            final_message=final_message,
            artifacts=artifacts,
            plan_steps=state["plan_steps"],
            trace=state["trace"],
            generated_code=state.get("generated_input") or None,
            stdout="",
            stderr=state.get("error", ""),
            html_content=None,
            html_path=None,
            termination_reason=termination_reason,
            metadata={
                "parse_result": state.get("parse_result", {}),
                "validation": state.get("validation", {}),
                "config": state.get("config", {}),
                "run_mode": state.get("run_mode", ""),
                "review": state.get("review", {}),
            },
            recognition_result=None,
            current_context_summary="",
            summary=state.get("summary", {}),
            run_status=run_status,
        )
        response.summary["result_profile"] = self._build_result_profile(state, success)
        response.metadata["result_profile"] = response.summary["result_profile"]
        self.artifact_service.write_run_summary(response)
        self._write_run_record(
            run_id=state["run_id"],
            conversation_id=conversation_id,
            route=state["route"],
            status=run_status,
            final_message=final_message,
            summary=state.get("summary", {}),
            artifacts=artifacts,
            trace=state["trace"],
            metadata=response.metadata,
        )
        clear_cancellation(state["run_id"])
        return response
