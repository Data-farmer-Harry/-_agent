from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import mimetypes
import os
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import api as api_module
from app.core.artifacts import ArtifactService
from app.jobs import AgentJobStore, AgentJobWorker
from app.lammps.config import LammpsConfig
from app.lammps.preflight import LAMMPS_PREFLIGHT_NODE_IDS, build_lammps_preflight_plan
from app.lammps.quality import build_physical_quality_report
from app.lammps.review import (
    RepairPatch,
    ReviewReport,
    blocking_findings_have_primary_evidence,
    build_deterministic_review_report,
    build_patch_from_llm_payload,
    evaluate_repair_convergence,
    parse_review_payload,
    verify_and_apply_patch,
)
from app.memory import MemoryStore
from app.mcp_server import MaterialsMcpServer
from app.orchestration import (
    DAGExecutionContext,
    DAGExecutor,
    DAGNode,
    DAGPlan,
    DAGResourceLimits,
    ReplanBudgetState,
    TaskLifecycleController,
    apply_level_1_fallbacks,
    decide_degradation,
)
from app.runtimes.lammps import LammpsRuntime
from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService
from app.state import AgentChatRequest, AgentGraphState, AgentRunResponse, AgentStreamEvent, ArtifactRef, ConversationTurn, LammpsRequest, LastRunContext, RecognitionResult, TaskRoute, UploadedAsset
from benchmarks.benchmark_config import (
    BENCHMARK_THRESHOLDS,
    DEFAULT_BENCHMARK_OUTPUT,
    DETERMINISTIC_SUITES,
    LIVE_SUITES,
)
from benchmarks.build_datasets import build_manifest
from benchmarks.dataset_io import load_datasets, resolve_backend_path as _resolve_backend_path, validate_datasets
from benchmarks.evaluators import (
    build_judge_backend_matrix,
    build_judge_drift_report,
    evaluate_judge_calibration_case,
    evaluate_judge_with_provider,
    evaluate_materials_multihop,
    judge_provider_config_from_env,
    sanitized_provider_metadata,
)
from benchmarks.materials_agent_bench import build_materials_agent_cases
from benchmarks.run_rag_recall import run_rag_recall
from tests.support import MINI_PNG_DATA_URL, ScriptedLLMClient, build_request


def _patch_api_llm_clients(stack: ExitStack) -> ScriptedLLMClient:
    scripted_llm = ScriptedLLMClient()
    stack.enter_context(patch.object(api_module.supervisor_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.recognition_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.chat_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.phase_diagram_runtime.codegen_service, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.phase_diagram_runtime.phase_agent_service, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.lammps_runtime, "llm_client", scripted_llm))
    return scripted_llm


def _patch_deterministic_lammps_runtime(stack: ExitStack, *, real_lammps: bool = False) -> None:
    """Keep default benchmark runs deterministic unless real LAMMPS is explicitly requested."""

    if real_lammps:
        return

    def contract_config() -> LammpsConfig:
        return LammpsConfig(
            allow_mock_fallback=True,
            force_mock=True,
            lammps_command="",
            potentials_dir="",
            max_retries=1,
            lammps_preflight_dag_enabled=False,
        )

    stack.enter_context(patch.object(api_module.lammps_runtime, "config_loader", contract_config))


def _patch_deterministic_rag_backends(stack: ExitStack, *, live_backends: bool = False) -> None:
    """Keep default benchmark runs offline even when local .env contains API keys."""

    if live_backends:
        return

    stack.enter_context(patch.object(api_module.settings, "thermo_rag_embedding_backend", "local_hash"))
    stack.enter_context(patch.object(api_module.settings, "materials_rag_embedding_backend", "local_hash"))
    stack.enter_context(patch.object(api_module.settings, "rag_reranker_enabled", False))


def _build_run_response(*, run_id: str, route_name: str, compute_domain: str, final_message: str) -> AgentRunResponse:
    return AgentRunResponse(
        success=True,
        run_id=run_id,
        conversation_id="benchmark-mcp",
        route=TaskRoute(
            name=route_name,
            reason="benchmark mcp tool invocation",
            selected_tool=route_name,
            intent=route_name,
            decision_source="benchmark_mcp",
            decision_confidence=1.0,
            compute_domain=compute_domain,
        ),
        final_message=final_message,
        html_content=None,
        html_path=None,
        artifacts=[ArtifactRef(kind="json", name="summary.json", path=f"/tmp/{run_id}/summary.json")],
        termination_reason="completed",
        metadata={"source": "benchmark"},
    )


class _FakeRuntime:
    def __init__(self, response: AgentRunResponse) -> None:
        self.response = response

    def run(self, **kwargs):  # noqa: ANN003
        return self.response

    def run_structured(self, **kwargs):  # noqa: ANN003
        return self.response


def _build_mcp_server() -> MaterialsMcpServer:
    tmp_dir = tempfile.mkdtemp(prefix="materials-agent-bench-mcp-")
    deps = SimpleNamespace(
        artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
        phase_diagram_runtime=_FakeRuntime(
            _build_run_response(
                run_id="phase-run-1",
                route_name="phase_diagram.generate",
                compute_domain="phase_diagram",
                final_message="phase diagram completed",
            )
        ),
        lammps_runtime=_FakeRuntime(
            _build_run_response(
                run_id="lammps-run-1",
                route_name="lammps.generate",
                compute_domain="lammps",
                final_message="lammps completed",
            )
        ),
    )
    return MaterialsMcpServer(dependencies=deps)


def _result_row(case_id: str, passed: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"case_id": case_id, "passed": passed, "details": details or {}}


def _pass_rate(result: dict[str, Any]) -> float:
    cases = int(result.get("cases") or 0)
    if cases <= 0:
        return 0.0
    return round(float(result.get("passed") or 0) / cases, 4)


def _detail_rate(rows: list[dict[str, Any]], key: str) -> float:
    applicable = 0
    hits = 0
    for row in rows:
        details = row.get("details", {}) if isinstance(row.get("details"), dict) else {}
        if key not in details:
            continue
        applicable += 1
        if details.get(key) is True:
            hits += 1
    return round(hits / applicable, 4) if applicable else 1.0


def _suite_metric_summary(suite: str, result: dict[str, Any], *, elapsed_seconds: float) -> dict[str, Any]:
    cases = int(result.get("cases") or 0)
    pass_rate = _pass_rate(result)
    metrics: dict[str, Any] = {
        "success_rate": pass_rate,
        "pass_rate": pass_rate,
        "avg_case_duration_seconds": round(elapsed_seconds / cases, 4) if cases else None,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    if suite == "routing":
        route_matches = 0
        domain_matches = 0
        for row in rows:
            details = row.get("details", {})
            route = details.get("route", {}) if isinstance(details, dict) else {}
            expected = details.get("expected", {}) if isinstance(details, dict) else {}
            if route.get("name") == expected.get("route_name"):
                route_matches += 1
            if route.get("compute_domain") == expected.get("compute_domain"):
                domain_matches += 1
        metrics["route_accuracy"] = round(route_matches / cases, 4) if cases else 0.0
        metrics["compute_domain_accuracy"] = round(domain_matches / cases, 4) if cases else 0.0
    elif suite == "phase_execution":
        accuracy_hits = 0
        for row in rows:
            details = row.get("details", {})
            accuracy = details.get("accuracy", {}) if isinstance(details, dict) else {}
            if isinstance(accuracy, dict) and accuracy.get("passed") is True:
                accuracy_hits += 1
        metrics["accuracy_gate_pass_rate"] = round(accuracy_hits / cases, 4) if cases else 0.0
    elif suite == "lammps_contract":
        completeness_values: list[float] = []
        for row in rows:
            details = row.get("details", {})
            required = set(details.get("required_artifacts", [])) if isinstance(details, dict) else set()
            artifacts = set(details.get("artifact_names", [])) if isinstance(details, dict) else set()
            if required:
                completeness_values.append(len(required & artifacts) / len(required))
        metrics["artifact_completeness"] = round(sum(completeness_values) / len(completeness_values), 4) if completeness_values else pass_rate
    elif suite == "lammps_e2e":
        chain_hits = 0
        clarification_cases = 0
        clarification_hits = 0
        rag_applicable = 0
        rag_hits = 0
        request_scores: list[float] = []
        for row in rows:
            details = row.get("details", {})
            expected = details.get("expected", {}) if isinstance(details, dict) else {}
            if details.get("chain_complete"):
                chain_hits += 1
            if expected.get("intent") == "clarify_lammps_request":
                clarification_cases += 1
                if row.get("passed"):
                    clarification_hits += 1
            else:
                rag_applicable += 1
                if details.get("materials_rag_used"):
                    rag_hits += 1
            if isinstance(details.get("request_field_score"), (int, float)):
                request_scores.append(float(details["request_field_score"]))
        metrics["chain_completion_rate"] = round(chain_hits / cases, 4) if cases else 0.0
        metrics["clarification_accuracy"] = round(clarification_hits / clarification_cases, 4) if clarification_cases else 1.0
        metrics["rag_preflight_rate"] = round(rag_hits / rag_applicable, 4) if rag_applicable else 1.0
        metrics["request_field_accuracy"] = round(sum(request_scores) / len(request_scores), 4) if request_scores else None
    elif suite == "lammps_quality":
        fatal_cases = 0
        fatal_hits = 0
        valid_cases = 0
        valid_hits = 0
        real_synthetic_cases = 0
        real_synthetic_hits = 0
        for row in rows:
            details = row.get("details", {})
            expected = details.get("expected", {}) if isinstance(details, dict) else {}
            report = details.get("quality_report", {}) if isinstance(details, dict) else {}
            if expected.get("fatal_anomaly"):
                fatal_cases += 1
                if report.get("passed") is False:
                    fatal_hits += 1
            if expected.get("valid_run"):
                valid_cases += 1
                if report.get("passed") is True and report.get("scientific_result_passed") is True:
                    valid_hits += 1
            if expected.get("real_synthetic_guard"):
                real_synthetic_cases += 1
                if report.get("synthetic_thermo") is True and report.get("scientific_result_passed") is False and report.get("passed") is False:
                    real_synthetic_hits += 1
        metrics["fatal_anomaly_recall"] = round(fatal_hits / fatal_cases, 4) if fatal_cases else 1.0
        metrics["valid_run_pass_rate"] = round(valid_hits / valid_cases, 4) if valid_cases else 1.0
        metrics["real_synthetic_guard_rate"] = round(real_synthetic_hits / real_synthetic_cases, 4) if real_synthetic_cases else 1.0
    elif suite == "lammps_red_blue":
        fatal_cases = fatal_hits = 0
        valid_cases = valid_hits = 0
        locked_cases = locked_hits = 0
        verified_cases = verified_hits = 0
        evidence_cases = evidence_hits = 0
        rag_evidence_cases = rag_evidence_hits = 0
        consistency_cases = consistency_hits = 0
        bounded_cases = bounded_hits = 0
        unverified_patch_execution = 0
        for row in rows:
            details = row.get("details", {})
            expected = details.get("expected", {}) if isinstance(details, dict) else {}
            if expected.get("fatal_finding"):
                fatal_cases += 1
                if details.get("fatal_finding_detected"):
                    fatal_hits += 1
            if expected.get("valid_run"):
                valid_cases += 1
                red_review = details.get("red_review", {}) if isinstance(details, dict) else {}
                if red_review.get("passed") is True and not red_review.get("blocking_findings"):
                    valid_hits += 1
            if expected.get("locked_field_protected"):
                locked_cases += 1
                policy_report = details.get("policy_report", {}) if isinstance(details, dict) else {}
                if policy_report.get("accepted") is False and policy_report.get("requires_user_confirmation") is True:
                    locked_hits += 1
            if expected.get("patch_verified"):
                verified_cases += 1
                policy_report = details.get("policy_report", {}) if isinstance(details, dict) else {}
                steps = set(policy_report.get("verification_steps", [])) if isinstance(policy_report, dict) else set()
                if policy_report.get("accepted") is True and {"lammps_validation", "red_review_required_on_retry"} <= steps:
                    verified_hits += 1
                elif policy_report.get("accepted") is True:
                    unverified_patch_execution += 1
            if expected.get("requires_primary_evidence"):
                evidence_cases += 1
                if details.get("primary_evidence_ok"):
                    evidence_hits += 1
            if expected.get("rag_evidence_traceable"):
                rag_evidence_cases += 1
                if details.get("rag_evidence_traceable"):
                    rag_evidence_hits += 1
            if expected.get("consistency_blocked"):
                consistency_cases += 1
                if details.get("consistency_blocked"):
                    consistency_hits += 1
            if expected.get("bounded_loop"):
                bounded_cases += 1
                convergence = details.get("convergence_report", {}) if isinstance(details, dict) else {}
                if convergence.get("allow_repair") is False and convergence.get("termination_reason") == expected.get("termination_reason"):
                    bounded_hits += 1
        metrics["fatal_finding_recall"] = round(fatal_hits / fatal_cases, 4) if fatal_cases else 1.0
        metrics["valid_run_non_block_rate"] = round(valid_hits / valid_cases, 4) if valid_cases else 1.0
        metrics["locked_field_protection_rate"] = round(locked_hits / locked_cases, 4) if locked_cases else 1.0
        metrics["patch_verification_rate"] = round(verified_hits / verified_cases, 4) if verified_cases else 1.0
        metrics["evidence_traceability_rate"] = round(evidence_hits / evidence_cases, 4) if evidence_cases else 1.0
        metrics["rag_evidence_traceability_rate"] = round(rag_evidence_hits / rag_evidence_cases, 4) if rag_evidence_cases else 1.0
        metrics["request_script_consistency_block_rate"] = round(consistency_hits / consistency_cases, 4) if consistency_cases else 1.0
        metrics["bounded_loop_rate"] = round(bounded_hits / bounded_cases, 4) if bounded_cases else 1.0
        metrics["unverified_patch_execution_count"] = unverified_patch_execution
    elif suite == "review_json_fallback":
        recoverable_cases = recovered = 0
        invalid_cases = invalid_hits = 0
        for row in rows:
            details = row.get("details", {})
            expected = details.get("expected", {}) if isinstance(details, dict) else {}
            parsed = details.get("parsed", {}) if isinstance(details, dict) else {}
            if expected.get("recoverable"):
                recoverable_cases += 1
                if parsed.get("success") is True:
                    recovered += 1
            if expected.get("invalid_patch"):
                invalid_cases += 1
                if parsed.get("success") is False and parsed.get("parse_mode") == "rejected":
                    invalid_hits += 1
        metrics["protocol_recovery_rate"] = round(recovered / recoverable_cases, 4) if recoverable_cases else 1.0
        metrics["invalid_patch_rejection_rate"] = round(invalid_hits / invalid_cases, 4) if invalid_cases else 1.0
    elif suite == "orchestration":
        speedups = [
            float(row.get("details", {}).get("speedup"))
            for row in rows
            if isinstance(row.get("details"), dict) and isinstance(row.get("details", {}).get("speedup"), (int, float))
        ]
        metrics["dependency_correctness_rate"] = _detail_rate(rows, "dependency_correctness_ok")
        metrics["no_concurrency_violation_rate"] = _detail_rate(rows, "concurrency_ok")
        metrics["injected_delay_speedup"] = round(max(speedups), 4) if speedups else 0.0
        metrics["degradation_decision_accuracy"] = _detail_rate(rows, "degradation_ok")
        metrics["partial_report_safety_rate"] = _detail_rate(rows, "partial_report_safety_ok")
    elif suite == "judge_calibration":
        agreements = [
            float(row.get("details", {}).get("within_one_agreement"))
            for row in rows
            if isinstance(row.get("details"), dict) and isinstance(row.get("details", {}).get("within_one_agreement"), (int, float))
        ]
        metrics["exact_agreement_rate"] = round(
            sum(float(row.get("details", {}).get("agreement") or 0.0) for row in rows if isinstance(row.get("details"), dict)) / len(rows),
            4,
        ) if rows else 0.0
        metrics["within_one_agreement_rate"] = round(sum(agreements) / len(agreements), 4) if agreements else 0.0
        metrics["parse_recovery_rate"] = _detail_rate(rows, "parse_recovered_ok")
        metrics["hard_gate_non_override_rate"] = _detail_rate(rows, "hard_gate_non_override_ok")
        metrics["blind_input_safety_rate"] = _detail_rate(rows, "blind_input_safety_ok")
        drift = result.get("drift_report") if isinstance(result.get("drift_report"), dict) else {}
        matrix = result.get("backend_matrix") if isinstance(result.get("backend_matrix"), dict) else {}
        metrics["mean_absolute_error"] = drift.get("mean_absolute_error", 5.0)
        metrics["drift_free_rate"] = 0.0 if drift.get("drift_detected", True) else 1.0
        backends = matrix.get("backends", []) if isinstance(matrix, dict) else []
        quick_ci_backend = str(matrix.get("quick_ci_backend") or "")
        quick_ci_available = any(
            isinstance(backend, dict)
            and backend.get("backend") == quick_ci_backend
            and backend.get("configured") is True
            and backend.get("allowed_in_quick_ci") is True
            for backend in backends
        )
        metrics["quick_ci_backend_available_rate"] = 1.0 if quick_ci_available else 0.0
        serialized_matrix = json.dumps(matrix, ensure_ascii=False).lower()
        metrics["backend_matrix_secret_safety_rate"] = 0.0 if any(marker in serialized_matrix for marker in ("sk-", "or-", "bearer ")) else 1.0
    elif suite == "lammps_recovery":
        timeout_cases = timeout_hits = 0
        replan_cases = replan_hits = 0
        crash_cases = crash_hits = 0
        cancel_cases = cancel_hits = 0
        for row in rows:
            details = row.get("details", {})
            scenario = str(details.get("scenario") or "")
            if scenario == "global_timeout_partial_report":
                timeout_cases += 1
                if row.get("passed"):
                    timeout_hits += 1
            elif scenario == "preflight_level2_replan_reuse":
                replan_cases += 1
                if row.get("passed"):
                    replan_hits += 1
            elif scenario == "worker_crash_failed_event":
                crash_cases += 1
                if row.get("passed"):
                    crash_hits += 1
            elif scenario == "running_cancel_not_overwritten":
                cancel_cases += 1
                if row.get("passed"):
                    cancel_hits += 1
        metrics["timeout_partial_report_rate"] = round(timeout_hits / timeout_cases, 4) if timeout_cases else 1.0
        metrics["replan_checkpoint_reuse_rate"] = round(replan_hits / replan_cases, 4) if replan_cases else 1.0
        metrics["worker_crash_guard_rate"] = round(crash_hits / crash_cases, 4) if crash_cases else 1.0
        metrics["running_cancel_guard_rate"] = round(cancel_hits / cancel_cases, 4) if cancel_cases else 1.0
        metrics["checkpoint_resume_correctness"] = pass_rate
    elif suite == "memory":
        metrics["followup_grounding_rate"] = pass_rate
    elif suite == "memory_retrieval":
        metrics["memory_retrieval_relevance"] = pass_rate
    elif suite == "shared_memory":
        metrics["duplicate_recall"] = _detail_rate(rows, "duplicate_ok")
        metrics["scope_isolation_rate"] = _detail_rate(rows, "scope_isolation_ok")
        metrics["locked_retention_rate"] = _detail_rate(rows, "locked_retention_ok")
        metrics["evidence_traceability_rate"] = _detail_rate(rows, "evidence_traceability_ok")
    elif suite == "memory_conflict":
        metrics["conflict_recall"] = _detail_rate(rows, "conflict_ok")
        metrics["needs_user_rate"] = _detail_rate(rows, "needs_user_ok")
        metrics["quarantine_rate"] = _detail_rate(rows, "quarantine_ok")
        metrics["semantic_candidate_rate"] = _detail_rate(rows, "semantic_candidate_ok")
        metrics["no_incorrect_auto_resolution_rate"] = _detail_rate(rows, "no_incorrect_auto_resolution_ok")
    elif suite == "context_compression":
        metrics["l2_traceability_rate"] = _detail_rate(rows, "l2_traceability_ok")
        metrics["noncompressible_protection_rate"] = _detail_rate(rows, "noncompressible_protection_ok")
    elif suite == "materials_multihop":
        metrics["required_hop_completion"] = _detail_rate(rows, "required_hops_ok")
        metrics["evidence_chain_completeness"] = _detail_rate(rows, "evidence_chain_ok")
        metrics["no_unsupported_bridge_claim_rate"] = _detail_rate(rows, "bridge_claims_ok")
        metrics["final_conclusion_correctness"] = _detail_rate(rows, "final_conclusion_ok")
        metrics["citation_order_authority_rate"] = _detail_rate(rows, "citation_order_authority_ok")
        metrics["missing_hop_honesty_rate"] = _detail_rate(rows, "missing_hop_honesty_ok")
    elif suite == "mcp":
        metrics["tool_contract_pass_rate"] = pass_rate
    elif suite == "recognition":
        metrics["recognition_contract_pass_rate"] = pass_rate
    return metrics


def _rag_metric_summary(result: dict[str, Any]) -> dict[str, Any]:
    materials = result["materials_rag"]["summary"]
    thermo = result["thermo_rag"]["summary"]
    return {
        "materials_hit@1": materials.get("hit@1", 0.0),
        "materials_hit@3": materials.get("hit@3", 0.0),
        "materials_hit@5": materials.get("hit@5", 0.0),
        "materials_mrr": materials.get("mrr", 0.0),
        "thermo_hit@1": thermo.get("hit@1", 0.0),
        "thermo_hit@3": thermo.get("hit@3", 0.0),
        "thermo_hit@5": thermo.get("hit@5", 0.0),
        "thermo_mrr": thermo.get("mrr", 0.0),
        "avg_case_duration_seconds": round(
            float(result.get("elapsed_seconds") or 0.0)
            / max(1, int(materials.get("total_cases") or 0) + int(thermo.get("total_cases") or 0)),
            4,
        ),
        "elapsed_seconds": result.get("elapsed_seconds", 0.0),
    }


def _threshold_results(suite_metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for metric_name, threshold in BENCHMARK_THRESHOLDS.items():
        suite, _, key = metric_name.partition(".")
        if suite not in suite_metrics:
            continue
        value = suite_metrics[suite].get(key)
        passed = isinstance(value, (int, float)) and float(value) >= threshold
        checks.append({"metric": metric_name, "value": value, "threshold": threshold, "passed": passed})
    return checks


def run_routing_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        for case in selected:
            request = build_request(case["prompt"], conversation_id=f"bench-routing-{uuid.uuid4().hex[:8]}")
            uploaded_assets: list[UploadedAsset] = []
            if case.get("upload_image"):
                uploaded_assets = [
                    UploadedAsset(
                        asset_id="bench-img",
                        name="diagram.png",
                        media_type="image/png",
                        data_url=MINI_PNG_DATA_URL,
                        size_bytes=128,
                    )
                ]
                request.uploaded_assets = uploaded_assets
            state: AgentGraphState = {
                "request": request,
                "messages": [],
                "uploaded_assets": uploaded_assets,
                "current_context_summary": "",
                "last_run_context": LastRunContext(),
            }
            decision = api_module.supervisor_agent.decide(state)
            route = api_module.supervisor_agent.build_route(decision)
            passed = route.name == case["expected"]["route_name"] and route.compute_domain == case["expected"]["compute_domain"]
            results.append(_result_row(case["case_id"], passed, {"decision": decision, "route": route.model_dump(mode="json"), "expected": case["expected"]}))
    return {"suite": "routing", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_phase_execution_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        with TestClient(api_module.app) as client:
            for case in selected:
                overrides = case["request_overrides"]
                request = build_request(
                    case["prompt"],
                    conversation_id=f"bench-phase-{uuid.uuid4().hex[:8]}",
                    system_name=overrides["system_name"],
                    temperature_min=overrides["temperature_min"],
                    temperature_max=overrides["temperature_max"],
                )
                request.pressure = overrides["pressure"]
                request.step_size = overrides["step_size"]
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                payload = response.json()
                metadata = payload.get("metadata", {})
                passed = (
                    response.status_code == 200
                    and payload.get("success") is True
                    and payload["route"]["name"] == case["expected"]["route_name"]
                    and metadata.get("thermo_lookup", {}).get("database_name") == case["expected"]["database_name"]
                    and metadata.get("accuracy", {}).get("passed") is True
                )
                results.append(
                    _result_row(
                        case["case_id"],
                        passed,
                        {
                            "status_code": response.status_code,
                            "run_id": payload.get("run_id"),
                            "route": payload.get("route", {}),
                            "database_name": metadata.get("thermo_lookup", {}).get("database_name"),
                            "accuracy": metadata.get("accuracy", {}),
                        },
                    )
                )
    return {"suite": "phase_execution", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_lammps_contract_benchmark(
    cases: list[dict[str, Any]],
    *,
    limit: int | None = None,
    real_lammps: bool = False,
) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        _patch_deterministic_lammps_runtime(stack, real_lammps=real_lammps)
        _patch_deterministic_rag_backends(stack)
        with TestClient(api_module.app) as client:
            for case in selected:
                case_started = time.perf_counter()
                request = build_request(case["prompt"], conversation_id=f"bench-lammps-{uuid.uuid4().hex[:8]}")
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                payload = response.json()
                artifact_names = {item["name"] for item in payload.get("artifacts", [])}
                plan_steps = [item["tool_name"] for item in payload.get("plan_steps", [])]
                required_artifacts = set(case["expected"]["required_artifacts"])
                required_plan_steps = set(case["expected"]["plan_steps"])
                metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
                summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
                passed = (
                    response.status_code == 200
                    and payload["route"]["name"] == case["expected"]["route_name"]
                    and payload["route"]["compute_domain"] == case["expected"]["compute_domain"]
                    and required_artifacts.issubset(artifact_names)
                    and required_plan_steps.issubset(plan_steps)
                )
                results.append(
                    _result_row(
                        case["case_id"],
                        passed,
                        {
                            "status_code": response.status_code,
                            "run_id": payload.get("run_id"),
                            "run_status": payload.get("run_status"),
                            "success": payload.get("success"),
                            "termination_reason": payload.get("termination_reason"),
                            "elapsed_seconds": round(time.perf_counter() - case_started, 3),
                            "route": payload.get("route", {}),
                            "run_mode": metadata.get("run_mode"),
                            "runtime_profile": metadata.get("runtime_profile") or summary.get("runtime_profile") or {},
                            "result_profile": summary.get("result_profile") or {},
                            "artifact_count": len(artifact_names),
                            "artifact_names": sorted(artifact_names),
                            "required_artifacts": case["expected"]["required_artifacts"],
                            "missing_required_artifacts": sorted(required_artifacts - artifact_names),
                            "plan_steps": plan_steps,
                            "required_plan_steps": case["expected"]["plan_steps"],
                            "missing_plan_steps": sorted(required_plan_steps - set(plan_steps)),
                        },
                    )
                )
    return {"suite": "lammps_contract", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def _find_lammps_request(payload: dict[str, Any]) -> dict[str, Any]:
    for item in payload.get("trace", []):
        if item.get("tool_name") == "lammps_request_interpreter":
            output = item.get("output") or {}
            request = output.get("request")
            if isinstance(request, dict):
                return request
    return {}


def _materials_rag_observed(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    materials_rag = metadata.get("materials_rag", {}) if isinstance(metadata.get("materials_rag"), dict) else {}
    if materials_rag.get("used") is True and int(materials_rag.get("hit_count") or 0) > 0:
        return True

    planning = materials_rag.get("planning") if isinstance(materials_rag.get("planning"), dict) else {}
    if planning.get("hits"):
        return True

    for item in payload.get("trace", []):
        if not isinstance(item, dict) or item.get("tool_name") != "materials_rag_search":
            continue
        output = item.get("output") if isinstance(item.get("output"), dict) else {}
        if item.get("success") is True and output.get("hits"):
            return True
    return False


def _request_field_score(actual: dict[str, Any], expected: dict[str, Any]) -> float:
    if not expected:
        return 1.0
    hits = 0
    for key, expected_value in expected.items():
        if actual.get(key) == expected_value:
            hits += 1
    return round(hits / len(expected), 4)


def run_lammps_e2e_benchmark(
    cases: list[dict[str, Any]],
    *,
    limit: int | None = None,
    real_lammps: bool = False,
) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        _patch_deterministic_lammps_runtime(stack, real_lammps=real_lammps)
        _patch_deterministic_rag_backends(stack)
        with TestClient(api_module.app) as client:
            for case in selected:
                expected = case["expected"]
                request = build_request(case["prompt"], conversation_id=f"bench-lammps-e2e-{uuid.uuid4().hex[:8]}")
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                payload = response.json()
                artifact_names = {item["name"] for item in payload.get("artifacts", [])}
                plan_steps = [item["tool_name"] for item in payload.get("plan_steps", [])]
                trace_tools = [item["tool_name"] for item in payload.get("trace", [])]
                route = payload.get("route", {})
                metadata = payload.get("metadata", {})
                summary = payload.get("summary", {})
                request_payload = _find_lammps_request(payload)
                request_field_score = _request_field_score(request_payload, expected.get("request", {}))
                required_steps = expected.get("required_steps", [])
                forbidden_steps = expected.get("forbidden_steps", [])
                required_artifacts = expected.get("required_artifacts", [])
                materials_rag_used = _materials_rag_observed(payload)
                review = metadata.get("review", {}) if isinstance(metadata, dict) else {}
                chain_complete = (
                    response.status_code == 200
                    and route.get("name") == expected.get("route_name")
                    and route.get("compute_domain") == expected.get("compute_domain")
                    and all(step in trace_tools for step in required_steps)
                    and all(step not in trace_tools for step in forbidden_steps)
                    and all(name in artifact_names for name in required_artifacts)
                    and request_field_score == 1.0
                    and (not expected.get("requires_materials_rag") or materials_rag_used)
                    and (not expected.get("requires_review") or review.get("passed") is True)
                )
                final_message = str(payload.get("final_message") or "")
                clarification_ok = True
                if expected.get("intent") == "clarify_lammps_request":
                    clarification_ok = (
                        route.get("intent") == "clarify_lammps_request"
                        and all(term in final_message for term in expected.get("required_final_terms", []))
                        and all(step not in trace_tools for step in forbidden_steps)
                    )
                passed = chain_complete and clarification_ok
                results.append(
                    _result_row(
                        case["case_id"],
                        passed,
                        {
                            "status_code": response.status_code,
                            "route": route,
                            "expected": expected,
                            "artifact_names": sorted(artifact_names),
                            "plan_steps": plan_steps,
                            "trace_tools": trace_tools,
                            "request": request_payload,
                            "request_field_score": request_field_score,
                            "materials_rag_used": materials_rag_used,
                            "review": review,
                            "run_mode": metadata.get("run_mode") if isinstance(metadata, dict) else "",
                            "result_profile": summary.get("result_profile") if isinstance(summary, dict) else {},
                            "chain_complete": chain_complete,
                            "final_message": final_message[:500],
                        },
                    )
                )
    return {"suite": "lammps_e2e", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def _write_lammps_quality_fixture(output_dir: Path, fixture: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(fixture.get("thermo_rows", []))
    with (output_dir / "thermo.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["step", "temp", "pe", "ke", "etotal", "press"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    (output_dir / "run.log").write_text(str(fixture.get("run_log") or ""), encoding="utf-8")
    dump_atom_count = fixture.get("dump_atom_count")
    if dump_atom_count is not None:
        (output_dir / str((fixture.get("request") or {}).get("dump_file") or "dump.atom")).write_text(
            "\n".join(
                [
                    "ITEM: TIMESTEP",
                    "0",
                    "ITEM: NUMBER OF ATOMS",
                    str(dump_atom_count),
                    "ITEM: BOX BOUNDS pp pp pp",
                    "0 10",
                    "0 10",
                    "0 10",
                    "ITEM: ATOMS id type x y z",
                    "1 1 1 1 1",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    if fixture.get("synthetic_thermo"):
        (output_dir / "thermo_metadata.json").write_text(
            json.dumps({"synthetic_thermo": True, "source": "benchmark_fixture"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def run_lammps_quality_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        fixture = case["fixture"]
        expected = case["expected"]
        with tempfile.TemporaryDirectory(prefix="lammps-quality-bench-") as tmp_dir:
            output_dir = Path(tmp_dir)
            _write_lammps_quality_fixture(output_dir, fixture)
            metrics = {"synthetic_thermo": bool(fixture.get("synthetic_thermo"))}
            report = build_physical_quality_report(
                output_dir=output_dir,
                request=fixture["request"],
                run_mode=fixture["run_mode"],
                metrics=metrics,
                execution_error=str(fixture.get("execution_error") or ""),
            )
        issues_text = " ".join([*report.issues, *report.warnings, *report.log_errors]).lower()
        required_terms_ok = all(str(term).lower() in issues_text for term in expected.get("required_issue_terms", []))
        passed = (
            report.passed is expected["passed"]
            and report.scientific_result_passed is expected["scientific_result_passed"]
            and (("synthetic_thermo" not in expected) or report.synthetic_thermo is expected["synthetic_thermo"])
            and required_terms_ok
        )
        results.append(
            _result_row(
                case["case_id"],
                passed,
                {
                    "fixture": {
                        "run_mode": fixture.get("run_mode"),
                        "request": fixture.get("request"),
                        "synthetic_thermo": bool(fixture.get("synthetic_thermo")),
                    },
                    "expected": expected,
                    "quality_report": report.model_dump(mode="json"),
                    "required_terms_ok": required_terms_ok,
                },
            )
        )
    return {"suite": "lammps_quality", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def _benchmark_lammps_request(payload: dict[str, Any] | None = None) -> LammpsRequest:
    base = {
        "material": "Cu",
        "potential_family": "eam",
        "task_type": "heating",
        "temperature": 800,
        "steps": 1000,
        "ensemble": "NVT",
        "box_size": 4,
        "initial_temp": 300,
        "time_step": 0.001,
        "dump_file": "dump.atom",
        "notes": "benchmark red-blue request",
    }
    if payload:
        base.update(payload)
    return LammpsRequest.model_validate(base)


def _benchmark_artifact_refs(names: list[str]) -> list[ArtifactRef]:
    return [
        ArtifactRef(kind="json" if name.endswith(".json") else "text", name=name, path=f"/tmp/benchmark/{name}")
        for name in names
    ]


def _red_blue_expected_ok(details: dict[str, Any], expected: dict[str, Any]) -> bool:
    if "passed" in expected and details.get("red_review", {}).get("passed") is not expected["passed"]:
        return False
    if "fatal_finding" in expected and bool(details.get("fatal_finding_detected")) is not bool(expected["fatal_finding"]):
        return False
    if expected.get("requires_primary_evidence") and not details.get("primary_evidence_ok"):
        return False
    if expected.get("rag_evidence_traceable") and not details.get("rag_evidence_traceable"):
        return False
    if expected.get("consistency_blocked") and not details.get("consistency_blocked"):
        return False
    policy_report = details.get("policy_report", {})
    if "policy_accepted" in expected and policy_report.get("accepted") is not expected["policy_accepted"]:
        return False
    if expected.get("locked_field_protected") and not (
        policy_report.get("accepted") is False and policy_report.get("requires_user_confirmation") is True
    ):
        return False
    if expected.get("patch_verified"):
        steps = set(policy_report.get("verification_steps", []))
        if policy_report.get("accepted") is not True or not {"lammps_validation", "red_review_required_on_retry"} <= steps:
            return False
    if "request_changed" in expected and policy_report.get("request_changed") is not expected["request_changed"]:
        return False
    if "termination_reason" in expected:
        report = details.get("convergence_report") or policy_report
        if report.get("termination_reason") != expected["termination_reason"]:
            return False
    if "allow_repair" in expected and details.get("convergence_report", {}).get("allow_repair") is not expected["allow_repair"]:
        return False
    return True


def run_lammps_red_blue_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        scenario = case["scenario"]
        fixture = case["fixture"]
        expected = case["expected"]
        details: dict[str, Any] = {"scenario": scenario, "expected": expected}
        if scenario == "red_review":
            report = build_deterministic_review_report(
                request=_benchmark_lammps_request(fixture.get("request")),
                mode=fixture["run_mode"],
                artifacts=_benchmark_artifact_refs(list(fixture.get("artifacts", []))),
                metrics=dict(fixture.get("metrics", {})),
                validation=dict(fixture.get("validation", {})),
                error=str(fixture.get("error") or ""),
                input_script=str(fixture.get("input_script") or ""),
                quality_report=dict(fixture.get("quality_report", {})),
                materials_rag_context=dict(fixture.get("materials_rag_context", {})),
            )
            evidence_refs = [ref.model_dump(mode="json") for ref in report.evidence_refs]
            blocking_findings = [finding.model_dump(mode="json") for finding in report.blocking_findings()]
            warning_findings = [finding.model_dump(mode="json") for finding in report.warning_findings()]
            details.update(
                {
                    "red_review": {
                        "passed": report.passed,
                        "blocking_findings": blocking_findings,
                        "warning_findings": warning_findings,
                        "score": report.score.model_dump(mode="json"),
                    },
                    "evidence_refs": evidence_refs,
                    "fatal_finding_detected": bool(report.blocking_findings()),
                    "primary_evidence_ok": blocking_findings_have_primary_evidence(report.findings, report.evidence_refs),
                    "rag_evidence_traceable": any(ref.get("source_type") == "rag" and ref.get("authority") == "secondary" for ref in evidence_refs),
                    "consistency_blocked": any(
                        finding.get("dimension") == "consistency" and finding.get("severity") == "blocking"
                        for finding in blocking_findings
                    ),
                }
            )
        elif scenario == "blue_policy_payload":
            request = _benchmark_lammps_request(fixture.get("request"))
            payload = dict(fixture.get("payload", {}))
            resolution = build_patch_from_llm_payload(
                request,
                raw_text=json.dumps(payload, ensure_ascii=False),
                parsed_payload=payload,
                stage=str(fixture.get("stage") or "review"),
                issues=[str(item) for item in fixture.get("issues", [])],
            )
            repaired, policy_report = verify_and_apply_patch(request, resolution.patch)
            details.update(
                {
                    "blue_parse_audit": resolution.parse_audit.model_dump(mode="json"),
                    "patch": resolution.patch.model_dump(mode="json"),
                    "policy_report": policy_report.model_dump(mode="json"),
                    "repaired_request": repaired.model_dump(mode="json") if repaired else None,
                }
            )
        elif scenario == "blue_policy_patch":
            request = _benchmark_lammps_request(fixture.get("request"))
            patch = RepairPatch.model_validate(fixture["patch"])
            repaired, policy_report = verify_and_apply_patch(request, patch)
            details.update(
                {
                    "patch": patch.model_dump(mode="json"),
                    "policy_report": policy_report.model_dump(mode="json"),
                    "repaired_request": repaired.model_dump(mode="json") if repaired else None,
                }
            )
        elif scenario == "repair_convergence":
            before = _benchmark_lammps_request(fixture.get("before_request"))
            current = _benchmark_lammps_request(fixture.get("current_request"))
            candidate = _benchmark_lammps_request(fixture["candidate_request"]) if fixture.get("candidate_request") else None
            history = [
                {
                    "raw_payload": current.model_dump(mode="json"),
                    "policy_report": {
                        "accepted": True,
                        "before_request": before.model_dump(mode="json"),
                        "after_request": current.model_dump(mode="json"),
                    },
                    "convergence_report": {
                        "allow_repair": True,
                        "score_before_repair": fixture.get("history_score"),
                    },
                }
            ]
            convergence = evaluate_repair_convergence(
                repair_history=history,
                current_request=current,
                stage=str(fixture.get("stage") or "review"),
                repair_budget=int(fixture.get("repair_budget") or 1),
                current_score=fixture.get("current_score"),
                candidate_request=candidate,
            )
            details["convergence_report"] = convergence.model_dump(mode="json")
        else:
            details["error"] = f"Unknown lammps_red_blue scenario: {scenario}"
        results.append(_result_row(case["case_id"], _red_blue_expected_ok(details, expected), details))
    return {"suite": "lammps_red_blue", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_review_json_fallback_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    schema_map = {"ReviewReport": ReviewReport, "RepairPatch": RepairPatch}
    for case in selected:
        expected = case["expected"]
        deterministic_payload = None
        if expected.get("deterministic_fallback") and case["schema"] == "ReviewReport":
            deterministic_payload = ReviewReport(summary="Deterministic fallback review passed.", findings=[], evidence_refs=[])
        parsed = parse_review_payload(
            case["raw"],
            schema=schema_map[case["schema"]],
            payload_type=case["payload_type"],
            deterministic_payload=deterministic_payload,
        )
        parsed_payload = parsed.model_dump(mode="json")
        normalizations = set(parsed_payload.get("normalizations", []))
        passed = parsed.success is expected["success"]
        if expected.get("parse_mode") and parsed.parse_mode != expected["parse_mode"]:
            passed = False
        for normalization in expected.get("normalizations", []):
            if normalization not in normalizations:
                passed = False
        details = {
            "payload_type": case["payload_type"],
            "schema": case["schema"],
            "expected": expected,
            "parsed": parsed_payload,
        }
        results.append(_result_row(case["case_id"], passed, details))
    return {"suite": "review_json_fallback", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def _orchestration_limits(payload: dict[str, Any] | None = None) -> DAGResourceLimits:
    payload = payload or {}
    return DAGResourceLimits(
        network=int(payload.get("network", 2)),
        cpu=int(payload.get("cpu", 4)),
        simulation=int(payload.get("simulation", 1)),
    )


def _orchestration_context(run_id: str) -> DAGExecutionContext:
    return DAGExecutionContext(
        run_id=run_id,
        input_payload={
            "message": "请用 LAMMPS 做 Cu heating，800K，1000 steps。",
            "conversation_context": [],
            "runtime_config": {"lammps_preflight_dag_enabled": True},
            "uploaded_assets": [],
            "material": "Cu",
        },
        config_signature="benchmark-orchestration-config/v1",
    )


def _orchestration_case_passed(details: dict[str, Any], expected: dict[str, Any]) -> bool:
    if "dependency_correctness_ok" in details and details["dependency_correctness_ok"] is not True:
        return False
    if "concurrency_ok" in details and details["concurrency_ok"] is not True:
        return False
    if "min_speedup" in expected and float(details.get("speedup") or 0.0) < float(expected["min_speedup"]):
        return False
    if expected.get("topological_order") is not None and details.get("topological_order") != expected["topological_order"]:
        return False
    if expected.get("max_active_network") is not None:
        max_active = details.get("max_active") if isinstance(details.get("max_active"), dict) else {}
        if int(max_active.get("network") or 0) > int(expected["max_active_network"]):
            return False
    if "degradation_level" in expected and details.get("degradation_level") != expected["degradation_level"]:
        return False
    if expected.get("fallback_nodes") is not None and details.get("fallback_nodes") != expected["fallback_nodes"]:
        return False
    if expected.get("invalidated_nodes") is not None and details.get("invalidated_nodes") != expected["invalidated_nodes"]:
        return False
    if expected.get("reused_nodes_contains"):
        if not set(expected["reused_nodes_contains"]) <= set(details.get("reused_nodes") or []):
            return False
        if not set(expected["reused_nodes_contains"]) <= set(details.get("reused_from_checkpoint_nodes") or []):
            return False
    if "termination_reason" in expected and details.get("termination_reason") != expected["termination_reason"]:
        return False
    if expected.get("scientific_result_available") is not None and details.get("scientific_result_available") is not expected["scientific_result_available"]:
        return False
    if "partial_report_safety_ok" in details and details["partial_report_safety_ok"] is not True:
        return False
    return True


def _run_orchestration_speedup_case(case: dict[str, Any]) -> dict[str, Any]:
    plan = build_lammps_preflight_plan(timeout_overrides={node_id: 1.0 for node_id in LAMMPS_PREFLIGHT_NODE_IDS})
    limits = _orchestration_limits(case.get("resource_limits"))
    limit_map = limits.as_dict()
    node_delays = {node_id: float(delay) for node_id, delay in dict(case.get("node_delays") or {}).items()}
    default_delay = float(case.get("default_delay_seconds") or 0.05)
    active: dict[str, int] = {"network": 0, "cpu": 0, "simulation": 0}
    max_active: dict[str, int] = {"network": 0, "cpu": 0, "simulation": 0}

    async def handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        resource = node.resource_class
        active[resource] += 1
        max_active[resource] = max(max_active[resource], active[resource])
        try:
            await asyncio.sleep(node_delays.get(node.node_id, default_delay))
            return {"node_id": node.node_id, "run_id": context.run_id}
        finally:
            active[resource] -= 1

    serial_estimate = sum(node_delays.get(node.node_id, default_delay) for node in plan.nodes)
    started = time.perf_counter()
    result = asyncio.run(
        DAGExecutor(limits).run(
            plan,
            _orchestration_context("bench-orchestration-speedup"),
            handlers={node.node_type: handler for node in plan.nodes},
        )
    )
    elapsed = time.perf_counter() - started
    speedup = 1 - (elapsed / serial_estimate) if serial_estimate else 0.0
    return {
        "scenario": case["scenario"],
        "dag_status": result.status,
        "topological_order": result.topological_order,
        "successful_nodes": result.successful_node_ids(),
        "dependency_correctness_ok": result.status == "completed" and result.successful_node_ids() == LAMMPS_PREFLIGHT_NODE_IDS,
        "concurrency_ok": all(max_active[resource] <= int(limit_map[resource]) for resource in max_active),
        "max_active": max_active,
        "resource_limits": {key: int(value) for key, value in limit_map.items()},
        "serial_estimate_seconds": round(serial_estimate, 4),
        "elapsed_seconds": round(elapsed, 4),
        "speedup": round(speedup, 4),
    }


def _run_orchestration_semaphore_case(case: dict[str, Any]) -> dict[str, Any]:
    plan = DAGPlan(
        plan_id="orchestration-semaphore-benchmark/v1",
        nodes=[
            DAGNode(node_id="network_a", node_type="network_probe", resource_class="network"),
            DAGNode(node_id="network_b", node_type="network_probe", resource_class="network"),
            DAGNode(node_id="network_c", node_type="network_probe", resource_class="network"),
            DAGNode(node_id="merge", node_type="merge", dependencies=["network_a", "network_b", "network_c"], resource_class="cpu"),
        ],
    )
    limits = _orchestration_limits(case.get("resource_limits"))
    limit_map = limits.as_dict()
    active_network = 0
    max_active_network = 0

    async def network_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        nonlocal active_network, max_active_network
        active_network += 1
        max_active_network = max(max_active_network, active_network)
        try:
            await asyncio.sleep(float(case.get("node_delay_seconds") or 0.03))
            return {"node_id": node.node_id}
        finally:
            active_network -= 1

    def merge_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        return {"merged": True, "node_id": node.node_id}

    result = asyncio.run(
        DAGExecutor(limits).run(
            plan,
            _orchestration_context("bench-orchestration-semaphore"),
            handlers={"network_probe": network_handler, "merge": merge_handler},
        )
    )
    return {
        "scenario": case["scenario"],
        "dag_status": result.status,
        "successful_nodes": result.successful_node_ids(),
        "dependency_correctness_ok": result.status == "completed" and result.successful_node_ids() == plan.topological_order(),
        "concurrency_ok": max_active_network <= int(limit_map["network"]),
        "max_active": {"network": max_active_network},
        "resource_limits": {key: int(value) for key, value in limit_map.items()},
    }


def _run_orchestration_level1_case(case: dict[str, Any]) -> dict[str, Any]:
    plan = build_lammps_preflight_plan(timeout_overrides={node_id: 1.0 for node_id in LAMMPS_PREFLIGHT_NODE_IDS})

    def handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        if node.node_id == "materials_rag_search":
            raise RuntimeError("benchmark optional RAG failure")
        return {"node_id": node.node_id, "fallback_safe": node.node_id == "materials_rag_search"}

    result = asyncio.run(DAGExecutor().run(plan, _orchestration_context("bench-orchestration-level1"), handlers={node.node_type: handler for node in plan.nodes}))
    decision = decide_degradation(plan, result, budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1))
    fallback_result = apply_level_1_fallbacks(result, decision)
    fallback_nodes = [
        node_id
        for node_id, node_result in fallback_result.results.items()
        if node_result.status == "completed_with_fallback"
    ]
    expected = case["expected"]
    degradation_ok = (
        decision.degradation_level == expected.get("degradation_level")
        and decision.fallback_nodes == expected.get("fallback_nodes")
        and fallback_nodes == expected.get("fallback_nodes")
        and fallback_result.status == "completed"
    )
    return {
        "scenario": case["scenario"],
        "dag_status": result.status,
        "degraded_status": fallback_result.status,
        "degradation_level": decision.degradation_level,
        "fallback_nodes": decision.fallback_nodes,
        "completed_with_fallback_nodes": fallback_nodes,
        "degradation_ok": degradation_ok,
    }


def _run_orchestration_level2_case(case: dict[str, Any]) -> dict[str, Any]:
    plan = build_lammps_preflight_plan(timeout_overrides={node_id: 1.0 for node_id in LAMMPS_PREFLIGHT_NODE_IDS})
    calls: dict[str, int] = {}

    def handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        calls[node.node_id] = calls.get(node.node_id, 0) + 1
        if node.node_id == "red_pre_execution_review" and context.run_id.endswith("first"):
            raise RuntimeError("benchmark transient red review failure")
        return {"node_id": node.node_id, "run_id": context.run_id}

    first = asyncio.run(
        DAGExecutor().run(
            plan,
            _orchestration_context("bench-orchestration-replan-first"),
            handlers={node.node_type: handler for node in plan.nodes},
        )
    )
    decision = decide_degradation(plan, first, budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1))
    reused_from_checkpoint_nodes: list[str] = []
    second_status = ""
    if decision.new_plan is not None:
        second_context = _orchestration_context("bench-orchestration-replan-second")
        second_context.metadata["reuse_node_results"] = {
            node_id: node_result.model_dump(mode="json")
            for node_id, node_result in first.results.items()
            if node_result.status in {"completed", "completed_with_fallback"}
        }
        second = asyncio.run(
            DAGExecutor().run(
                decision.new_plan,
                second_context,
                handlers={node.node_type: handler for node in decision.new_plan.nodes},
            )
        )
        second_status = second.status
        reused_from_checkpoint_nodes = [
            node_id
            for node_id, node_result in second.results.items()
            if node_result.metadata.get("reused_from_checkpoint") is True
        ]
    expected = case["expected"]
    degradation_ok = (
        first.status == "failed"
        and decision.degradation_level == expected.get("degradation_level")
        and decision.can_continue is True
        and decision.invalidated_nodes == expected.get("invalidated_nodes")
        and set(expected.get("reused_nodes_contains") or []) <= set(decision.reused_nodes)
        and set(expected.get("reused_nodes_contains") or []) <= set(reused_from_checkpoint_nodes)
        and second_status == "completed"
    )
    return {
        "scenario": case["scenario"],
        "dag_status": first.status,
        "degradation_level": decision.degradation_level,
        "can_continue": decision.can_continue,
        "invalidated_nodes": decision.invalidated_nodes,
        "reused_nodes": decision.reused_nodes,
        "reused_from_checkpoint_nodes": reused_from_checkpoint_nodes,
        "rerun_status": second_status,
        "handler_calls": calls,
        "degradation_ok": degradation_ok,
    }


def _run_orchestration_level3_case(case: dict[str, Any]) -> dict[str, Any]:
    async def slow_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
        await asyncio.sleep(float(case.get("node_delay_seconds") or 0.05))
        return {"late": True}

    plan = DAGPlan(
        plan_id="orchestration-global-timeout-benchmark/v1",
        global_timeout_seconds=float(case.get("global_timeout_seconds") or 0.01),
        nodes=[
            DAGNode(node_id="slow_preflight", node_type="slow", timeout_seconds=1.0),
            DAGNode(node_id="downstream", node_type="slow", dependencies=["slow_preflight"], timeout_seconds=1.0),
        ],
    )
    result = asyncio.run(DAGExecutor().run(plan, _orchestration_context("bench-orchestration-timeout"), handlers={"slow": slow_handler}))
    decision = decide_degradation(plan, result, budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1), last_checkpoint_id="ckpt-orchestration")
    partial = decision.partial_report.model_dump(mode="json") if decision.partial_report else {}
    partial_report_safety_ok = (
        partial.get("success") is False
        and partial.get("scientific_result_available") is False
        and partial.get("termination_reason") == "global_timeout"
    )
    return {
        "scenario": case["scenario"],
        "dag_status": result.status,
        "degradation_level": decision.degradation_level,
        "termination_reason": decision.termination_reason,
        "scientific_result_available": partial.get("scientific_result_available"),
        "partial_report": partial,
        "degradation_ok": decision.degradation_level == case["expected"].get("degradation_level"),
        "partial_report_safety_ok": partial_report_safety_ok,
    }


def run_orchestration_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    scenario_runner = {
        "parallel_preflight_speedup": _run_orchestration_speedup_case,
        "network_semaphore_limit": _run_orchestration_semaphore_case,
        "level1_optional_fallback": _run_orchestration_level1_case,
        "level2_replan_checkpoint_reuse": _run_orchestration_level2_case,
        "level3_global_timeout_partial_report": _run_orchestration_level3_case,
    }
    results: list[dict[str, Any]] = []
    for case in selected:
        scenario = str(case.get("scenario") or "")
        expected = case["expected"]
        runner = scenario_runner.get(scenario)
        if runner is None:
            details = {"scenario": scenario, "error": f"Unknown orchestration scenario: {scenario}", "expected": expected}
            passed = False
        else:
            try:
                details = runner(case)
                details["expected"] = expected
                passed = _orchestration_case_passed(details, expected)
            except Exception as exc:  # noqa: BLE001 - benchmark rows should report structured failures.
                details = {"scenario": scenario, "error": str(exc), "expected": expected}
                passed = False
        results.append(_result_row(case["case_id"], passed, details))
    return {"suite": "orchestration", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def _recovery_response(run_id: str, conversation_id: str = "bench-recovery") -> AgentRunResponse:
    return AgentRunResponse(
        success=True,
        run_id=run_id,
        conversation_id=conversation_id,
        route=TaskRoute(name="lammps.generate", compute_domain="lammps", selected_tool="lammps"),
        final_message="recovery benchmark response",
        metadata={"benchmark": "lammps_recovery"},
    )


def _run_recovery_global_timeout_case() -> dict[str, Any]:
    async def slow_handler(node: DAGNode, context: DAGExecutionContext) -> dict[str, object]:
        await asyncio.sleep(0.05)
        return {"node": node.node_id, "late": True}

    with tempfile.TemporaryDirectory(prefix="lammps-recovery-timeout-") as tmp_dir:
        lifecycle = TaskLifecycleController(run_id="bench-global-timeout", run_dir=Path(tmp_dir))
        lifecycle.transition(to_state="planning", reason="benchmark")
        plan = DAGPlan(
            plan_id="bench-timeout-plan/v1",
            global_timeout_seconds=0.01,
            nodes=[
                DAGNode(node_id="extract", node_type="slow", timeout_seconds=1.0),
                DAGNode(node_id="review", node_type="slow", dependencies=["extract"], timeout_seconds=1.0),
            ],
        )
        lifecycle.record_plan_created(plan, metadata={"created_from": "benchmark"})
        lifecycle.save_checkpoint(stage="after_plan", plan=plan, metadata={"created_from": "benchmark"})
        lifecycle.transition(to_state="preflight", reason="benchmark", plan_version=plan.plan_version)
        partial_results = {}

        def save_checkpoint(node_result) -> None:  # noqa: ANN001 - DAG callback passes DAGNodeResult.
            partial_results[node_result.node_id] = node_result
            lifecycle.save_checkpoint(
                stage=f"after_node_{node_result.node_id}",
                plan=plan,
                results=partial_results,
                node_id=node_result.node_id,
            )

        result = asyncio.run(
            DAGExecutor().run(
                plan,
                DAGExecutionContext(run_id="bench-global-timeout"),
                handlers={"slow": slow_handler},
                node_result_sink=save_checkpoint,
            )
        )
        decision = decide_degradation(
            plan,
            result,
            budget_state=ReplanBudgetState(repair_budget=1, replan_budget=1),
            last_checkpoint_id=lifecycle.state.last_checkpoint_id,
        )
        return {
            "scenario": "global_timeout_partial_report",
            "dag_status": result.status,
            "termination_reason": decision.termination_reason,
            "partial_report": decision.partial_report.model_dump(mode="json") if decision.partial_report else {},
            "checkpoint_count": len(lifecycle.state.checkpoints),
            "last_checkpoint_id": lifecycle.state.last_checkpoint_id,
        }


def _run_recovery_preflight_replan_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="lammps-recovery-replan-") as tmp_dir:
        runtime = LammpsRuntime(
            artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
            llm_client=ScriptedLLMClient(),
            config_loader=lambda: LammpsConfig(
                allow_mock_fallback=True,
                force_mock=True,
                lammps_command="",
                potentials_dir="",
                max_retries=1,
                lammps_preflight_dag_enabled=True,
            ),
        )
        original_red_review = runtime._preflight_red_review_handler
        calls = {"count": 0}

        def fail_once(node, context):  # noqa: ANN001 - bound runtime hook follows DAG handler contract.
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("benchmark transient red review failure")
            return original_red_review(node, context)

        runtime._preflight_red_review_handler = fail_once  # type: ignore[method-assign]
        response = runtime.run(
            run_id="bench-preflight-replan",
            request=build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。", conversation_id="bench-recovery-replan"),
        )
    preflight = response.metadata.get("preflight_dag", {}) if isinstance(response.metadata, dict) else {}
    degradation = preflight.get("metadata", {}).get("degradation", {}) if isinstance(preflight, dict) else {}
    history = degradation.get("replan_history", []) if isinstance(degradation, dict) else []
    initial_decision = history[0] if history and isinstance(history[0], dict) else {}
    results_by_node = preflight.get("results", {}) if isinstance(preflight, dict) else {}
    return {
        "scenario": "preflight_level2_replan_reuse",
        "success": response.success,
        "preflight_status": preflight.get("status") if isinstance(preflight, dict) else "",
        "replan_executed": bool(degradation.get("replan_executed")) if isinstance(degradation, dict) else False,
        "final_plan_version": degradation.get("final_plan_version") if isinstance(degradation, dict) else None,
        "invalidated_nodes": initial_decision.get("invalidated_nodes", []),
        "reused_nodes": initial_decision.get("reused_nodes", []),
        "node_reuse": {
            node_id: bool((node_result.get("metadata") or {}).get("reused_from_checkpoint"))
            for node_id, node_result in results_by_node.items()
            if isinstance(node_result, dict)
        },
    }


def _run_recovery_worker_crash_case() -> dict[str, Any]:
    def crashing_runner(request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
        if event_sink:
            event_sink(AgentStreamEvent(type="run_started", run_id="run-crash", payload={"message": request.message}))
        raise RuntimeError("benchmark worker crash")

    with tempfile.TemporaryDirectory(prefix="lammps-recovery-crash-") as tmp_dir:
        store = AgentJobStore(root_dir=Path(tmp_dir))
        worker = AgentJobWorker(store=store, runner=crashing_runner, poll_interval_seconds=0.01)
        record = worker.submit_agent_chat(AgentChatRequest(conversation_id="bench-crash", message="crash"))
        claimed = store.claim_next()
        assert claimed is not None
        worker._run_record(claimed)
        latest = store.get(record.job_id)
        events = store.events_after(record.job_id)
        return {
            "scenario": "worker_crash_failed_event",
            "job_status": latest.status if latest else "",
            "run_id": latest.run_id if latest else "",
            "event_types": [item.event.type for item in events],
            "error": latest.error if latest else "",
        }


def _run_recovery_running_cancel_case() -> dict[str, Any]:
    started = threading.Event()
    release = threading.Event()

    def blocking_runner(request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
        if event_sink:
            event_sink(AgentStreamEvent(type="run_started", run_id="run-cancel-running", payload={"message": request.message}))
        started.set()
        release.wait(timeout=2.0)
        return _recovery_response("run-cancel-running", conversation_id=request.conversation_id)

    with tempfile.TemporaryDirectory(prefix="lammps-recovery-cancel-") as tmp_dir:
        store = AgentJobStore(root_dir=Path(tmp_dir))
        worker = AgentJobWorker(store=store, runner=blocking_runner, poll_interval_seconds=0.01)
        worker.start()
        try:
            record = worker.submit_agent_chat(AgentChatRequest(conversation_id="bench-cancel", message="cancel"))
            started.wait(timeout=2.0)
            worker.cancel(record.job_id)
            release.set()
            deadline = time.time() + 3
            latest = store.get(record.job_id)
            while latest and latest.status not in {"completed", "failed", "cancelled"} and time.time() < deadline:
                time.sleep(0.02)
                latest = store.get(record.job_id)
        finally:
            release.set()
            worker.stop()
        events = store.events_after(record.job_id)
        return {
            "scenario": "running_cancel_not_overwritten",
            "job_status": latest.status if latest else "",
            "run_id": latest.run_id if latest else "",
            "result_run_id": latest.result_run_id if latest else "",
            "event_types": [item.event.type for item in events],
        }


def _recovery_case_passed(details: dict[str, Any], expected: dict[str, Any]) -> bool:
    scenario = details.get("scenario")
    if scenario == "global_timeout_partial_report":
        partial = details.get("partial_report", {}) if isinstance(details.get("partial_report"), dict) else {}
        return (
            details.get("dag_status") == "timed_out"
            and details.get("termination_reason") == expected.get("termination_reason")
            and partial.get("resume_supported") is expected.get("resume_supported")
            and partial.get("scientific_result_available") is expected.get("scientific_result_available")
            and int(details.get("checkpoint_count") or 0) >= int(expected.get("checkpoint_count_min") or 0)
            and bool(details.get("last_checkpoint_id"))
        )
    if scenario == "preflight_level2_replan_reuse":
        reused = set(details.get("reused_nodes") or [])
        return (
            details.get("success") is expected.get("success")
            and details.get("replan_executed") is expected.get("replan_executed")
            and details.get("final_plan_version") == expected.get("final_plan_version")
            and details.get("invalidated_nodes") == expected.get("invalidated_nodes")
            and set(expected.get("reused_nodes_contains") or []) <= reused
        )
    if scenario in {"worker_crash_failed_event", "running_cancel_not_overwritten"}:
        return all(details.get(key) == value for key, value in expected.items())
    return False


def run_lammps_recovery_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    scenario_runner = {
        "global_timeout_partial_report": _run_recovery_global_timeout_case,
        "preflight_level2_replan_reuse": _run_recovery_preflight_replan_case,
        "worker_crash_failed_event": _run_recovery_worker_crash_case,
        "running_cancel_not_overwritten": _run_recovery_running_cancel_case,
    }
    for case in selected:
        scenario = str(case.get("scenario") or "")
        expected = case["expected"]
        runner = scenario_runner.get(scenario)
        if runner is None:
            details = {"scenario": scenario, "error": f"Unknown lammps_recovery scenario: {scenario}"}
            passed = False
        else:
            try:
                details = runner()
                passed = _recovery_case_passed(details, expected)
            except Exception as exc:  # noqa: BLE001 - keep benchmark row-level failure details.
                details = {"scenario": scenario, "error": str(exc)}
                passed = False
        details["expected"] = expected
        results.append(_result_row(case["case_id"], passed, details))
    return {"suite": "lammps_recovery", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_recognition_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        with TestClient(api_module.app) as client:
            for case in selected:
                request = build_request(case["prompt"], conversation_id=f"bench-recognition-{uuid.uuid4().hex[:8]}")
                request.uploaded_assets = [
                    UploadedAsset(
                        asset_id="bench-img",
                        name="diagram.png",
                        media_type="image/png",
                        data_url=MINI_PNG_DATA_URL,
                        size_bytes=128,
                    )
                ]
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                payload = response.json()
                recognition = payload.get("recognition_result") or {}
                passed = (
                    response.status_code == 200
                    and payload["route"]["name"] == case["expected"]["route_name"]
                    and recognition.get("source") == case["expected"]["source"]
                    and recognition.get("system") == case["expected"]["system_name"]
                )
                results.append(_result_row(case["case_id"], passed, {"status_code": response.status_code, "recognition": recognition}))
    return {"suite": "recognition", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_external_recognition_live_benchmark(
    cases: list[dict[str, Any]],
    *,
    limit: int | None = None,
    api_base: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        asset_path = _resolve_backend_path(case["asset_path"])
        mime = mimetypes.guess_type(str(asset_path))[0] or "image/jpeg"
        encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
        payload = {
            "message": case["prompt"],
            "conversation_id": f"bench-external-recognition-{uuid.uuid4().hex[:8]}",
            "uploaded_assets": [
                {
                    "asset_id": f"bench-img-{uuid.uuid4().hex[:8]}",
                    "name": asset_path.name,
                    "media_type": mime,
                    "data_url": f"data:{mime};base64,{encoded}",
                    "size_bytes": asset_path.stat().st_size,
                }
            ],
        }
        response = requests.post(f"{api_base.rstrip('/')}/api/agent/chat", json=payload, timeout=240)
        details: dict[str, Any] = {
            "status_code": response.status_code,
            "source_url": case["source_url"],
            "asset_path": str(asset_path),
        }
        passed = False
        if response.ok:
            payload_json = response.json()
            recognition = payload_json.get("recognition_result") or {}
            x_axis_label = ((recognition.get("x_axis") or {}).get("label") or "").lower()
            y_axis_label = ((recognition.get("y_axis") or {}).get("label") or "").lower()
            phases = recognition.get("phases") or []
            system = recognition.get("system")
            passed = (
                payload_json.get("route", {}).get("name") == case["expected"]["route_name"]
                and system in case["expected"]["system_names"]
                and recognition.get("diagram_type") == case["expected"]["diagram_type"]
                and any(keyword in x_axis_label for keyword in case["expected"]["x_axis_keywords"])
                and any(keyword in y_axis_label for keyword in case["expected"]["y_axis_keywords"])
                and len(phases) >= case["expected"]["min_phase_count"]
            )
            details.update(
                {
                    "run_id": payload_json.get("run_id"),
                    "route": payload_json.get("route", {}),
                    "recognition_result": recognition,
                }
            )
        else:
            details["body"] = response.text[:2000]
        results.append(_result_row(case["case_id"], passed, details))
    return {
        "suite": "external_recognition_live",
        "cases": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "results": results,
    }


def run_memory_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        with TestClient(api_module.app) as client:
            for case in selected:
                conversation_id = f"bench-memory-{uuid.uuid4().hex[:8]}"
                last_payload: dict[str, Any] = {}
                passed = True
                for turn in case["turns"]:
                    overrides = turn.get("request_overrides", {})
                    request = build_request(
                        turn["message"],
                        conversation_id=conversation_id,
                        system_name=overrides.get("system_name", ""),
                        temperature_min=overrides.get("temperature_min", 300.0),
                        temperature_max=overrides.get("temperature_max", 1800.0),
                    )
                    response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                    payload = response.json()
                    last_payload = payload
                    if response.status_code != 200 or payload["route"]["name"] != turn["expected_route_name"]:
                        passed = False
                        break
                    for marker in turn.get("expected_contains", []):
                        if marker not in payload.get("final_message", ""):
                            passed = False
                            break
                    if not passed:
                        break
                results.append(_result_row(case["case_id"], passed, {"final_route": last_payload.get("route", {}), "final_message": last_payload.get("final_message", "")}))
    return {"suite": "memory_followup", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_memory_retrieval_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            snapshot = store.build_next_snapshot(
                conversation_id=f"bench-memory-retrieval-{uuid.uuid4().hex[:8]}",
                messages=[ConversationTurn(**item) for item in case["seed_messages"]],
                uploaded_assets=[],
                recognition_result=RecognitionResult(**case["recognition_result"]) if case.get("recognition_result") else None,
                last_run_context=LastRunContext(**case["last_run_context"]) if case.get("last_run_context") else None,
                current_context_summary="",
            )
            hits = store.retrieve_long_term_context(
                query=case["query"],
                snapshot=snapshot,
                conversation_id=snapshot.conversation_id,
                limit=5,
            )
        passed = all(marker in " ".join(hits) for marker in case["expected_hits"])
        results.append(_result_row(case["case_id"], passed, {"hits": hits}))
    return {"suite": "memory_retrieval", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def _shared_memory_item(payload: dict[str, Any], *, default_scope_id: str) -> MemoryItem:
    item_payload = {
        "scope_type": "conversation",
        "scope_id": default_scope_id,
        "item_type": "fact",
        "subject": "benchmark memory",
        "predicate": "fact",
        "value": payload.get("text") or payload.get("value") or "",
        "text": str(payload.get("text") or payload.get("value") or ""),
        "authority": "execution",
        "source_refs": [f"benchmark:{uuid.uuid4().hex[:8]}"],
    }
    item_payload.update(payload)
    return MemoryItem.model_validate(item_payload)


def run_shared_memory_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        scenario = str(case["scenario"])
        expected = case["expected"]
        with tempfile.TemporaryDirectory(prefix="shared-memory-bench-") as tmp_dir:
            service = SharedMemoryService(root_dir=Path(tmp_dir))
            writes = [
                service.write(_shared_memory_item(item, default_scope_id=str(case.get("scope_id") or "bench-shared-memory")))
                for item in case["items"]
            ]
            details: dict[str, Any] = {
                "scenario": scenario,
                "expected": expected,
                "writes": [write.model_dump(mode="json") for write in writes],
            }
            if scenario == "duplicate_normalized":
                active_items = service.store.list_items(
                    scope=MemoryScope(scope_type="conversation", scope_id=str(case.get("scope_id") or "bench-shared-memory"), include_global=False),
                    statuses=["active"],
                    limit=100,
                )
                duplicate_ok = len(active_items) == int(expected.get("active_count", 1)) and any(write.deduplicated for write in writes)
                details["duplicate_ok"] = duplicate_ok
                details["active_count"] = len(active_items)
            elif scenario == "scope_isolation":
                retrieval = service.retrieve(
                    query=str(case["query"]),
                    scope=MemoryScope(scope_type="conversation", scope_id=str(expected["scope_id"]), include_global=False),
                    top_k=5,
                )
                scopes = {candidate.item.scope_id for candidate in retrieval.candidates}
                scope_isolation_ok = scopes == {str(expected["scope_id"])}
                details["scope_isolation_ok"] = scope_isolation_ok
                details["selected_scopes"] = sorted(scopes)
            elif scenario == "locked_retention":
                retrieval = service.retrieve(
                    query=str(case["query"]),
                    scope=MemoryScope(scope_type="conversation", scope_id=str(case.get("scope_id") or "bench-shared-memory"), include_global=False),
                    top_k=1,
                    prompt_budget_bytes=int(case.get("prompt_budget_bytes") or 320),
                )
                forced = set(retrieval.forced_retention_ids)
                locked_ids = {write.item.memory_id for write in writes if write.item.metadata.get("locked") is True}
                locked_retention_ok = bool(locked_ids) and locked_ids <= forced
                details["locked_retention_ok"] = locked_retention_ok
                details["forced_retention_ids"] = retrieval.forced_retention_ids
            elif scenario == "raw_evidence_traceability":
                expanded = service.expand_evidence([writes[0].item.memory_id])
                evidence_traceability_ok = bool(expanded) and all(item.hash_verified is True for item in expanded)
                details["evidence_traceability_ok"] = evidence_traceability_ok
                details["expanded"] = [item.model_dump(mode="json") for item in expanded]
            else:
                details["error"] = f"Unknown shared_memory scenario: {scenario}"
            passed = all(value is True for key, value in details.items() if key.endswith("_ok"))
            results.append(_result_row(case["case_id"], passed, details))
    return {"suite": "shared_memory", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_memory_conflict_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        expected = case["expected"]
        with tempfile.TemporaryDirectory(prefix="memory-conflict-bench-") as tmp_dir:
            service = SharedMemoryService(root_dir=Path(tmp_dir))
            writes = [
                service.write(_shared_memory_item(item, default_scope_id=str(case.get("scope_id") or "bench-memory-conflict")))
                for item in case["items"]
            ]
            incoming = writes[-1]
            conflicts = [service.store.get_conflict(conflict_id) for conflict_id in incoming.conflict_ids]
            conflicts = [conflict for conflict in conflicts if conflict is not None]
            incoming_loaded = service.store.get_item(incoming.item.memory_id)
        conflict = conflicts[0] if conflicts else None
        details: dict[str, Any] = {
            "scenario": case["scenario"],
            "expected": expected,
            "incoming_status": incoming_loaded.status if incoming_loaded else incoming.item.status,
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
        }
        conflict_ok = bool(conflict)
        if conflict is not None:
            conflict_ok = (
                conflict.conflict_type == expected.get("conflict_type")
                and conflict.detection_mode == expected.get("detection_mode")
                and conflict.status == expected.get("conflict_status", conflict.status)
            )
        details["conflict_ok"] = conflict_ok
        if expected.get("conflict_status") == "needs_user":
            details["needs_user_ok"] = conflict is not None and conflict.status == "needs_user"
        if expected.get("incoming_status") == "quarantined":
            details["quarantine_ok"] = details["incoming_status"] == "quarantined"
        if expected.get("detection_mode") == "semantic_candidate":
            details["semantic_candidate_ok"] = conflict is not None and conflict.detection_mode == "semantic_candidate"
        details["no_incorrect_auto_resolution_ok"] = conflict is not None and conflict.status in {"open", "needs_user"}
        passed = all(value is True for key, value in details.items() if key.endswith("_ok"))
        results.append(_result_row(case["case_id"], passed, details))
    return {"suite": "memory_conflict", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def run_context_compression_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    for case in selected:
        expected = case["expected"]
        with tempfile.TemporaryDirectory(prefix="context-compression-bench-") as tmp_dir:
            service = SharedMemoryService(root_dir=Path(tmp_dir))
            writes = [
                service.write(_shared_memory_item(item, default_scope_id=str(case.get("scope_id") or "bench-context-compression")))
                for item in case["items"]
            ]
            retrieval = service.retrieve(
                query=str(case["query"]),
                scope=MemoryScope(scope_type="conversation", scope_id=str(case.get("scope_id") or "bench-context-compression"), include_global=False),
                top_k=3,
                prompt_budget_bytes=int(case.get("prompt_budget_bytes") or 100_000),
            )
        candidate = retrieval.candidates[0] if retrieval.candidates else None
        compression = candidate.item.metadata.get("context_compression", {}) if candidate else {}
        details: dict[str, Any] = {
            "scenario": case["scenario"],
            "expected": expected,
            "writes": [write.model_dump(mode="json") for write in writes],
            "retrieval": retrieval.model_dump(mode="json"),
            "compression": compression,
        }
        if expected.get("requires_l3_trace"):
            l3 = compression.get("l3") if isinstance(compression, dict) else {}
            raw_ids = l3.get("raw_evidence_ids") if isinstance(l3, dict) else []
            details["l2_traceability_ok"] = bool(candidate and compression and l3 and raw_ids)
        if expected.get("protected"):
            details["noncompressible_protection_ok"] = bool(compression.get("protected") is True)
        passed = all(value is True for key, value in details.items() if key.endswith("_ok"))
        results.append(_result_row(case["case_id"], passed, details))
    return {
        "suite": "context_compression",
        "cases": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "results": results,
    }


def run_materials_multihop_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    bench_cases = {
        case.source_case_id: case
        for case in build_materials_agent_cases({"materials_multihop_cases": selected})
    }
    results: list[dict[str, Any]] = []
    for case in selected:
        bench_case = bench_cases[str(case["case_id"])]
        evaluation = evaluate_materials_multihop(bench_case, dict(case["observation"]))
        payload = evaluation.to_dict()
        metrics = payload["metrics"]
        details = {
            "expected": case["expected"],
            "evaluation": payload,
            "required_hops_ok": _metric_passed(metrics, "required_hop_completion"),
            "evidence_chain_ok": _metric_passed(metrics, "evidence_chain_completeness"),
            "bridge_claims_ok": _metric_passed(metrics, "no_unsupported_bridge_claim_rate"),
            "final_conclusion_ok": _metric_passed(metrics, "final_conclusion_correctness"),
            "citation_order_authority_ok": _metric_passed(metrics, "citation_order_authority_rate"),
            "missing_hop_honesty_ok": _metric_passed(metrics, "missing_hop_honesty_rate"),
        }
        results.append(_result_row(case["case_id"], evaluation.passed, details))
    return {
        "suite": "materials_multihop",
        "cases": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "results": results,
    }


def _judge_blind_input_safe(details: dict[str, Any]) -> bool:
    report = details.get("report", {}) if isinstance(details.get("report"), dict) else {}
    metadata = report.get("metadata", {}) if isinstance(report.get("metadata"), dict) else {}
    blind_input = metadata.get("blind_input", {}) if isinstance(metadata.get("blind_input"), dict) else {}
    serialized = json.dumps(blind_input, ensure_ascii=False, sort_keys=True).lower()
    forbidden_markers = (
        "source_dataset",
        "source_case_id",
        "split",
        "human_scores",
        "raw_judge_payload",
        "judge_calibration.",
    )
    return bool(blind_input) and not any(marker in serialized for marker in forbidden_markers)


def run_judge_calibration_benchmark(
    cases: list[dict[str, Any]],
    *,
    limit: int | None = None,
    live_judge: bool = False,
) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    adapted_cases = {
        case.source_case_id: case
        for case in build_materials_agent_cases({"judge_calibration_cases": selected})
    }
    results: list[dict[str, Any]] = []
    calibrations = []
    provider_config = judge_provider_config_from_env(os.environ)
    live_provider_enabled = live_judge and provider_config.provider not in {"offline_contract", "mock", "local"} and provider_config.configured
    for row in selected:
        adapted = adapted_cases[str(row["case_id"])]
        if live_provider_enabled:
            observation = dict(row.get("observation") or {})
            report = evaluate_judge_with_provider(
                adapted,
                observation,
                provider_config=provider_config,
                require_live=False,
            )
            calibration = evaluate_judge_calibration_case(adapted, row, report=report)
        else:
            calibration = evaluate_judge_calibration_case(adapted, row)
        calibrations.append(calibration)
        details = calibration.model_dump(mode="json")
        details["parse_recovered_ok"] = calibration.parse_recovered
        details["hard_gate_non_override_ok"] = not calibration.hard_gate_override
        details["blind_input_safety_ok"] = _judge_blind_input_safe(details)
        details["live_judge_requested"] = live_judge
        details["live_provider_called"] = live_provider_enabled
        details["judge_provider"] = sanitized_provider_metadata(provider_config)
        calibration_quality_ok = calibration.within_one_agreement >= 0.8 or not calibration.report.hard_gate_passed
        details["calibration_quality_ok"] = calibration_quality_ok
        passed = calibration_quality_ok and details["parse_recovered_ok"] and details["hard_gate_non_override_ok"] and details["blind_input_safety_ok"]
        results.append(_result_row(row["case_id"], passed, details))
    return {
        "suite": "judge_calibration",
        "cases": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "results": results,
        "drift_report": build_judge_drift_report(calibrations).model_dump(mode="json"),
        "backend_matrix": build_judge_backend_matrix(os.environ).model_dump(mode="json"),
        "judge_provider": sanitized_provider_metadata(provider_config),
        "live_judge_requested": live_judge,
        "live_provider_called": live_provider_enabled,
    }


def _metric_passed(metrics: dict[str, Any], name: str) -> bool:
    metric = metrics.get(name, {})
    if metric.get("status") == "not_applicable":
        return True
    return metric.get("passed") is not False


def run_mcp_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    server = _build_mcp_server()
    results: list[dict[str, Any]] = []
    for case in selected:
        response = server.handle_request(case["request"])
        passed = False
        details: dict[str, Any] = {"response": response}
        if case["case_id"] == "mcp.initialize":
            result = response["result"]
            passed = result["protocolVersion"] == case["expected"]["protocol_version"] and result["serverInfo"]["name"] == case["expected"]["server_name"]
        elif case["case_id"] == "mcp.tools_list":
            tools = {tool["name"] for tool in response["result"]["tools"]}
            passed = all(name in tools for name in case["expected"]["required_tools"])
            details["tools"] = sorted(tools)
        else:
            payload = json.loads(response["result"]["content"][0]["text"])
            if case["case_id"] == "mcp.phase_registry_search":
                passed = payload["matched"] is True and payload["card"]["system_name"] == case["expected"]["system_name"]
            elif case["case_id"] == "mcp.phase_rag_search":
                passed = payload["matched"] is True and payload["candidates"][0]["system_name"] == case["expected"]["top_system_name"]
            else:
                passed = payload["run_id"] == case["expected"]["run_id"]
            details["payload"] = payload
        results.append(_result_row(case["case_id"], passed, details))
    return {"suite": "mcp", "cases": len(results), "passed": sum(1 for item in results if item["passed"]), "results": results}


def print_summary(datasets: dict[str, list[dict[str, Any]]]) -> None:
    summary = build_manifest(datasets)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def run_suite(
    name: str,
    datasets: dict[str, list[dict[str, Any]]],
    *,
    limit: int | None = None,
    api_base: str = "http://127.0.0.1:8000",
    real_lammps: bool = False,
    live_backends: bool = False,
) -> dict[str, Any]:
    suite_map = {
        "routing": lambda: run_routing_benchmark(datasets["routing_cases"], limit=limit),
        "phase_execution": lambda: run_phase_execution_benchmark(datasets["phase_execution_cases"], limit=limit),
        "lammps_contract": lambda: run_lammps_contract_benchmark(
            datasets["lammps_contract_cases"],
            limit=limit,
            real_lammps=real_lammps,
        ),
        "lammps_e2e": lambda: run_lammps_e2e_benchmark(
            datasets["lammps_e2e_cases"],
            limit=limit,
            real_lammps=real_lammps,
        ),
        "lammps_quality": lambda: run_lammps_quality_benchmark(datasets["lammps_quality_cases"], limit=limit),
        "lammps_red_blue": lambda: run_lammps_red_blue_benchmark(datasets["lammps_red_blue_cases"], limit=limit),
        "review_json_fallback": lambda: run_review_json_fallback_benchmark(datasets["review_json_fallback_cases"], limit=limit),
        "orchestration": lambda: run_orchestration_benchmark(datasets["orchestration_cases"], limit=limit),
        "judge_calibration": lambda: run_judge_calibration_benchmark(
            datasets["judge_calibration_cases"],
            limit=limit,
            live_judge=live_backends,
        ),
        "lammps_recovery": lambda: run_lammps_recovery_benchmark(datasets["lammps_recovery_cases"], limit=limit),
        "recognition": lambda: run_recognition_benchmark(datasets["recognition_cases"], limit=limit),
        "external_recognition_live": lambda: run_external_recognition_live_benchmark(datasets["external_recognition_cases"], limit=limit, api_base=api_base),
        "memory": lambda: run_memory_benchmark(datasets["memory_followup_cases"], limit=limit),
        "memory_retrieval": lambda: run_memory_retrieval_benchmark(datasets["memory_retrieval_cases"], limit=limit),
        "shared_memory": lambda: run_shared_memory_benchmark(datasets["shared_memory_cases"], limit=limit),
        "memory_conflict": lambda: run_memory_conflict_benchmark(datasets["memory_conflict_cases"], limit=limit),
        "context_compression": lambda: run_context_compression_benchmark(datasets["context_compression_cases"], limit=limit),
        "materials_multihop": lambda: run_materials_multihop_benchmark(datasets["materials_multihop_cases"], limit=limit),
        "mcp": lambda: run_mcp_benchmark(datasets["mcp_cases"], limit=limit),
        "rag_recall": lambda: run_rag_recall(top_k=5, limit=limit),
    }
    with ExitStack() as stack:
        _patch_deterministic_rag_backends(stack, live_backends=live_backends)
        return suite_map[name]()


def run_all_benchmarks(
    datasets: dict[str, list[dict[str, Any]]],
    *,
    suites: list[str] | None = None,
    limit: int | None = None,
    include_live: bool = False,
    api_base: str = "http://127.0.0.1:8000",
    real_lammps: bool = False,
    live_backends: bool = False,
) -> dict[str, Any]:
    selected_suites = suites or list(DETERMINISTIC_SUITES)
    if include_live:
        selected_suites = [*selected_suites, *LIVE_SUITES]
    started = time.perf_counter()
    raw_results: dict[str, Any] = {}
    suite_metrics: dict[str, dict[str, Any]] = {}
    for suite in selected_suites:
        suite_started = time.perf_counter()
        result = run_suite(
            suite,
            datasets,
            limit=limit,
            api_base=api_base,
            real_lammps=real_lammps,
            live_backends=live_backends,
        )
        elapsed = time.perf_counter() - suite_started
        raw_results[suite] = result
        suite_metrics[suite] = _rag_metric_summary(result) if suite == "rag_recall" else _suite_metric_summary(suite, result, elapsed_seconds=elapsed)
    checks = _threshold_results(suite_metrics)
    return {
        "schema_version": "agent-benchmark-report/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark_manifest": build_manifest(datasets),
        "selected_suites": selected_suites,
        "limit": limit,
        "include_live": include_live,
        "real_lammps": real_lammps,
        "live_backends": live_backends,
        "thresholds": BENCHMARK_THRESHOLDS,
        "threshold_checks": checks,
        "passed": all(item["passed"] for item in checks) and bool(checks),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "metrics": suite_metrics,
        "raw_results": raw_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark runner for phase_diagram_agent backend")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("summary")
    sub.add_parser("validate")
    run_parser = sub.add_parser("run")
    run_parser.add_argument(
        "--suite",
        required=True,
        choices=[*DETERMINISTIC_SUITES, *LIVE_SUITES],
    )
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    run_parser.add_argument("--real-lammps", action="store_true", help="Allow LAMMPS suites to use the configured real local executable instead of deterministic mock mode.")
    run_parser.add_argument("--live-backends", action="store_true", help="Allow embedding/reranker backends from local configuration instead of deterministic local_hash/disabled mode.")
    run_all_parser = sub.add_parser("run-all")
    run_all_parser.add_argument("--suite", action="append", choices=[*DETERMINISTIC_SUITES, *LIVE_SUITES], help="Run only the selected suite. May be passed multiple times.")
    run_all_parser.add_argument("--limit", type=int, default=None)
    run_all_parser.add_argument("--include-live", action="store_true", help="Also run live external recognition benchmark against --api-base.")
    run_all_parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    run_all_parser.add_argument("--real-lammps", action="store_true", help="Allow LAMMPS suites to use the configured real local executable instead of deterministic mock mode.")
    run_all_parser.add_argument("--live-backends", action="store_true", help="Allow embedding/reranker backends from local configuration instead of deterministic local_hash/disabled mode.")
    run_all_parser.add_argument("--output", type=Path, default=DEFAULT_BENCHMARK_OUTPUT)
    args = parser.parse_args()

    datasets = load_datasets()
    if args.command == "summary":
        print_summary(datasets)
        return 0
    if args.command == "validate":
        errors = validate_datasets(datasets)
        if errors:
            print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, "manifest": build_manifest(datasets)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-all":
        report = run_all_benchmarks(
            datasets,
            suites=args.suite,
            limit=args.limit,
            include_live=args.include_live,
            api_base=args.api_base,
            real_lammps=args.real_lammps,
            live_backends=args.live_backends,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "passed": report["passed"],
                    "elapsed_seconds": report["elapsed_seconds"],
                    "metrics": report["metrics"],
                    "threshold_checks": report["threshold_checks"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report["passed"] else 1

    result = run_suite(
        args.suite,
        datasets,
        limit=args.limit,
        api_base=args.api_base,
        real_lammps=args.real_lammps,
        live_backends=args.live_backends,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.suite == "rag_recall":
        materials_ok = result["materials_rag"]["summary"].get("hit@5", 0.0) >= BENCHMARK_THRESHOLDS["rag_recall.materials_hit@5"]
        thermo_ok = result["thermo_rag"]["summary"].get("hit@5", 0.0) >= BENCHMARK_THRESHOLDS["rag_recall.thermo_hit@5"]
        return 0 if materials_ok and thermo_ok else 1
    return 0 if result["passed"] == result["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
