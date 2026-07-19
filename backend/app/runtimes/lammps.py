from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.artifacts import ArtifactService
from app.core.cancellation import RunCancelledError, clear_cancellation
from app.core.llm import LLMClient, LLMRequiredError
from app.core.llm_capabilities import LLMCapability
from app.lammps.attachments import infer_request_overrides, persist_uploaded_assets
from app.lammps.config import LammpsConfig, lammps_config_public_payload, load_lammps_config
from app.lammps.multifidelity import evaluate_pilot, plan_multifidelity_run
from app.lammps.preflight import LAMMPS_PREFLIGHT_NODE_IDS, build_lammps_preflight_plan
from app.lammps.postprocess import convert_dump, generate_diffusion_trajectory_if_applicable, generate_plot
from app.lammps.quality import ThermoParseError, build_physical_quality_report, write_physical_quality_report
from app.lammps.review import (
    LLMReviewAdvisory,
    build_deterministic_review_report,
    build_patch_from_llm_payload,
    evaluate_repair_convergence,
    parse_review_payload,
    verify_and_apply_patch,
)
from app.lammps.review.evidence import format_materials_rag_l2_context, format_shared_memory_l2_context
from app.lammps.registry import get_lammps_registry_payload, get_supported_materials, get_supported_potentials, get_supported_tasks
from app.lammps.runner import run_lammps, run_mock
from app.lammps.template import get_lammps_form_schema, generate_lammps_input
from app.lammps.validator import validate_request
from app.materials_rag.service import MaterialsRagService
from app.materials_rag.normalizer import extract_materials, normalize_material
from app.rag.uncertainty import estimate_retrieval_uncertainty
from app.orchestration import (
    DAGExecutionContext,
    DAGExecutionResult,
    DAGExecutor,
    DAGNode,
    DAGNodeResult,
    DAGPlan,
    ReplanBudgetState,
    ProcessRewardModel,
    TaskLifecycleController,
    apply_level_1_fallbacks,
    build_plan_variants,
    decide_degradation,
    search_plans,
)
from app.runtimes.telemetry import build_runtime_execution_profile, initialize_runtime_state
from app.state import (
    AgentChatRequest,
    AgentRunResponse,
    AgentStreamEvent,
    ArtifactRef,
    LammpsRequest,
    PlanStep,
    RecognitionResult,
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
        materials_rag_service: MaterialsRagService | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.llm_client = llm_client or LLMClient()
        self.config_loader = config_loader
        self.materials_rag_service = materials_rag_service or MaterialsRagService()

    @staticmethod
    def _emit(state: dict[str, Any], event: AgentStreamEvent) -> None:
        sink = state.get("event_sink")
        if sink:
            sink(event)

    @staticmethod
    def _lifecycle(state: dict[str, Any]) -> TaskLifecycleController | None:
        controller = state.get("_lifecycle_controller")
        return controller if isinstance(controller, TaskLifecycleController) else None

    @classmethod
    def _transition_lifecycle(
        cls,
        state: dict[str, Any],
        *,
        to_state: str,
        reason: str,
        plan_version: int | None = None,
        metadata: dict[str, Any] | None = None,
        termination_reason: str = "",
    ) -> None:
        controller = cls._lifecycle(state)
        if controller is None:
            return
        if controller.state.current_state == to_state:
            return
        controller.transition(
            to_state=to_state,  # type: ignore[arg-type]
            reason=reason,
            plan_version=plan_version,
            metadata=metadata or {},
            termination_reason=termination_reason,
        )
        state["lifecycle"] = controller.snapshot()

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

    @staticmethod
    def _explicit_mock_requested(message: str) -> bool:
        """Return true only for an affirmative mock/demo request.

        Generic Chinese "模拟" is intentionally not a trigger because it is
        the normal word for a molecular-dynamics simulation.
        """

        normalized = (message or "").strip().lower()
        if not normalized:
            return False
        negative = re.search(
            r"(?:禁止|不要|不允许|拒绝|不能|不得|非|no|without|disable)\s*(?:使用|use)?\s*(?:mock|synthetic|假数据|合成数据)",
            normalized,
        )
        if negative:
            return False
        return bool(
            re.search(r"\bmock\b|\bsynthetic\b|仅演示|演示模式|演示数据|假数据|合成数据|demo\s+mode", normalized)
        )

    @staticmethod
    def _repair_stop_reason(state: dict[str, Any], default: str) -> str:
        history = state.get("repair_history")
        if not isinstance(history, list) or not history:
            return default
        last_entry = history[-1]
        if not isinstance(last_entry, dict):
            return default
        convergence = last_entry.get("convergence_report")
        if not isinstance(convergence, dict):
            return default
        if convergence.get("allow_repair") is False and convergence.get("termination_reason"):
            return str(convergence["termination_reason"])
        return default

    @staticmethod
    def _review_overall_score(review: dict[str, Any]) -> float | None:
        score = review.get("score")
        if not isinstance(score, dict):
            return None
        value = score.get("overall_score")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _collect_llm_parse_audits(state: dict[str, Any]) -> list[dict[str, Any]]:
        audits: list[dict[str, Any]] = []
        review = state.get("review")
        if isinstance(review, dict):
            review_audit = review.get("llm_review_parse_audit")
            if isinstance(review_audit, dict) and review_audit:
                audits.append({"audit_type": "red_review_advisory", **review_audit})
        repair_history = state.get("repair_history")
        if isinstance(repair_history, list):
            for index, entry in enumerate(repair_history):
                if not isinstance(entry, dict):
                    continue
                blue_audit = entry.get("blue_parse_audit")
                if isinstance(blue_audit, dict) and blue_audit:
                    audits.append(
                        {
                            "audit_type": "blue_patch",
                            "repair_index": index,
                            "stage": entry.get("stage", ""),
                            **blue_audit,
                        }
                    )
        return audits

    @staticmethod
    def _serialize_materials_rag_hits(hits: list[object]) -> list[dict[str, object]]:
        payload: list[dict[str, object]] = []
        for hit in hits:
            payload.append(
                {
                    "title": hit.document.title,
                    "doc_type": hit.document.doc_type,
                    "score": hit.score,
                    "lexical_score": getattr(hit, "lexical_score", 0.0),
                    "bm25_score": getattr(hit, "bm25_score", 0.0),
                    "vector_score": getattr(hit, "vector_score", 0.0),
                    "graph_score": getattr(hit, "graph_score", 0.0),
                    "graph_paths": list(getattr(hit, "graph_paths", [])),
                    "graph_community": getattr(hit, "graph_community", ""),
                    "embedding_backend": getattr(hit, "embedding_backend", ""),
                    "matched_fields": list(hit.matched_fields),
                    "source": hit.document.source,
                    "source_url": hit.document.source_url,
                }
            )
        return payload

    @staticmethod
    def _materials_rag_material_hint(message: str, fallback_material: str | None = None) -> str | None:
        hinted = next(iter(extract_materials(message)), None)
        if hinted:
            return hinted
        return normalize_material(fallback_material)

    def _planning_materials_rag(self, request: AgentChatRequest) -> dict[str, object]:
        material_hint = self._materials_rag_material_hint(request.message)
        hits = self.materials_rag_service.search(
            request.message,
            domain="lammps",
            material=material_hint,
            top_k=4,
        )
        context = self.materials_rag_service.build_context(
            request.message,
            domain="lammps",
            material=material_hint,
            top_k=4,
            max_items=3,
        )
        return {
            "query": request.message,
            "material": material_hint,
            "hits": hits,
            "context": context,
            "retrieval_uncertainty": estimate_retrieval_uncertainty(hits).public_payload(),
        }

    def _planning_materials_rag_serialized_hits(self, planning_rag: dict[str, object]) -> list[dict[str, object]]:
        if isinstance(planning_rag.get("serialized_hits"), list):
            return list(planning_rag["serialized_hits"])  # type: ignore[index]
        hits = list(planning_rag.get("hits", []))
        if hits and isinstance(hits[0], dict):
            return [dict(hit) for hit in hits if isinstance(hit, dict)]
        return self._serialize_materials_rag_hits(hits)

    def _planning_materials_rag_hit_count(self, planning_rag: dict[str, object]) -> int:
        return len(self._planning_materials_rag_serialized_hits(planning_rag))

    def _planning_materials_rag_evidence_refs(self, planning_rag: dict[str, object]) -> list[str]:
        refs: list[str] = []
        for hit in self._planning_materials_rag_serialized_hits(planning_rag):
            source_url = str(hit.get("source_url") or "").strip()
            source = str(hit.get("source") or "").strip()
            title = str(hit.get("title") or "").strip()
            ref = source_url or source or title
            if ref and ref not in refs:
                refs.append(ref)
        return refs

    @staticmethod
    def _lammps_config_payload(config: LammpsConfig) -> dict[str, Any]:
        payload = dict(lammps_config_public_payload())
        payload.update(
            {
                "lammps_command": config.lammps_command,
                "potentials_dir": config.potentials_dir,
                "ovito_location": config.ovito_location or payload.get("ovito_location", ""),
                "allow_mock_fallback": config.allow_mock_fallback,
                "force_mock": config.force_mock,
                "max_retries": config.max_retries,
                "lammps_preflight_dag_enabled": config.lammps_preflight_dag_enabled,
                "lammps_red_blue_review_enabled": config.lammps_red_blue_review_enabled,
                "lammps_command_exists": bool(config.lammps_command and Path(config.lammps_command).exists()),
                "potentials_dir_exists": bool(config.potentials_dir and Path(config.potentials_dir).exists()),
            }
        )
        return payload

    @staticmethod
    def _run_async_sync(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result_box: dict[str, Any] = {}

        def _runner() -> None:
            try:
                result_box["result"] = asyncio.run(coro)
            except BaseException as exc:  # noqa: BLE001 - preserve the async boundary exception.
                result_box["error"] = exc

        thread = threading.Thread(target=_runner, name="lammps-preflight-dag-runner", daemon=True)
        thread.start()
        thread.join()
        if "error" in result_box:
            raise result_box["error"]
        return result_box.get("result")

    def _error_materials_rag(self, *, error_text: str, material: str | None, request_message: str) -> dict[str, object]:
        query = f"{request_message}\n{error_text}".strip()
        material_hint = self._materials_rag_material_hint(query, material)
        hits = self.materials_rag_service.search(
            query,
            domain="lammps",
            doc_type="error_cookbook",
            material=material_hint,
            top_k=3,
        )
        context = self.materials_rag_service.build_context(
            query,
            domain="lammps",
            doc_type="error_cookbook",
            material=material_hint,
            top_k=3,
            max_items=2,
        )
        return {
            "query": query,
            "material": material_hint,
            "hits": hits,
            "context": context,
        }

    @staticmethod
    def _error_diagnostic_lines(error_payload: dict[str, object]) -> list[str]:
        hits = list(error_payload.get("hits", []))
        if not hits:
            return []
        lines = ["基于材料知识检索的可能原因与建议："]
        for hit in hits[:2]:
            lines.append(f"- {hit.document.title}：{hit.document.content}")
            if hit.document.source_url:
                lines.append(f"  来源：{hit.document.source_url}")
        return lines

    @classmethod
    def _heuristic_request(
        cls,
        message: str,
        notes: str,
        attachment_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        task_type = "heating" if any(token in raw for token in ("heat", "heating", "升温", "升到", "升至", "加热")) else "equilibration"
        potential_family = "lj" if "lj" in raw or "lennard-jones" in raw else "eam"
        temperature_values = [
            int(round(float(value)))
            for value in re.findall(r"(\d{2,5}(?:\.\d+)?)\s*(?:k|kelvin)\b", raw)
        ]
        range_match = re.search(
            r"(?:from|从)\s*(\d{2,5}(?:\.\d+)?)\s*(?:k|kelvin)\s*"
            r"(?:to|[-~–—]|(?:升温?|加热)?(?:到|至))\s*"
            r"(\d{2,5}(?:\.\d+)?)\s*(?:k|kelvin)\b",
            raw,
        )
        initial_temperature: int | None = None
        if range_match:
            initial_temperature = int(round(float(range_match.group(1))))
            target_temperature = int(round(float(range_match.group(2))))
        elif task_type == "heating" and len(temperature_values) >= 2:
            initial_temperature = temperature_values[0]
            target_temperature = temperature_values[-1]
        else:
            target_temperature = temperature_values[0] if temperature_values else 900
        steps_match = (
            re.search(r"(\d{3,7})\s*steps?", raw)
            or re.search(r"步数\s*(\d{3,7})", raw)
            or re.search(r"(\d{3,7})\s*步", raw)
        )
        request_payload = {
            "material": material,
            "potential_family": potential_family,
            "task_type": task_type,
            "temperature": target_temperature,
            "steps": int(steps_match.group(1)) if steps_match else 5000,
            "ensemble": "NVT",
            "box_size": 4,
            "time_step": 0.001,
            "dump_file": "dump.atom",
            "notes": notes or message.strip(),
        }
        if initial_temperature is not None:
            request_payload["initial_temp"] = initial_temperature
        if attachment_overrides:
            request_payload.update({key: value for key, value in attachment_overrides.items() if value})
            if attachment_overrides.get("custom_potential_path"):
                request_payload["potential_family"] = "eam"
        return request_payload

    @staticmethod
    def _preflight_request(context: DAGExecutionContext) -> AgentChatRequest:
        request = context.metadata.get("request")
        if not isinstance(request, AgentChatRequest):
            raise RuntimeError("LAMMPS preflight context is missing AgentChatRequest.")
        return request

    def _preflight_constraint_extract_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        request = self._preflight_request(context)
        constraints = self._heuristic_request(request.message, request.notes)
        extracted_materials = list(extract_materials(request.message))
        locked_constraints = {
            key: value
            for key, value in constraints.items()
            if value not in (None, "", [], {})
            and key in {"material", "potential_family", "task_type", "temperature", "steps", "ensemble", "box_size"}
        }
        return {
            "request_constraints": constraints,
            "locked_constraints": locked_constraints,
            "extracted_materials": extracted_materials,
            "evidence_refs": ["user_message", "heuristic_lammps_request_interpreter"],
        }

    def _preflight_materials_rag_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        request = self._preflight_request(context)
        planning_rag = self._planning_materials_rag(request)
        serialized_hits = self._planning_materials_rag_serialized_hits(planning_rag)
        planning_payload = {
            "query": planning_rag["query"],
            "material": planning_rag["material"],
            "hits": serialized_hits,
            "serialized_hits": serialized_hits,
            "context": planning_rag.get("context", ""),
            "retrieval_uncertainty": planning_rag.get("retrieval_uncertainty", {}),
        }
        return {
            "planning_rag": planning_payload,
            "rag_context": planning_rag.get("context", ""),
            "evidence_refs": self._planning_materials_rag_evidence_refs(planning_payload),
        }

    def _preflight_registry_lookup_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        request = self._preflight_request(context)
        candidate = self._heuristic_request(request.message, request.notes)
        supported_materials = sorted(get_supported_materials())
        supported_potentials = sorted(get_supported_potentials())
        supported_tasks = sorted(get_supported_tasks())
        candidate_supported = (
            candidate.get("material") in set(supported_materials)
            and candidate.get("potential_family") in set(supported_potentials)
            and candidate.get("task_type") in set(supported_tasks)
        )
        return {
            "registry": get_lammps_registry_payload(),
            "registry_match": {
                "candidate": candidate,
                "candidate_supported": candidate_supported,
                "supported_materials": supported_materials,
                "supported_potentials": supported_potentials,
                "supported_tasks": supported_tasks,
                "note": "Pre-parse registry lookup is advisory; exact registry gating still runs after structured parsing.",
            },
            "registry_evidence_refs": ["lammps_registry"],
        }

    def _preflight_attachment_inspection_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        request = self._preflight_request(context)
        output_dir = context.metadata.get("output_dir")
        if not isinstance(output_dir, Path):
            raise RuntimeError("LAMMPS preflight context is missing output_dir.")
        uploaded_attachments = persist_uploaded_assets(request.uploaded_assets, output_dir)
        attachment_overrides = infer_request_overrides(uploaded_attachments)
        categories: dict[str, int] = {}
        for item in uploaded_attachments:
            category = str(item.get("category") or "attachment")
            categories[category] = categories.get(category, 0) + 1
        return {
            "uploaded_attachments": uploaded_attachments,
            "attachment_overrides": attachment_overrides,
            "attachment_summary": {
                "count": len(uploaded_attachments),
                "categories": categories,
                "has_custom_potential": bool(attachment_overrides.get("custom_potential_path")),
                "has_custom_structure": bool(attachment_overrides.get("custom_structure_path")),
            },
            "evidence_refs": [str(item.get("path")) for item in uploaded_attachments if item.get("path")],
        }

    def _preflight_runtime_diagnostics_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        config = context.metadata.get("config")
        if not isinstance(config, LammpsConfig):
            raise RuntimeError("LAMMPS preflight context is missing LammpsConfig.")
        payload = self._lammps_config_payload(config)
        diagnostics = {
            "config": payload,
            "real_execution_ready": bool(payload.get("lammps_command_exists")),
            "mock_fallback_available": bool(payload.get("allow_mock_fallback") or payload.get("force_mock")),
            "ovito_available": bool(payload.get("ovito_available")),
        }
        return {
            "runtime_diagnostics": diagnostics,
            "environment_evidence_refs": [
                str(payload.get("lammps_command") or ""),
                str(payload.get("potentials_dir") or ""),
                str(payload.get("ovito_location") or ""),
            ],
        }

    def _preflight_merge_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        node_outputs = context.metadata.get("node_outputs", {})
        node_results = context.metadata.get("node_results", {})
        if not isinstance(node_outputs, dict) or not isinstance(node_results, dict):
            raise RuntimeError("LAMMPS preflight merge cannot read node outputs.")

        constraint_output = dict(node_outputs.get("constraint_extract") or {})
        rag_output = dict(node_outputs.get("materials_rag_search") or {})
        registry_output = dict(node_outputs.get("registry_lookup") or {})
        attachment_output = dict(node_outputs.get("attachment_inspection") or {})
        diagnostics_output = dict(node_outputs.get("runtime_diagnostics") or {})

        failed_nodes = [
            node_id
            for node_id, result in node_results.items()
            if isinstance(result, dict) and result.get("status") not in {"completed"}
        ]
        evidence_refs: list[str] = []
        for output in (constraint_output, rag_output, registry_output, attachment_output, diagnostics_output):
            for key in ("evidence_refs", "registry_evidence_refs", "environment_evidence_refs"):
                for ref in output.get(key, []) if isinstance(output.get(key), list) else []:
                    text = str(ref).strip()
                    if text and text not in evidence_refs:
                        evidence_refs.append(text)

        registry_match = registry_output.get("registry_match") or {}
        runtime_diagnostics = diagnostics_output.get("runtime_diagnostics") or {}
        runtime_config = runtime_diagnostics.get("config", {}) if isinstance(runtime_diagnostics, dict) else {}
        advisories: list[str] = []
        if isinstance(registry_match, dict) and registry_match.get("candidate_supported") is False:
            advisories.append("Pre-parse registry candidate was not fully supported; exact parsed registry check remains authoritative.")
        if isinstance(runtime_config, dict) and not runtime_config.get("lammps_command_exists") and not (
            runtime_config.get("allow_mock_fallback") or runtime_config.get("force_mock")
        ):
            advisories.append("LAMMPS executable is not available and mock fallback is disabled.")
        if failed_nodes:
            advisories.append(f"Preflight observed non-completed node(s): {', '.join(sorted(failed_nodes))}.")

        report = {
            "status": "ready_with_advisories" if advisories else "ready",
            "request_constraints": constraint_output.get("request_constraints", {}),
            "locked_constraints": constraint_output.get("locked_constraints", {}),
            "rag": {
                "hit_count": len((rag_output.get("planning_rag") or {}).get("hits", []))
                if isinstance(rag_output.get("planning_rag"), dict)
                else 0,
                "material": (rag_output.get("planning_rag") or {}).get("material", "")
                if isinstance(rag_output.get("planning_rag"), dict)
                else "",
            },
            "registry_match": registry_match,
            "attachment_summary": attachment_output.get("attachment_summary", {}),
            "runtime_diagnostics": runtime_diagnostics,
            "advisories": advisories,
            "failed_nodes": failed_nodes,
        }
        return {
            "preflight_report": report,
            "merged_evidence_refs": evidence_refs,
        }

    def _preflight_red_review_handler(self, node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        _ = node
        node_outputs = context.metadata.get("node_outputs", {})
        merge_output = node_outputs.get("preflight_merge", {}) if isinstance(node_outputs, dict) else {}
        report = merge_output.get("preflight_report", {}) if isinstance(merge_output, dict) else {}
        advisories = list(report.get("advisories", [])) if isinstance(report, dict) and isinstance(report.get("advisories"), list) else []
        blocking_issues = [
            item
            for item in advisories
            if "mock fallback is disabled" in str(item).lower()
        ]
        passed = not blocking_issues
        return {
            "red_review_report": {
                "passed": passed,
                "mode": "deterministic_pre_execution_guardrail",
                "summary": "Pre-execution review passed." if passed else "Pre-execution review found blocking infrastructure issues.",
                "blocking_issues": blocking_issues,
                "advisory_issues": [item for item in advisories if item not in blocking_issues],
            },
            "repair_intent": "runtime_config_or_user_clarification" if blocking_issues else "",
        }

    def _lammps_preflight_handlers(self) -> dict[str, Any]:
        return {
            "constraint_extract": self._preflight_constraint_extract_handler,
            "materials_rag_search": self._preflight_materials_rag_handler,
            "registry_lookup": self._preflight_registry_lookup_handler,
            "attachment_inspection": self._preflight_attachment_inspection_handler,
            "runtime_diagnostics": self._preflight_runtime_diagnostics_handler,
            "preflight_merge": self._preflight_merge_handler,
            "red_pre_execution_review": self._preflight_red_review_handler,
        }

    def _run_lammps_preflight_dag(
        self,
        *,
        run_id: str,
        request: AgentChatRequest,
        output_dir: Path,
        config: LammpsConfig,
        config_payload: dict[str, Any],
        lifecycle: TaskLifecycleController | None = None,
    ) -> tuple[DAGExecutionResult, dict[str, Any]]:
        plan = build_lammps_preflight_plan(
            requires_attachment=bool(request.uploaded_assets),
            metadata={"run_id": run_id, "conversation_id": request.conversation_id},
        )
        risk_hint = self._heuristic_request(request.message, request.notes)
        plan_risk = 0.35
        plan_risk += 0.2 if request.uploaded_assets else 0.0
        plan_risk += 0.15 if risk_hint.get("task_type") == "heating" else 0.0
        plan_risk += 0.2 if int(risk_hint.get("temperature") or 0) > 1200 else 0.0
        plan_risk += 0.15 if int(risk_hint.get("steps") or 0) > 10_000 else 0.0
        plan_risk = min(plan_risk, 1.0)
        plan_search = search_plans(
            build_plan_variants(plan),
            latency_budget_seconds=min(plan.global_timeout_seconds, 35 * 60),
            risk_level=plan_risk,
        )
        plan = plan_search.selected_plan.model_copy(
            update={
                "metadata": {
                    **plan_search.selected_plan.metadata,
                    "process_reward_search": plan_search.public_payload(),
                    "plan_risk_level": plan_risk,
                }
            }
        )
        if lifecycle is not None:
            lifecycle.record_plan_created(plan, metadata={"created_from": "initial"})
            lifecycle.save_checkpoint(stage="after_plan", plan=plan, metadata={"created_from": "initial"})
            lifecycle.transition(to_state="preflight", reason="lammps_preflight_dag_started", plan_version=plan.plan_version)
        context = DAGExecutionContext(
            run_id=run_id,
            conversation_id=request.conversation_id,
            input_payload={
                "message": request.message,
                "notes": request.notes,
                "uploaded_asset_count": len(request.uploaded_assets),
            },
            config_signature=json.dumps(config_payload, ensure_ascii=False, sort_keys=True, default=str),
        )
        context.metadata.update(
            {
                "request": request,
                "output_dir": output_dir,
                "config": config,
                "config_payload": config_payload,
            }
        )
        executor = DAGExecutor()

        def execute_preflight_plan(active_plan: DAGPlan, *, checkpoint_prefix: str = "") -> DAGExecutionResult:
            partial_results: dict[str, DAGNodeResult] = {}

            def save_node_checkpoint(node_result: DAGNodeResult) -> None:
                if lifecycle is None:
                    return
                partial_results[node_result.node_id] = node_result
                checkpoint_stage = f"after_node_{node_result.node_id}"
                if checkpoint_prefix:
                    checkpoint_stage = f"{checkpoint_prefix}_{checkpoint_stage}"
                lifecycle.save_checkpoint(
                    stage=checkpoint_stage,
                    plan=active_plan,
                    results=partial_results,
                    node_id=node_result.node_id,
                )

            return self._run_async_sync(
                executor.run(
                    active_plan,
                    context,
                    self._lammps_preflight_handlers(),
                    event_sink=lifecycle.record_dag_event if lifecycle is not None else None,
                    node_result_sink=save_node_checkpoint if lifecycle is not None else None,
                )
            )

        result = execute_preflight_plan(plan)
        budget_state = ReplanBudgetState(
            repair_budget=max(0, config.max_retries),
            replan_budget=max(1, config.max_retries + 1),
        )
        decision = decide_degradation(
            plan,
            result,
            budget_state=budget_state,
            last_checkpoint_id=lifecycle.state.last_checkpoint_id if lifecycle is not None else "",
        )
        replan_history: list[dict[str, Any]] = []
        final_plan = plan
        replan_executed = False
        if decision.degradation_level == "level_1_fallback":
            result = apply_level_1_fallbacks(result, decision)
            context.metadata["node_results"] = {
                node_id: node_result.model_dump(mode="json")
                for node_id, node_result in result.results.items()
            }
        elif decision.new_plan is not None and decision.can_continue:
            initial_decision_payload = decision.model_dump(mode="json")
            replan_history.append(initial_decision_payload)
            if lifecycle is not None:
                lifecycle.record_event("degradation.decision", initial_decision_payload, plan_version=plan.plan_version)
                lifecycle.transition(
                    to_state="repairing",
                    reason="preflight_level_2_replan",
                    plan_version=plan.plan_version,
                    metadata={
                        "invalidated_nodes": decision.invalidated_nodes,
                        "reused_nodes": decision.reused_nodes,
                        "failure_batch_id": decision.failure_batch.batch_id if decision.failure_batch else "",
                    },
                )
                lifecycle.record_plan_created(
                    decision.new_plan,
                    metadata={
                        "created_from": "runtime_replan",
                        "source_failure_batch": decision.failure_batch.batch_id if decision.failure_batch else "",
                        "invalidated_nodes": decision.invalidated_nodes,
                        "reused_nodes": decision.reused_nodes,
                    },
                )
                lifecycle.save_checkpoint(
                    stage="before_preflight_replan",
                    plan=plan,
                    dag_result=result,
                    metadata={"degradation_decision": initial_decision_payload},
                )

            context.metadata["reuse_node_results"] = {
                node_id: node_result.model_dump(mode="json")
                for node_id, node_result in result.results.items()
            }
            context.metadata["node_outputs"] = {}
            context.metadata["node_results"] = {}
            final_plan = decision.new_plan
            if lifecycle is not None:
                lifecycle.transition(
                    to_state="preflight",
                    reason="preflight_replan_started",
                    plan_version=final_plan.plan_version,
                    metadata={
                        "old_plan_id": plan.plan_id,
                        "new_plan_id": final_plan.plan_id,
                        "invalidated_nodes": decision.invalidated_nodes,
                        "reused_nodes": decision.reused_nodes,
                    },
                )
            result = execute_preflight_plan(final_plan, checkpoint_prefix=f"plan_v{final_plan.plan_version}")
            replan_executed = True
            previous_signature = str(decision.metadata.get("failure_signature") or "")
            replan_budget_state = ReplanBudgetState(
                repair_budget=decision.repair_budget_remaining,
                replan_budget=decision.replan_budget_remaining,
                previous_failure_signatures=[previous_signature] if previous_signature else [],
            )
            decision = decide_degradation(
                final_plan,
                result,
                budget_state=replan_budget_state,
                last_checkpoint_id=lifecycle.state.last_checkpoint_id if lifecycle is not None else "",
            )
            if decision.degradation_level == "level_1_fallback":
                result = apply_level_1_fallbacks(result, decision)
                context.metadata["node_results"] = {
                    node_id: node_result.model_dump(mode="json")
                    for node_id, node_result in result.results.items()
                }
        partial_report_path = ""
        if decision.partial_report is not None:
            partial_report_path = str(
                write_json_file(output_dir / "partial_result.json", decision.partial_report.model_dump(mode="json"))
            )
        decision_payload = decision.model_dump(mode="json")
        if partial_report_path:
            decision_payload["partial_report_path"] = partial_report_path
        if replan_history:
            decision_payload["replan_history"] = replan_history
        if replan_executed:
            decision_payload["replan_executed"] = True
            decision_payload["final_plan_id"] = final_plan.plan_id
            decision_payload["final_plan_version"] = final_plan.plan_version
        result = result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "degradation": decision_payload,
                    "plan_search": plan_search.public_payload(),
                    "process_reward": ProcessRewardModel().score_execution(final_plan, result),
                }
            }
        )
        if lifecycle is not None:
            lifecycle.record_event("degradation.decision", decision_payload, plan_version=final_plan.plan_version)
            if decision.new_plan is not None and not replan_executed:
                lifecycle.record_plan_created(
                    decision.new_plan,
                    metadata={
                        "created_from": "replan_decision",
                        "source_failure_batch": decision.failure_batch.batch_id if decision.failure_batch else "",
                    },
                )
            lifecycle.save_checkpoint(stage="after_preflight_dag", plan=final_plan, dag_result=result)
        return result, context.metadata

    def _run_multifidelity_pilot(
        self,
        *,
        request_payload: dict[str, Any],
        output_dir: Path,
        config: LammpsConfig,
        run_id: str,
    ) -> dict[str, Any]:
        plan = plan_multifidelity_run(request_payload, enabled=config.lammps_multifidelity_enabled)
        report: dict[str, Any] = {"schema_version": "lammps-multifidelity/v1", "plan": plan.public_payload()}
        if not plan.requires_pilot:
            report["decision"] = {
                "action": "skip",
                "reasons": ["pilot_not_required"],
                "value_of_information": 0.0,
            }
            write_json_file(output_dir / "multifidelity_report.json", report)
            return report

        pilot_dir = output_dir / "pilot"
        pilot_dir.mkdir(parents=True, exist_ok=True)
        pilot_request = {
            **request_payload,
            "steps": plan.pilot_steps,
            "dump_file": "pilot.dump",
            "script_name": "pilot.in.lammps",
        }
        pilot_input = generate_lammps_input(pilot_request, pilot_dir, potentials_dir=config.potentials_dir)
        metrics: dict[str, Any] = {}
        execution_success = False
        error = ""
        try:
            _, _, metrics = run_lammps(pilot_input, pilot_dir, pilot_request, config, run_id)
            execution_success = True
        except Exception as exc:  # noqa: BLE001 - converted into a value-of-information decision.
            error = str(exc)
        quality = build_physical_quality_report(
            output_dir=pilot_dir,
            request=pilot_request,
            run_mode="real",
            metrics=metrics,
            execution_error=error,
        )
        decision = evaluate_pilot(
            plan,
            execution_success=execution_success,
            quality_passed=quality.passed,
            scientific_result_passed=quality.scientific_result_passed,
            metrics=metrics,
            fatal_anomalies=len(quality.log_errors),
        )
        report.update(
            {
                "pilot_request": pilot_request,
                "pilot_metrics": metrics,
                "pilot_error": error,
                "pilot_quality": quality.model_dump(mode="json"),
                "decision": decision.public_payload(),
            }
        )
        write_json_file(output_dir / "multifidelity_report.json", report)
        return report

    def _parse_request(
        self,
        request: AgentChatRequest,
        *,
        attachment_overrides: dict[str, Any] | None = None,
        attachment_context: list[dict[str, Any]] | None = None,
        materials_rag_context: str = "",
    ) -> tuple[LammpsRequest, dict[str, Any]]:
        heuristic = self._heuristic_request(request.message, request.notes, attachment_overrides)
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ComputeAgent", capability=LLMCapability.LAMMPS_REQUEST_PARSE)
            return LammpsRequest.model_validate(heuristic), {"source": "heuristic_request_interpreter", "confidence": 0.55}

        registry = get_lammps_registry_payload()
        try:
            payload = self.llm_client.chat_json(
                system_prompt=(
                    "You are the LammpsRuntime request interpreter for a true multi-agent materials system. "
                    "Convert the user request into conservative JSON for a single-metal LAMMPS demo. "
                    "For a heating range such as 'from 300 K to 900 K', set initial_temp=300 and temperature=900. "
                    "Return JSON only with keys: material, potential_family, task_type, temperature, steps, ensemble, box_size, initial_temp, time_step, dump_file, custom_potential_path, custom_structure_path, custom_structure_format, notes, confidence."
                ),
                user_prompt=(
                    f"User message:\n{request.message}\n\n"
                    f"Caller notes:\n{request.notes}\n\n"
                    f"Uploaded assets:\n{json.dumps(attachment_context or [], ensure_ascii=False)}\n\n"
                    f"Materials RAG context:\n{materials_rag_context or '(none)'}\n\n"
                    f"Registry:\n{json.dumps(registry, ensure_ascii=False)}\n\n"
                    f"Heuristic baseline:\n{json.dumps(heuristic, ensure_ascii=False)}"
                ),
                max_tokens=900,
                temperature=0.1,
                capability=LLMCapability.LAMMPS_REQUEST_PARSE,
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
        explicit_temperatures = re.findall(r"\d{2,5}(?:\.\d+)?\s*(?:k|kelvin)\b", request.message.lower())
        if explicit_temperatures:
            # Explicit user temperatures are locked constraints. In particular,
            # LLMs often mistake the first value in "from 300 K to 900 K" for
            # the target; the deterministic parser has already separated the
            # initial and target temperatures above.
            merged["temperature"] = heuristic["temperature"]
            if "initial_temp" in heuristic:
                merged["initial_temp"] = heuristic["initial_temp"]
        if re.search(r"(?:\d{3,7}\s*steps?|步数\s*\d{3,7}|\d{3,7}\s*步)", request.message.lower()):
            merged["steps"] = heuristic["steps"]
        structured = LammpsRequest.model_validate(merged)
        confidence = payload.get("confidence", 0.82)
        try:
            score = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            score = 0.82
        return structured, {"source": "llm_request_interpreter", "confidence": score}

    def _legacy_repair_request(
        self,
        *,
        request: LammpsRequest,
        issues: list[str],
        stage: str,
        repair_history: list[dict[str, Any]] | None = None,
        repair_budget: int = 1,
        current_score: float | None = None,
    ) -> LammpsRequest | None:
        """Rollback repair path used when Red/Blue patching is disabled.

        This keeps the old request-delta behavior available behind a feature
        flag. It remains bounded by the same convergence guard, but it does not
        invoke the native Blue patch parser/policy stack.
        """

        history = repair_history if repair_history is not None else []
        gate_report = evaluate_repair_convergence(
            repair_history=history,
            current_request=request,
            stage=stage,
            repair_budget=repair_budget,
            current_score=current_score,
        )
        if not gate_report.allow_repair:
            if repair_history is not None:
                repair_history.append(
                    {
                        "entry_type": "legacy_repair_guard",
                        "stage": stage,
                        "issues": issues,
                        "convergence_report": gate_report.model_dump(mode="json"),
                    }
                )
            return None
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ComputeAgent", capability=LLMCapability.LAMMPS_REQUEST_REPAIR)
            return None
        system_prompt = (
            "You repair a structured LAMMPS request. Return a conservative JSON object containing only "
            "fields from the current LammpsRequest that need to change. Do not modify locked scientific "
            "constraints such as material, task_type, temperature, or steps."
        )
        user_prompt = (
            f"Current request:\n{request.model_dump_json()}\n\n"
            f"Stage:\n{stage}\n\n"
            f"Issues:\n{json.dumps(issues, ensure_ascii=False)}"
        )
        try:
            payload = self.llm_client.chat_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=700,
                temperature=0.1,
                capability=LLMCapability.LAMMPS_REQUEST_REPAIR,
            )
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"ComputeAgent 在 legacy 修复 LAMMPS 请求时调用 LLM 失败：{exc}") from exc
            return None
        if not isinstance(payload, dict) or not payload:
            return None
        allowed = set(LammpsRequest.model_fields)
        delta = {key: value for key, value in payload.items() if key in allowed and value not in (None, "")}
        if not delta:
            return None
        try:
            repaired = LammpsRequest.model_validate({**request.model_dump(mode="json"), **delta})
        except Exception:  # noqa: BLE001 - invalid legacy payloads are rejected safely.
            repaired = None
        validation = validate_request(repaired.model_dump(mode="json")) if repaired is not None else {"is_reasonable": False}
        convergence_report = evaluate_repair_convergence(
            repair_history=history,
            current_request=request,
            stage=stage,
            repair_budget=repair_budget,
            current_score=current_score,
            candidate_request=repaired,
            policy_accepted=bool(validation.get("is_reasonable")),
            policy_termination_reason="" if validation.get("is_reasonable") else "legacy_repair_validation_failed",
        )
        if repair_history is not None:
            repair_history.append(
                {
                    "entry_type": "legacy_repair_attempt",
                    "stage": stage,
                    "issues": issues,
                    "raw_payload": payload,
                    "delta_keys": sorted(delta),
                    "validation": validation,
                    "convergence_report": convergence_report.model_dump(mode="json"),
                }
            )
        if repaired is None or not validation.get("is_reasonable") or not convergence_report.allow_repair:
            return None
        return repaired

    def _repair_request(
        self,
        *,
        request: LammpsRequest,
        issues: list[str],
        stage: str,
        repair_history: list[dict[str, Any]] | None = None,
        repair_budget: int = 1,
        current_score: float | None = None,
        shared_memory_context: dict[str, Any] | None = None,
        red_blue_enabled: bool = True,
    ) -> LammpsRequest | None:
        if not red_blue_enabled:
            return self._legacy_repair_request(
                request=request,
                issues=issues,
                stage=stage,
                repair_history=repair_history,
                repair_budget=repair_budget,
                current_score=current_score,
            )
        history = repair_history if repair_history is not None else []
        gate_report = evaluate_repair_convergence(
            repair_history=history,
            current_request=request,
            stage=stage,
            repair_budget=repair_budget,
            current_score=current_score,
        )
        if not gate_report.allow_repair:
            if repair_history is not None:
                repair_history.append(
                    {
                        "entry_type": "repair_guard",
                        "stage": stage,
                        "issues": issues,
                        "convergence_report": gate_report.model_dump(mode="json"),
                    }
                )
            return None
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ComputeAgent", capability=LLMCapability.LAMMPS_REQUEST_REPAIR)
            return None
        system_prompt = (
            "You repair a structured LAMMPS request after validation/execution/review feedback. "
            "Prefer returning a native JSON RepairPatch with schema_version='lammps-blue-patch/v1' and operations. "
            "Allowed operations are add/delete/modify/verify. Use paths like time_step, box_size, dump_file, "
            "potential_family, ensemble, initial_temp, notes. Do not modify locked scientific/user constraints "
            "such as material, task_type, temperature, steps, custom_potential_path, custom_structure_path, "
            "or custom_structure_format. If you cannot safely repair, return an empty operations array. "
            "For compatibility, a conservative request-delta JSON is accepted but will be converted into a patch."
        )
        user_prompt = (
            f"Current request:\n{request.model_dump_json()}\n\n"
            f"Stage:\n{stage}\n\n"
            f"Issues:\n{json.dumps(issues, ensure_ascii=False)}"
        )
        memory_prompt = format_shared_memory_l2_context(shared_memory_context)
        if memory_prompt:
            user_prompt = (
                f"{user_prompt}\n\n"
                f"{memory_prompt}\n\n"
                "Blue patch rule: never change locked L1 user/scientific constraints from shared memory. "
                "If an issue conflicts with a locked constraint, return no mutation and explain via VERIFY/empty operations."
            )
        raw_text = ""
        raw_source = "chat_text"
        parsed_payload: dict[str, Any] | None = None
        try:
            chat_text = getattr(self.llm_client, "chat_text", None)
            if callable(chat_text):
                raw_text = str(
                    chat_text(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        max_tokens=900,
                        temperature=0.1,
                        capability=LLMCapability.LAMMPS_REQUEST_REPAIR,
                    )
                )
                parsed_payload = LLMClient.extract_json_object(raw_text)
            if parsed_payload is None:
                fallback_payload = self.llm_client.chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=900,
                    temperature=0.1,
                    capability=LLMCapability.LAMMPS_REQUEST_REPAIR,
                )
                if fallback_payload:
                    parsed_payload = fallback_payload
                    raw_text = json.dumps(fallback_payload, ensure_ascii=False)
                    raw_source = "chat_json_compatibility"
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"ComputeAgent 在修复 LAMMPS 请求时调用 LLM 失败：{exc}") from exc
            return None
        if not raw_text and not parsed_payload:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("ComputeAgent 需要 LLM 修复 LAMMPS 请求，但本次没有得到有效文本或 JSON。")
            return None
        patch_resolution = build_patch_from_llm_payload(
            request,
            raw_text=raw_text or json.dumps(parsed_payload or {}, ensure_ascii=False),
            parsed_payload=parsed_payload,
            stage=stage,
            issues=issues,
        )
        patch = patch_resolution.patch
        repaired, policy_report = verify_and_apply_patch(request, patch)
        convergence_report = evaluate_repair_convergence(
            repair_history=history,
            current_request=request,
            stage=stage,
            repair_budget=repair_budget,
            current_score=current_score,
            candidate_request=repaired,
            policy_accepted=policy_report.accepted,
            policy_termination_reason=policy_report.termination_reason,
        )
        if repair_history is not None:
            blue_parse_audit = patch_resolution.parse_audit.model_dump(mode="json")
            blue_parse_audit.update(
                {
                    "source": patch_resolution.source,
                    "fallback_used": patch_resolution.fallback_used,
                    "raw_source": raw_source,
                    "legacy_payload_keys": patch_resolution.legacy_payload_keys,
                }
            )
            repair_history.append(
                {
                    "entry_type": "repair_attempt",
                    "stage": stage,
                    "issues": issues,
                    "raw_payload": parsed_payload or {},
                    "blue_parse_audit": blue_parse_audit,
                    "patch": patch.model_dump(mode="json"),
                    "policy_report": policy_report.model_dump(mode="json"),
                    "convergence_report": convergence_report.model_dump(mode="json"),
                }
            )
        if not convergence_report.allow_repair:
            return None
        return repaired

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

    @staticmethod
    def _legacy_review_result(
        *,
        mode: str,
        artifacts: list[ArtifactRef],
        metrics: dict[str, Any],
        validation: dict[str, Any],
        error: str,
        quality_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Lightweight rollback reviewer used when Red/Blue is disabled."""

        artifact_names = {artifact.name for artifact in artifacts}
        required_artifacts = {"in.lammps", "thermo.csv", "report.md"}
        missing_artifacts = sorted(required_artifacts.difference(artifact_names))
        issues: list[str] = []
        advisory_issues: list[str] = []
        if not validation.get("is_reasonable", True):
            issues.extend(str(item) for item in validation.get("errors", []) if str(item).strip())
        if missing_artifacts:
            issues.append(f"Missing required LAMMPS artifacts: {', '.join(missing_artifacts)}")
        if error and mode == "real":
            issues.append(str(error))
        quality = quality_report or {}
        if quality.get("passed") is False:
            issues.append("Physical quality report did not pass.")
        if mode == "real" and quality.get("synthetic_thermo") is True:
            issues.append("Real execution cannot use synthetic thermo data.")
        if mode == "mock":
            advisory_issues.append("Legacy reviewer: mock output is infrastructure/demo evidence, not a scientific result.")
        passed = not issues
        return {
            "passed": passed,
            "summary": (
                "LAMMPS legacy review passed using artifact/validation/quality checks."
                if passed
                else f"LAMMPS legacy review found {len(issues)} issue(s)."
            ),
            "confidence": 0.82 if passed else 0.45,
            "issues": issues,
            "advisory_issues": advisory_issues,
            "llm_blocking_candidates": [],
            "review_mode": "legacy_review",
            "mode": mode,
            "llm_review_parse_audit": {},
            "feature_flag": "lammps_red_blue_review_enabled:false",
            "metrics_checked": sorted(str(key) for key in metrics),
        }

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
        quality_report: dict[str, Any] | None = None,
        shared_memory_context: dict[str, Any] | None = None,
        materials_rag_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        red_review = build_deterministic_review_report(
            request=request,
            mode=mode,
            artifacts=artifacts,
            metrics=metrics,
            validation=validation,
            error=error,
            input_script=input_script,
            quality_report=quality_report,
            phase="post_execution",
            shared_memory_context=shared_memory_context,
            materials_rag_context=materials_rag_context,
        )
        blocking_issues = [finding.message for finding in red_review.findings if finding.severity == "blocking"]
        advisory_issues = [finding.message for finding in red_review.findings if finding.severity == "warning"]
        artifact_names = [artifact.name for artifact in artifacts]
        review_mode = "deterministic_red_review"
        llm_summary = ""
        llm_confidence: float | None = None
        llm_blocking_candidates: list[str] = []
        llm_review_parse_audit: dict[str, Any] = {}
        if self.llm_client.is_configured():
            try:
                review_system_prompt = (
                    "You are reviewing a LAMMPS runtime agent run. "
                    "Return JSON only with keys: summary, confidence, passed, blocking_issues, advisory_issues. "
                    "Your blocking_issues are advisory candidates only; deterministic Red review is the only hard gate."
                )
                review_user_prompt = (
                    f"Request:\n{request.model_dump_json()}\n\n"
                    f"Mode:\n{mode}\n\n"
                    f"Validation:\n{json.dumps(validation, ensure_ascii=False)}\n\n"
                    f"Metrics:\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
                    f"Artifacts:\n{json.dumps(artifact_names, ensure_ascii=False)}\n\n"
                    f"Execution error:\n{error}\n\n"
                    f"in.lammps preview:\n{input_script[:2500]}"
                )
                memory_prompt = format_shared_memory_l2_context(shared_memory_context)
                if memory_prompt:
                    review_user_prompt = (
                        f"{review_user_prompt}\n\n"
                        f"{memory_prompt}\n\n"
                        "Red review rule: use shared memory as evidence context only. Locked user constraints are primary evidence; "
                        "RAG evidence is secondary and must not override deterministic validation, logs, or quality reports."
                    )
                rag_prompt = format_materials_rag_l2_context(materials_rag_context)
                if rag_prompt:
                    review_user_prompt = (
                        f"{review_user_prompt}\n\n"
                        f"{rag_prompt}\n\n"
                        "Red review rule: Materials RAG is secondary context only. It can support explanations and citations, "
                        "but cannot override primary request/script/execution/quality evidence."
                    )
                raw_review_text = self.llm_client.chat_text(
                    system_prompt=review_system_prompt,
                    user_prompt=review_user_prompt,
                    max_tokens=900,
                    temperature=0.1,
                    capability=LLMCapability.LAMMPS_REVIEW,
                )
                parsed_review = parse_review_payload(
                    raw_review_text,
                    schema=LLMReviewAdvisory,
                    payload_type="red_review_advisory",
                )
                if not parsed_review.success:
                    fallback_payload = self.llm_client.chat_json(
                        system_prompt=review_system_prompt,
                        user_prompt=review_user_prompt,
                        max_tokens=900,
                        temperature=0.1,
                        capability=LLMCapability.LAMMPS_REVIEW,
                    )
                    if fallback_payload:
                        parsed_review = parse_review_payload(
                            json.dumps(fallback_payload, ensure_ascii=False),
                            schema=LLMReviewAdvisory,
                            payload_type="red_review_advisory",
                        )
                        parsed_payload = parsed_review.model_dump(mode="json")
                        parsed_payload["raw_source"] = "chat_json_compatibility"
                        llm_review_parse_audit = parsed_payload
                    else:
                        llm_review_parse_audit = parsed_review.model_dump(mode="json")
                else:
                    parsed_payload = parsed_review.model_dump(mode="json")
                    parsed_payload["raw_source"] = "chat_text"
                    llm_review_parse_audit = parsed_payload
                payload = parsed_review.payload if parsed_review.success else None
            except RuntimeError as exc:
                if settings.require_llm_for_agents:
                    raise LLMRequiredError(f"ComputeAgent 在审查 LAMMPS 结果时调用 LLM 失败：{exc}") from exc
                payload = None
            if payload:
                review_mode = "llm_plus_deterministic_red_review"
                llm_summary = str(payload.get("summary") or "").strip()
                try:
                    llm_confidence = max(0.0, min(float(payload.get("confidence", 0.78)), 1.0))
                except (TypeError, ValueError):
                    llm_confidence = None
                for issue in payload.get("blocking_issues", []) if isinstance(payload.get("blocking_issues"), list) else []:
                    text = str(issue).strip()
                    if not text:
                        continue
                    if text not in llm_blocking_candidates:
                        llm_blocking_candidates.append(text)
                    advisory_text = f"LLM blocking candidate (advisory only): {text}"
                    if advisory_text not in advisory_issues:
                        advisory_issues.append(advisory_text)
                for issue in payload.get("advisory_issues", []) if isinstance(payload.get("advisory_issues"), list) else []:
                    text = str(issue).strip()
                    if text and text not in advisory_issues:
                        advisory_issues.append(text)
            elif settings.require_llm_for_agents:
                raise LLMRequiredError("ComputeAgent 需要 LLM 审查 LAMMPS 结果，但本次没有得到有效 JSON。")
        elif settings.require_llm_for_agents:
            self.llm_client.require_configured(agent_name="ComputeAgent", capability=LLMCapability.LAMMPS_REVIEW)

        passed = not blocking_issues
        confidence = max(0.2, min(0.99, float(red_review.score.overall_score) / 100.0))
        confidence = min(confidence, max(0.2, 0.93 - 0.18 * len(blocking_issues) - 0.03 * len(advisory_issues)))
        if llm_confidence is not None:
            confidence = min(confidence, llm_confidence)
        if llm_summary:
            advisory_text = f"LLM review summary (advisory only): {llm_summary}"
            if advisory_text not in advisory_issues:
                advisory_issues.append(advisory_text)
        summary = (
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
            "llm_blocking_candidates": llm_blocking_candidates,
            "review_mode": review_mode,
            "mode": mode,
            "llm_review_parse_audit": llm_review_parse_audit,
            "red_review": red_review.model_dump(mode="json"),
            "score": red_review.score.model_dump(mode="json"),
            "findings": [finding.model_dump(mode="json") for finding in red_review.findings],
            "evidence_refs": [evidence.model_dump(mode="json") for evidence in red_review.evidence_refs],
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
        response = AgentRunResponse(
            success=status not in {"failed", "cancelled"},
            run_id=run_id,
            conversation_id=conversation_id,
            route=route,
            final_message=final_message,
            artifacts=artifacts,
            plan_steps=[],
            trace=trace,
            metadata=metadata,
            summary=summary,
            run_status=status,
        )
        self.artifact_service.write_run_summary(response)

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
        quality = state.get("quality", {}) or {}
        summary = state.get("summary", {}) or {}
        config = state.get("config", {}) or {}
        run_mode = str(state.get("run_mode", "") or "")
        ovito_status = (((summary.get("postprocess") or {}) if isinstance(summary, dict) else {}).get("ovito_status") or {})

        warnings = [str(item) for item in review.get("advisory_issues", []) if str(item).strip()]
        warnings.extend(str(item) for item in quality.get("warnings", []) if str(item).strip())
        if run_mode == "mock":
            warnings.append("This result used mock fallback instead of a real local LAMMPS execution.")
        if not ovito_status.get("generated"):
            reason = str(ovito_status.get("reason") or "").strip()
            warnings.append(f"OVITO animation not fully available{f': {reason}' if reason else '.'}")
        if review.get("issues"):
            warnings.extend(str(item) for item in review.get("issues", []) if str(item).strip())
        if quality.get("issues"):
            warnings.extend(str(item) for item in quality.get("issues", []) if str(item).strip())

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
        if quality:
            evidence.append(f"Physical quality: passed={quality.get('passed')}, scientific={quality.get('scientific_result_passed')}")

        confidence = review.get("confidence")
        try:
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_value = None
        if confidence_value is None:
            confidence_value = 0.9 if run_mode == "real" and success else 0.55 if success else 0.3
        if run_mode == "mock":
            confidence_value = min(confidence_value, 0.55)
        if quality and not quality.get("scientific_result_passed", False):
            confidence_value = min(confidence_value, 0.55 if success else 0.3)

        if success and run_mode == "real" and quality.get("scientific_result_passed", True):
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
        prestructured_request: LammpsRequest | None = None,
        prestructured_parse_info: dict[str, Any] | None = None,
        shared_memory_context: dict[str, Any] | None = None,
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
            "quality": {},
            "repair_history": [],
            "summary": {},
            "materials_rag": {},
            "preflight_dag": {},
            "lifecycle": {},
            "shared_memory_context": shared_memory_context or {},
            "event_sink": event_sink,
        }
        initialize_runtime_state(
            state,
            runtime_name="LammpsRuntime",
            capability_tags=[
                "lammps",
                "materials_rag",
                "local_md_execute",
                "trajectory_postprocess",
                "ovito_optional",
                "review_repair_loop",
            ],
        )
        clear_cancellation(run_id)
        config = self.config_loader()
        explicit_mock_requested = self._explicit_mock_requested(request.message)
        # `allow_mock_fallback` is only an authorization switch. A normal
        # LAMMPS request remains real-only even if an old installation still
        # has that switch enabled. `force_mock` is reserved for an explicit
        # developer/test configuration.
        config.allow_mock_fallback = bool(config.allow_mock_fallback and explicit_mock_requested)
        state["config"] = {
            **self._lammps_config_payload(config),
            "execution_policy": "forced_mock_debug" if config.force_mock else "explicit_mock_demo" if config.allow_mock_fallback else "real_required",
            "explicit_mock_requested": explicit_mock_requested,
        }
        request_attempt: LammpsRequest | None = prestructured_request
        parse_info: dict[str, Any] = dict(prestructured_parse_info or {})
        max_retries = max(0, config.max_retries)
        output_dir = self.artifact_service.get_run_dir(run_id)
        lifecycle = TaskLifecycleController(run_id=run_id, run_dir=output_dir, event_sink=event_sink)
        state["_lifecycle_controller"] = lifecycle
        state["lifecycle"] = lifecycle.snapshot()
        self._transition_lifecycle(
            state,
            to_state="planning",
            reason="lammps_runtime_started",
            metadata={"feature_flag_preflight_dag": config.lammps_preflight_dag_enabled},
        )
        uploaded_attachments: list[dict[str, Any]] = []
        attachment_overrides: dict[str, Any] = {}
        planning_rag: dict[str, object] = {}
        preflight_metadata: dict[str, Any] = {}
        preflight_used = False
        if config.lammps_preflight_dag_enabled:
            try:
                preflight_result, preflight_metadata = self._run_lammps_preflight_dag(
                    run_id=run_id,
                    request=request,
                    output_dir=output_dir,
                    config=config,
                    config_payload=state["config"],
                    lifecycle=self._lifecycle(state),
                )
                state["preflight_dag"] = preflight_result.model_dump(mode="json")
                self._record_step(
                    state,
                    tool_name="lammps_preflight_dag",
                    success=preflight_result.status == "completed",
                    summary=(
                        f"LAMMPS preflight DAG completed in {preflight_result.duration_seconds:.2f}s."
                        if preflight_result.status == "completed"
                        else f"LAMMPS preflight DAG ended with status={preflight_result.status}; falling back to legacy preflight."
                    ),
                    input_data={
                        "message": request.message,
                        "nodes": LAMMPS_PREFLIGHT_NODE_IDS,
                    },
                    output_data=preflight_result.model_dump(mode="json"),
                    description="Run parallel LAMMPS preflight DAG for RAG, registry, attachment, diagnostics, merge, and review.",
                    stage="lammps_preflight_dag",
                    metadata={"feature_flag": "lammps_preflight_dag_enabled"},
                )
                degradation_payload = preflight_result.metadata.get("degradation", {})
                if degradation_payload.get("degradation_level") == "level_3_partial_report":
                    partial_report_path = degradation_payload.get("partial_report_path", "")
                    partial_artifacts = []
                    if partial_report_path:
                        partial_artifacts.append(
                            self._build_artifact(
                                run_id=run_id,
                                kind="json",
                                name="partial_result.json",
                                path=partial_report_path,
                                metadata={"artifact_role": "partial_result", "termination_reason": "global_timeout"},
                            )
                        )
                    state["artifacts"] = self._merge_artifacts(state["artifacts"], partial_artifacts)
                    state["summary"] = {
                        "success": False,
                        "termination_reason": "global_timeout",
                        "preflight_dag": preflight_result.model_dump(mode="json"),
                        "partial_report": degradation_payload.get("partial_report", {}),
                    }
                    return self._finalize(
                        state,
                        success=False,
                        final_message="LAMMPS preflight DAG 达到全局超时，本轮已生成 partial_result.json；不会把未完成结果伪装成科学结论。",
                        termination_reason="global_timeout",
                        conversation_id=request.conversation_id,
                        run_status="failed",
                    )
                if preflight_result.status == "completed":
                    node_outputs = preflight_metadata.get("node_outputs", {})
                    if isinstance(node_outputs, dict):
                        attachment_output = node_outputs.get("attachment_inspection", {})
                        rag_output = node_outputs.get("materials_rag_search", {})
                        if isinstance(attachment_output, dict):
                            uploaded_attachments = list(attachment_output.get("uploaded_attachments", []))
                            attachment_overrides = dict(attachment_output.get("attachment_overrides", {}))
                        if isinstance(rag_output, dict) and isinstance(rag_output.get("planning_rag"), dict):
                            planning_rag = dict(rag_output["planning_rag"])
                    preflight_used = True
                    if self._lifecycle(state) is not None:
                        self._lifecycle(state).record_event("preflight.batch_completed", {"preflight_status": preflight_result.status})
                        state["lifecycle"] = self._lifecycle(state).snapshot()
                else:
                    state["preflight_dag"]["fallback"] = "legacy_preflight"
                    if self._lifecycle(state) is not None:
                        self._lifecycle(state).record_event("preflight.fallback_to_legacy", {"preflight_status": preflight_result.status})
                        state["lifecycle"] = self._lifecycle(state).snapshot()
            except Exception as exc:  # noqa: BLE001 - DAG integration must not break the stable legacy path.
                state["preflight_dag"] = {
                    "status": "failed",
                    "error": str(exc),
                    "fallback": "legacy_preflight",
                }
                self._record_step(
                    state,
                    tool_name="lammps_preflight_dag",
                    success=False,
                    summary=f"LAMMPS preflight DAG failed before completion，已回退到 legacy preflight：{exc}",
                    input_data={"message": request.message, "nodes": LAMMPS_PREFLIGHT_NODE_IDS},
                    output_data=state["preflight_dag"],
                    description="Run parallel LAMMPS preflight DAG for RAG, registry, attachment, diagnostics, merge, and review.",
                    stage="lammps_preflight_dag",
                    metadata={"feature_flag": "lammps_preflight_dag_enabled", "fallback": "legacy_preflight"},
                )
                if self._lifecycle(state) is not None and self._lifecycle(state).state.current_state == "planning":
                    self._transition_lifecycle(
                        state,
                        to_state="preflight",
                        reason="lammps_preflight_dag_exception_enter_legacy_preflight",
                        metadata={"error": str(exc)},
                    )
        if not preflight_used:
            self._transition_lifecycle(
                state,
                to_state="preflight",
                reason="legacy_lammps_preflight_started",
                metadata={"preflight_dag_enabled": config.lammps_preflight_dag_enabled},
            )
            uploaded_attachments = persist_uploaded_assets(request.uploaded_assets, output_dir)
            attachment_overrides = infer_request_overrides(uploaded_attachments)
            planning_rag = self._planning_materials_rag(request)
            if self._lifecycle(state) is not None:
                self._lifecycle(state).record_event(
                    "preflight.legacy_completed",
                    {"preflight_dag_enabled": config.lammps_preflight_dag_enabled},
                )
                state["lifecycle"] = self._lifecycle(state).snapshot()
        elif not planning_rag:
            planning_rag = {
                "query": request.message,
                "material": self._materials_rag_material_hint(request.message),
                "hits": [],
                "serialized_hits": [],
                "context": "",
                "retrieval_uncertainty": estimate_retrieval_uncertainty([]).public_payload(),
            }
        serialized_planning_hits = self._planning_materials_rag_serialized_hits(planning_rag)
        state["materials_rag"] = {
            "planning": {
                "query": planning_rag["query"],
                "material": planning_rag["material"],
                "hits": serialized_planning_hits,
                "retrieval_uncertainty": planning_rag.get("retrieval_uncertainty", {}),
                "source": "preflight_dag" if preflight_used else "legacy_preflight",
            }
        }
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
        self._record_step(
            state,
            tool_name="materials_rag_search",
            success=True,
            summary=(
                f"已召回 {len(serialized_planning_hits)} 条材料知识，用于 LAMMPS 请求解释和参数建议。"
                if serialized_planning_hits
                else "未召回额外的材料知识卡片，继续使用现有 registry 与请求解析链路。"
            ),
            input_data={"message": request.message},
            output_data={
                "material": planning_rag["material"],
                "hits": serialized_planning_hits,
                "source": "preflight_dag" if preflight_used else "legacy_preflight",
            },
            description="Retrieve lightweight materials-domain knowledge before LAMMPS request parsing.",
            stage="materials_rag_preflight",
        )

        for attempt in range(max_retries + 1):
            if request_attempt is None:
                request_attempt, parse_info = self._parse_request(
                    request,
                    attachment_overrides=attachment_overrides,
                    attachment_context=uploaded_attachments,
                    materials_rag_context=str(planning_rag.get("context") or ""),
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
                    repaired = self._repair_request(
                        request=request_attempt,
                        issues=issues,
                        stage="registry_lookup",
                        repair_history=state["repair_history"],
                        repair_budget=max_retries,
                        shared_memory_context=state.get("shared_memory_context", {}),
                        red_blue_enabled=config.lammps_red_blue_review_enabled,
                    )
                    if repaired is not None:
                        request_attempt = repaired
                        parse_info = {"source": "llm_request_repair", "confidence": 0.7}
                        self._transition_lifecycle(
                            state,
                            to_state="repairing",
                            reason="registry_lookup_repair_requested",
                            metadata={"issues": issues, "attempt": attempt + 1},
                        )
                        self._transition_lifecycle(
                            state,
                            to_state="preflight",
                            reason="registry_lookup_repair_applied",
                            metadata={"attempt": attempt + 1},
                        )
                        continue
                return self._finalize(
                    state,
                    success=False,
                    final_message="当前 LAMMPS registry 还不支持这个材料/势函数/任务组合，所以这轮没有进入真实执行。",
                    termination_reason=self._repair_stop_reason(state, "lammps_registry_not_found"),
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
                    repaired = self._repair_request(
                        request=request_attempt,
                        issues=issues,
                        stage="validation",
                        repair_history=state["repair_history"],
                        repair_budget=max_retries,
                        shared_memory_context=state.get("shared_memory_context", {}),
                        red_blue_enabled=config.lammps_red_blue_review_enabled,
                    )
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
                        self._transition_lifecycle(
                            state,
                            to_state="repairing",
                            reason="validation_repair_requested",
                            metadata={"issues": issues, "attempt": attempt + 1},
                        )
                        self._transition_lifecycle(
                            state,
                            to_state="preflight",
                            reason="validation_repair_applied",
                            metadata={"attempt": attempt + 1},
                        )
                        continue
                return self._finalize(
                    state,
                    success=False,
                    final_message="这轮 LAMMPS 任务没有通过参数校验，我已保留 trace 和错误信息，方便继续调整请求。",
                    termination_reason=self._repair_stop_reason(state, "lammps_validation_failed"),
                    conversation_id=request.conversation_id,
                    run_status="failed",
                )

            self._transition_lifecycle(
                state,
                to_state="ready",
                reason="lammps_registry_and_validation_passed",
                metadata={"attempt": attempt + 1},
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
            ir_artifacts = [
                self._build_artifact(
                    run_id=run_id,
                    kind="json",
                    name="lammps_ir.json",
                    path=output_dir / "lammps_ir.json",
                    metadata={"artifact_role": "typed_simulation_ir", "compiler": "matterlab-neurosymbolic/v1"},
                ),
                self._build_artifact(
                    run_id=run_id,
                    kind="json",
                    name="lammps_ir_validation.json",
                    path=output_dir / "lammps_ir_validation.json",
                    metadata={"artifact_role": "symbolic_constraint_report"},
                ),
            ]
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
                artifacts=[input_artifact, request_artifact, *ir_artifacts],
            )

            self._transition_lifecycle(
                state,
                to_state="running",
                reason="lammps_input_generated_and_execution_started",
                metadata={"attempt": attempt + 1, "input_path": str(input_path)},
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
                metadata={
                    "parse_result": parse_info,
                    "validation": validation,
                    "config": state["config"],
                    "preflight_dag": state.get("preflight_dag", {}),
                },
            )

            mode = "real"
            error = ""
            try:
                if config.force_mock:
                    raise RuntimeError("Mock mode forced by USE_MOCK=true.")
                multifidelity_report = self._run_multifidelity_pilot(
                    request_payload=request_payload,
                    output_dir=output_dir,
                    config=config,
                    run_id=run_id,
                )
                state["multifidelity"] = multifidelity_report
                multifidelity_artifact = self._build_artifact(
                    run_id=run_id,
                    kind="json",
                    name="multifidelity_report.json",
                    path=output_dir / "multifidelity_report.json",
                    metadata={"artifact_role": "value_of_information_simulation_policy"},
                )
                self._record_step(
                    state,
                    tool_name="lammps_multifidelity_scheduler",
                    success=str(multifidelity_report.get("decision", {}).get("action")) in {"skip", "continue_full"},
                    summary=f"多保真调度决策：{multifidelity_report.get('decision', {}).get('action', 'unknown')}。",
                    input_data={"request": request_payload},
                    output_data=multifidelity_report,
                    description="Estimate value of information with a short pilot before an expensive full run.",
                    stage=f"lammps_multifidelity_{attempt + 1}",
                    artifacts=[multifidelity_artifact],
                )
                pilot_action = str(multifidelity_report.get("decision", {}).get("action") or "skip")
                if pilot_action in {"repair", "stop"}:
                    raise RuntimeError(f"Multi-fidelity pilot rejected the full run: {pilot_action}.")
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
            except ThermoParseError as exc:
                error = str(exc)
                state["error"] = error
                state["run_mode"] = "real"
                self._record_step(
                    state,
                    tool_name="lammps_execute",
                    success=False,
                    summary=f"LAMMPS 真实执行完成，但 thermo 解析失败：{error}",
                    input_data={"input_path": str(input_path)},
                    output_data={"mode": "real", "error": error},
                    description="Execute the generated LAMMPS input locally and require real thermo output.",
                    stage=f"lammps_execute_{attempt + 1}",
                    retryable=False,
                    state_delta={"run_mode": "real"},
                )
                quality_report = build_physical_quality_report(
                    output_dir=output_dir,
                    request=request_payload,
                    run_mode="real",
                    metrics={},
                    execution_error=error,
                )
                quality_path = write_physical_quality_report(output_dir / "quality_report.json", quality_report)
                quality_artifact = self._build_artifact(
                    run_id=run_id,
                    kind="json",
                    name="quality_report.json",
                    path=quality_path,
                    metadata={"artifact_role": "physical_quality_report"},
                )
                state["quality"] = quality_report.model_dump(mode="json")
                state["summary"] = {
                    "mode": "real",
                    "request": request_payload,
                    "quality": state["quality"],
                    "termination_reason": "thermo_parse_failed",
                }
                self._record_step(
                    state,
                    tool_name="lammps_physical_quality_gate",
                    success=False,
                    summary="真实 LAMMPS 输出缺少可解析 thermo 行，本轮终止且不会使用 synthetic thermo 伪装真实结果。",
                    input_data={"mode": "real", "thermo_csv": str(output_dir / "thermo.csv")},
                    output_data=state["quality"],
                    description="Validate real LAMMPS thermo/log/dump outputs before scientific success is allowed.",
                    stage=f"lammps_physical_quality_gate_{attempt + 1}",
                    artifacts=[quality_artifact],
                )
                return self._finalize(
                    state,
                    success=False,
                    final_message="LAMMPS 真实执行结束，但没有解析到可信 thermo 数据；我已终止本轮并保存 quality_report.json，没有生成 synthetic thermo 来冒充真实结果。",
                    termination_reason="thermo_parse_failed",
                    conversation_id=request.conversation_id,
                    run_status="failed",
                )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                state["error"] = error
                error_rag = self._error_materials_rag(
                    error_text=error,
                    material=request_attempt.material,
                    request_message=request.message,
                )
                state["materials_rag"]["error_diagnosis"] = {
                    "query": error_rag["query"],
                    "material": error_rag["material"],
                    "hits": self._serialize_materials_rag_hits(list(error_rag.get("hits", []))),
                }
                self._record_step(
                    state,
                    tool_name="materials_rag_error_search",
                    success=True,
                    summary=(
                        f"已为执行错误召回 {len(error_rag.get('hits', []))} 条诊断知识。"
                        if error_rag.get("hits")
                        else "没有从材料知识库中召回到匹配的 LAMMPS 错误诊断卡片。"
                    ),
                    input_data={"error": error},
                    output_data={"hits": self._serialize_materials_rag_hits(list(error_rag.get("hits", [])))},
                    description="Retrieve error-cookbook knowledge for a failed LAMMPS execution.",
                    stage=f"materials_rag_error_search_{attempt + 1}",
                    retryable=attempt < max_retries,
                )
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
                        repaired = self._repair_request(
                            request=request_attempt,
                            issues=[error],
                            stage="execution",
                            repair_history=state["repair_history"],
                            repair_budget=max_retries,
                            shared_memory_context=state.get("shared_memory_context", {}),
                            red_blue_enabled=config.lammps_red_blue_review_enabled,
                        )
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
                            self._transition_lifecycle(
                                state,
                                to_state="repairing",
                                reason="execution_repair_requested",
                                metadata={"issues": [error], "attempt": attempt + 1},
                            )
                            self._transition_lifecycle(
                                state,
                                to_state="ready",
                                reason="execution_repair_applied",
                                metadata={"attempt": attempt + 1},
                            )
                            continue
                    return self._finalize(
                        state,
                        success=False,
                        final_message="\n\n".join(
                            [
                                "本地 LAMMPS 执行失败，我已保留输入脚本和错误信息，方便继续修复。",
                                *self._error_diagnostic_lines(error_rag),
                            ]
                        ),
                        termination_reason=self._repair_stop_reason(state, "lammps_execution_failed"),
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
                metadata={
                    "parse_result": parse_info,
                    "validation": validation,
                    "config": state["config"],
                    "preflight_dag": state.get("preflight_dag", {}),
                },
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

            quality_report = build_physical_quality_report(
                output_dir=output_dir,
                request=request_payload,
                run_mode="real" if mode == "real" else "mock",
                metrics=metrics,
                execution_error=error,
            )
            quality_path = write_physical_quality_report(output_dir / "quality_report.json", quality_report)
            quality_artifact = self._build_artifact(
                run_id=run_id,
                kind="json",
                name="quality_report.json",
                path=quality_path,
                metadata={
                    "artifact_role": "physical_quality_report",
                    "scientific_result_passed": quality_report.scientific_result_passed,
                },
            )
            state["quality"] = quality_report.model_dump(mode="json")
            self._record_step(
                state,
                tool_name="lammps_physical_quality_gate",
                success=quality_report.passed,
                summary=(
                    "物理质量门通过，可继续后处理。"
                    if quality_report.passed
                    else "物理质量门未通过，停止把本轮作为有效科学结果返回。"
                ),
                input_data={"mode": mode, "metrics": metrics},
                output_data=state["quality"],
                description="Validate LAMMPS thermo/log/dump outputs before post-processing and review.",
                stage=f"lammps_physical_quality_gate_{attempt + 1}",
                artifacts=[quality_artifact],
            )
            if not quality_report.passed:
                state["summary"] = {
                    "mode": mode,
                    "request": request_payload,
                    "metrics": metrics,
                    "quality": state["quality"],
                    "termination_reason": "physical_quality_failed",
                }
                return self._finalize(
                    state,
                    success=False,
                    final_message="LAMMPS 已产生输出，但物理质量门发现异常；我已保存 quality_report.json，未将本轮标记为有效科学结果。",
                    termination_reason="physical_quality_failed",
                    conversation_id=request.conversation_id,
                    run_status="failed",
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

            self._transition_lifecycle(
                state,
                to_state="reviewing",
                reason="lammps_postprocess_completed",
                metadata={"attempt": attempt + 1, "mode": mode},
            )
            if config.lammps_red_blue_review_enabled:
                review = self._review_result(
                    request=request_attempt,
                    mode=mode,
                    artifacts=state["artifacts"],
                    metrics=metrics,
                    validation=validation,
                    error=error,
                    input_script=generated_input,
                    quality_report=state.get("quality", {}),
                    shared_memory_context=state.get("shared_memory_context", {}),
                    materials_rag_context=state.get("materials_rag", {}),
                )
            else:
                review = self._legacy_review_result(
                    mode=mode,
                    artifacts=state["artifacts"],
                    metrics=metrics,
                    validation=validation,
                    error=error,
                    quality_report=state.get("quality", {}),
                )
            state["review"] = review
            red_review_artifacts: list[ArtifactRef] = []
            red_review_payload = review.get("red_review")
            if isinstance(red_review_payload, dict):
                red_review_path = write_json_file(output_dir / "red_review_post.json", red_review_payload)
                red_review_artifacts.append(
                    self._build_artifact(
                        run_id=run_id,
                        kind="json",
                        name="red_review_post.json",
                        path=red_review_path,
                        metadata={"artifact_role": "red_review_post", "review_mode": review.get("review_mode", "")},
                    )
                )
            summary_payload = {
                "mode": mode,
                "request": request_payload,
                "metrics": metrics,
                "validation": validation,
                "config": state["config"],
                "materials_rag": state.get("materials_rag", {}),
                "preflight_dag": state.get("preflight_dag", {}),
                "quality": state.get("quality", {}),
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
                metadata={
                    "parse_result": parse_info,
                    "validation": validation,
                    "config": state["config"],
                    "run_mode": mode,
                    "preflight_dag": state.get("preflight_dag", {}),
                    "quality": state.get("quality", {}),
                },
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
                artifacts=red_review_artifacts,
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
                repaired = self._repair_request(
                    request=request_attempt,
                    issues=issues,
                    stage="review",
                    repair_history=state["repair_history"],
                    repair_budget=max_retries,
                    current_score=self._review_overall_score(review),
                    shared_memory_context=state.get("shared_memory_context", {}),
                    red_blue_enabled=config.lammps_red_blue_review_enabled,
                )
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
                    self._transition_lifecycle(
                        state,
                        to_state="repairing",
                        reason="review_repair_requested",
                        metadata={"issues": issues, "attempt": attempt + 1},
                    )
                    self._transition_lifecycle(
                        state,
                        to_state="ready",
                        reason="review_repair_applied",
                        metadata={"attempt": attempt + 1},
                    )
                    continue

            return self._finalize(
                state,
                success=False,
                final_message="这轮 LAMMPS 结果没有通过完整自检，我已保留输入脚本、trace 和产物，方便继续修复。",
                termination_reason=self._repair_stop_reason(state, "review_failed"),
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

    def run_structured(
        self,
        *,
        run_id: str,
        structured_request: LammpsRequest | dict[str, Any],
        conversation_id: str = "mcp-lammps-structured",
        original_query: str = "",
        uploaded_assets: list[Any] | None = None,
        decision: dict[str, Any] | None = None,
        event_sink=None,
        existing_plan_steps: list[PlanStep] | None = None,
        existing_trace: list[ToolObservation] | None = None,
        existing_artifacts: list[ArtifactRef] | None = None,
        shared_memory_context: dict[str, Any] | None = None,
    ) -> AgentRunResponse:
        request_payload = (
            structured_request
            if isinstance(structured_request, LammpsRequest)
            else LammpsRequest.model_validate(structured_request)
        )
        request = AgentChatRequest(
            conversation_id=conversation_id,
            message=original_query or "Structured LAMMPS request triggered via MCP.",
            notes="Triggered via MCP lammps.run_structured",
            uploaded_assets=list(uploaded_assets or []),
            conversation_history=[],
        )
        return self.run(
            run_id=run_id,
            request=request,
            decision=decision,
            event_sink=event_sink,
            existing_plan_steps=existing_plan_steps,
            existing_trace=existing_trace,
            existing_artifacts=existing_artifacts,
            prestructured_request=request_payload,
            prestructured_parse_info={"source": "structured_request", "confidence": 1.0},
            shared_memory_context=shared_memory_context,
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
        controller = self._lifecycle(state)
        if controller is not None and controller.state.current_state not in {"completed", "terminated"}:
            if success:
                self._transition_lifecycle(
                    state,
                    to_state="completed",
                    reason=termination_reason,
                    metadata={"run_status": run_status},
                )
            else:
                self._transition_lifecycle(
                    state,
                    to_state="terminated",
                    reason=termination_reason,
                    termination_reason=termination_reason,
                    metadata={"run_status": run_status},
                )
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
                "parse_result": state.get("parse_result", {}),
                "validation": state.get("validation", {}),
                "config": state.get("config", {}),
                "run_mode": state.get("run_mode", ""),
                "review": state.get("review", {}),
                "quality": state.get("quality", {}),
                "repair_history": state.get("repair_history", []),
                "materials_rag": state.get("materials_rag", {}),
                "preflight_dag": state.get("preflight_dag", {}),
                "lifecycle": state.get("lifecycle", {}),
                "summary": state.get("summary", {}),
                "runtime_profile": runtime_profile,
            },
        )
        trace_path = self.artifact_service.write_trace(trace_model)
        trace_artifact = self._build_artifact(run_id=state["run_id"], kind="json", name="trace.json", path=trace_path)
        extra_artifacts = [trace_artifact]
        llm_parse_audits = self._collect_llm_parse_audits(state)
        if llm_parse_audits:
            llm_parse_audit_path = write_json_file(
                self.artifact_service.get_run_dir(state["run_id"]) / "llm_parse_audit.json",
                {"parse_audits": llm_parse_audits},
            )
            extra_artifacts.append(
                self._build_artifact(
                    run_id=state["run_id"],
                    kind="json",
                    name="llm_parse_audit.json",
                    path=llm_parse_audit_path,
                    metadata={"artifact_role": "llm_parse_audit"},
                )
            )
        if state.get("repair_history"):
            repair_history_path = write_json_file(
                self.artifact_service.get_run_dir(state["run_id"]) / "repair_history.json",
                {"repair_history": state["repair_history"]},
            )
            extra_artifacts.append(
                self._build_artifact(
                    run_id=state["run_id"],
                    kind="json",
                    name="repair_history.json",
                    path=repair_history_path,
                    metadata={"artifact_role": "repair_history"},
                )
            )
        if controller is not None and controller.lifecycle_path.exists():
            extra_artifacts.append(
                self._build_artifact(
                    run_id=state["run_id"],
                    kind="json",
                    name="lifecycle.json",
                    path=controller.lifecycle_path,
                    metadata={"artifact_role": "runtime_lifecycle"},
                )
            )
        artifacts = self._merge_artifacts(state["artifacts"], extra_artifacts)
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
                "quality": state.get("quality", {}),
                "repair_history": state.get("repair_history", []),
                "materials_rag": state.get("materials_rag", {}),
                "preflight_dag": state.get("preflight_dag", {}),
                "lifecycle": state.get("lifecycle", {}),
                "runtime_profile": runtime_profile,
            },
            recognition_result=None,
            current_context_summary="",
            summary=state.get("summary", {}),
            run_status=run_status,
        )
        if state.get("preflight_dag"):
            response.summary.setdefault("preflight_dag", state.get("preflight_dag", {}))
        if state.get("lifecycle"):
            response.summary.setdefault("lifecycle", state.get("lifecycle", {}))
        if state.get("quality"):
            response.summary.setdefault("quality", state.get("quality", {}))
        response.summary["result_profile"] = result_profile
        response.summary["runtime_profile"] = runtime_profile
        response.metadata["result_profile"] = result_profile
        response.metadata["runtime_profile"] = runtime_profile
        self._write_run_record(
            run_id=state["run_id"],
            conversation_id=conversation_id,
            route=state["route"],
            status=run_status,
            final_message=final_message,
            summary=response.summary,
            artifacts=artifacts,
            trace=state["trace"],
            metadata=response.metadata,
        )
        clear_cancellation(state["run_id"])
        return response
