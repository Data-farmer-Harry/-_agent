from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import tempfile
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
from app.memory import MemoryStore
from app.mcp_server import MaterialsMcpServer
from app.state import AgentGraphState, AgentRunResponse, ArtifactRef, ConversationTurn, LastRunContext, RecognitionResult, TaskRoute, UploadedAsset
from benchmarks.build_datasets import DATASET_DIR, build_all_datasets, build_manifest
from benchmarks.run_rag_recall import run_rag_recall
from tests.support import MINI_PNG_DATA_URL, ScriptedLLMClient, build_request


DEFAULT_BENCHMARK_OUTPUT = BACKEND_ROOT / "outputs" / "benchmarks" / "latest.json"
DETERMINISTIC_SUITES = (
    "routing",
    "rag_recall",
    "phase_execution",
    "lammps_contract",
    "lammps_e2e",
    "recognition",
    "memory",
    "memory_retrieval",
    "mcp",
)
LIVE_SUITES = ("external_recognition_live",)
BENCHMARK_THRESHOLDS = {
    "routing.route_accuracy": 0.90,
    "routing.compute_domain_accuracy": 0.90,
    "rag_recall.materials_hit@5": 0.80,
    "rag_recall.thermo_hit@5": 0.80,
    "phase_execution.success_rate": 0.80,
    "phase_execution.accuracy_gate_pass_rate": 0.80,
    "lammps_contract.artifact_completeness": 0.80,
    "lammps_e2e.chain_completion_rate": 0.80,
    "lammps_e2e.clarification_accuracy": 0.80,
    "lammps_e2e.rag_preflight_rate": 0.80,
    "recognition.success_rate": 0.80,
    "memory.followup_grounding_rate": 0.80,
    "memory_retrieval.memory_retrieval_relevance": 0.80,
    "mcp.tool_contract_pass_rate": 0.90,
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_datasets() -> dict[str, list[dict[str, Any]]]:
    if not DATASET_DIR.exists():
        raise FileNotFoundError(f"benchmark dataset directory does not exist: {DATASET_DIR}")
    datasets: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(DATASET_DIR.glob("*.jsonl")):
        datasets[path.stem] = _load_jsonl(path)
    return datasets


def validate_datasets(datasets: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    required_common = {"case_id", "suite", "mode", "tags"}
    for dataset_name, rows in datasets.items():
        if not rows:
            errors.append(f"{dataset_name}: dataset is empty")
            continue
        for index, row in enumerate(rows, start=1):
            dataset_required_common = {"case_id", "suite"} if dataset_name == "rag_blind_cases" else required_common
            missing = sorted(dataset_required_common - set(row.keys()))
            if missing:
                errors.append(f"{dataset_name}:{index}: missing common fields {missing}")
            if dataset_name == "rag_blind_cases":
                for field in ("query", "material", "domain", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name in {"routing_cases", "phase_parsing_cases", "lammps_parsing_cases", "phase_execution_cases", "lammps_contract_cases", "lammps_e2e_cases", "recognition_cases"}:
                for field in ("prompt", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "external_recognition_cases":
                for field in ("prompt", "expected", "asset_path", "source_url"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
                else:
                    asset_path = Path(row["asset_path"])
                    if not asset_path.exists():
                        errors.append(f"{dataset_name}:{index}: asset_path does not exist: {asset_path}")
            if dataset_name == "memory_followup_cases" and "turns" not in row:
                errors.append(f"{dataset_name}:{index}: missing turns")
            if dataset_name == "memory_retrieval_cases":
                for field in ("seed_messages", "query", "expected_hits"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "mcp_cases" and "request" not in row:
                errors.append(f"{dataset_name}:{index}: missing request")
    return errors


def _patch_api_llm_clients(stack: ExitStack) -> ScriptedLLMClient:
    scripted_llm = ScriptedLLMClient()
    stack.enter_context(patch.object(api_module.supervisor_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.recognition_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.chat_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.phase_diagram_runtime.codegen_service, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.phase_diagram_runtime.phase_agent_service, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.lammps_runtime, "llm_client", scripted_llm))
    return scripted_llm


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
    elif suite == "memory":
        metrics["followup_grounding_rate"] = pass_rate
    elif suite == "memory_retrieval":
        metrics["memory_retrieval_relevance"] = pass_rate
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


def run_lammps_contract_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
        with TestClient(api_module.app) as client:
            for case in selected:
                request = build_request(case["prompt"], conversation_id=f"bench-lammps-{uuid.uuid4().hex[:8]}")
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                payload = response.json()
                artifact_names = {item["name"] for item in payload.get("artifacts", [])}
                plan_steps = [item["tool_name"] for item in payload.get("plan_steps", [])]
                passed = (
                    response.status_code == 200
                    and payload["route"]["name"] == case["expected"]["route_name"]
                    and payload["route"]["compute_domain"] == case["expected"]["compute_domain"]
                    and all(name in artifact_names for name in case["expected"]["required_artifacts"])
                    and all(step in plan_steps for step in case["expected"]["plan_steps"])
                )
                results.append(
                    _result_row(
                        case["case_id"],
                        passed,
                        {
                            "status_code": response.status_code,
                            "artifact_names": sorted(artifact_names),
                            "required_artifacts": case["expected"]["required_artifacts"],
                            "plan_steps": plan_steps,
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


def _request_field_score(actual: dict[str, Any], expected: dict[str, Any]) -> float:
    if not expected:
        return 1.0
    hits = 0
    for key, expected_value in expected.items():
        if actual.get(key) == expected_value:
            hits += 1
    return round(hits / len(expected), 4)


def run_lammps_e2e_benchmark(cases: list[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    selected = cases[:limit] if limit else cases
    results: list[dict[str, Any]] = []
    with ExitStack() as stack:
        _patch_api_llm_clients(stack)
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
                materials_rag = metadata.get("materials_rag", {}) if isinstance(metadata, dict) else {}
                materials_rag_used = bool(((materials_rag.get("planning") or {}) if isinstance(materials_rag, dict) else {}).get("hits"))
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
        asset_path = Path(case["asset_path"])
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


def run_suite(name: str, datasets: dict[str, list[dict[str, Any]]], *, limit: int | None = None, api_base: str = "http://127.0.0.1:8000") -> dict[str, Any]:
    suite_map = {
        "routing": lambda: run_routing_benchmark(datasets["routing_cases"], limit=limit),
        "phase_execution": lambda: run_phase_execution_benchmark(datasets["phase_execution_cases"], limit=limit),
        "lammps_contract": lambda: run_lammps_contract_benchmark(datasets["lammps_contract_cases"], limit=limit),
        "lammps_e2e": lambda: run_lammps_e2e_benchmark(datasets["lammps_e2e_cases"], limit=limit),
        "recognition": lambda: run_recognition_benchmark(datasets["recognition_cases"], limit=limit),
        "external_recognition_live": lambda: run_external_recognition_live_benchmark(datasets["external_recognition_cases"], limit=limit, api_base=api_base),
        "memory": lambda: run_memory_benchmark(datasets["memory_followup_cases"], limit=limit),
        "memory_retrieval": lambda: run_memory_retrieval_benchmark(datasets["memory_retrieval_cases"], limit=limit),
        "mcp": lambda: run_mcp_benchmark(datasets["mcp_cases"], limit=limit),
        "rag_recall": lambda: run_rag_recall(top_k=5, limit=limit),
    }
    return suite_map[name]()


def run_all_benchmarks(
    datasets: dict[str, list[dict[str, Any]]],
    *,
    suites: list[str] | None = None,
    limit: int | None = None,
    include_live: bool = False,
    api_base: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    selected_suites = suites or list(DETERMINISTIC_SUITES)
    if include_live:
        selected_suites = [*selected_suites, *LIVE_SUITES]
    started = time.perf_counter()
    raw_results: dict[str, Any] = {}
    suite_metrics: dict[str, dict[str, Any]] = {}
    for suite in selected_suites:
        suite_started = time.perf_counter()
        result = run_suite(suite, datasets, limit=limit, api_base=api_base)
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
    run_parser.add_argument("--suite", required=True, choices=["routing", "rag_recall", "phase_execution", "lammps_contract", "lammps_e2e", "recognition", "external_recognition_live", "memory", "memory_retrieval", "mcp"])
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    run_all_parser = sub.add_parser("run-all")
    run_all_parser.add_argument("--suite", action="append", choices=[*DETERMINISTIC_SUITES, *LIVE_SUITES], help="Run only the selected suite. May be passed multiple times.")
    run_all_parser.add_argument("--limit", type=int, default=None)
    run_all_parser.add_argument("--include-live", action="store_true", help="Also run live external recognition benchmark against --api-base.")
    run_all_parser.add_argument("--api-base", default="http://127.0.0.1:8000")
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

    result = run_suite(args.suite, datasets, limit=args.limit, api_base=args.api_base)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.suite == "rag_recall":
        materials_ok = result["materials_rag"]["summary"].get("hit@5", 0.0) >= BENCHMARK_THRESHOLDS["rag_recall.materials_hit@5"]
        thermo_ok = result["thermo_rag"]["summary"].get("hit@5", 0.0) >= BENCHMARK_THRESHOLDS["rag_recall.thermo_hit@5"]
        return 0 if materials_ok and thermo_ok else 1
    return 0 if result["passed"] == result["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
