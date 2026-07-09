from __future__ import annotations

from dataclasses import dataclass
import json
from queue import Queue
from threading import Thread

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from app.graph import AgentAppGraph
from app.agents.chat import ChatAgent
from app.agents.compute import ComputeAgent
from app.config import llm_config_public_payload, settings, update_runtime_llm_config
from app.core.cancellation import cancel_run
from app.diagnostics import build_system_diagnostics
from app.core.llm import LLMRequiredError
from app.core.observability import log_event, new_request_id
from app.jobs import AgentJobStore, AgentJobWorker, TerminalJobStatus
from app.memory import MemoryStore
from app.shared_memory import SharedMemoryService
from app.lammps import get_lammps_registry_payload, lammps_config_public_payload, update_runtime_lammps_config
from app.materials_rag.service import MaterialsRagService
from app.rag.data_manager import RagDataManager
from app.runtimes.lammps import LammpsRuntime
from app.runtimes.manager import build_runtime_manager_report
from app.runtimes.phase_diagram import PhaseDiagramRuntime
from app.agents.recognition import RecognitionAgent
from app.state import (
    AgentChatRequest,
    AgentJobListResponse,
    AgentJobRecord,
    AgentJobResumeRequest,
    AgentJobResumeResponse,
    AgentRunResponse,
    AgentStreamEvent,
    ConversationSnapshotResponse,
    HealthResponse,
    PromptSuggestionRequest,
    PromptSuggestionResponse,
    SystemDiagnosticsResponse,
    ThermoRagSearchRequest,
    ThermoRagSearchResponse,
    ThermoRegistryEntry,
    ThermoRegistryResponse,
)
from app.agents.supervisor import SupervisorAgent
from app.core.artifacts import ArtifactService
from app.thermo.rag_service import ThermoRagService
from app.thermo.registry import load_thermo_database_cards
from app.utils.path_utils import ensure_directory


@dataclass
class AppDependencies:
    artifact_service: ArtifactService
    memory_store: MemoryStore
    shared_memory_service: SharedMemoryService
    job_store: AgentJobStore
    job_worker: AgentJobWorker
    supervisor_agent: SupervisorAgent
    recognition_agent: RecognitionAgent
    phase_diagram_runtime: PhaseDiagramRuntime
    lammps_runtime: LammpsRuntime
    compute_agent: ComputeAgent
    chat_agent: ChatAgent
    agent_graph: AgentAppGraph


def build_app_dependencies(
    *,
    artifact_service: ArtifactService | None = None,
    memory_store: MemoryStore | None = None,
    shared_memory_service: SharedMemoryService | None = None,
    job_store: AgentJobStore | None = None,
    job_worker: AgentJobWorker | None = None,
    supervisor_agent: SupervisorAgent | None = None,
    recognition_agent: RecognitionAgent | None = None,
    phase_diagram_runtime: PhaseDiagramRuntime | None = None,
    lammps_runtime: LammpsRuntime | None = None,
    compute_agent: ComputeAgent | None = None,
    chat_agent: ChatAgent | None = None,
) -> AppDependencies:
    artifact_service = artifact_service or ArtifactService(root_dir=settings.tmp_dir)
    memory_store = memory_store or MemoryStore(root_dir=settings.tmp_dir)
    shared_memory_service = shared_memory_service or SharedMemoryService(root_dir=memory_store.paths.root_dir)
    supervisor_agent = supervisor_agent or SupervisorAgent()
    recognition_agent = recognition_agent or RecognitionAgent(artifact_service=artifact_service)
    phase_diagram_runtime = phase_diagram_runtime or PhaseDiagramRuntime(artifact_service=artifact_service)
    lammps_runtime = lammps_runtime or LammpsRuntime(artifact_service=artifact_service)
    compute_agent = compute_agent or ComputeAgent(
        phase_diagram_runtime=phase_diagram_runtime,
        lammps_runtime=lammps_runtime,
    )
    chat_agent = chat_agent or ChatAgent(artifact_service=artifact_service)
    agent_graph = AgentAppGraph(
        artifact_service=artifact_service,
        memory_store=memory_store,
        shared_memory_service=shared_memory_service,
        supervisor=supervisor_agent,
        recognition_agent=recognition_agent,
        compute_agent=compute_agent,
        chat_agent=chat_agent,
    )
    job_store = job_store or AgentJobStore(root_dir=settings.tmp_dir / "jobs")
    job_worker = job_worker or AgentJobWorker(store=job_store, runner=agent_graph.run_chat)
    return AppDependencies(
        artifact_service=artifact_service,
        memory_store=memory_store,
        shared_memory_service=shared_memory_service,
        job_store=job_store,
        job_worker=job_worker,
        supervisor_agent=supervisor_agent,
        recognition_agent=recognition_agent,
        phase_diagram_runtime=phase_diagram_runtime,
        lammps_runtime=lammps_runtime,
        compute_agent=compute_agent,
        chat_agent=chat_agent,
        agent_graph=agent_graph,
    )


app_dependencies = build_app_dependencies()
artifact_service = app_dependencies.artifact_service
memory_store = app_dependencies.memory_store
shared_memory_service = app_dependencies.shared_memory_service
job_store = app_dependencies.job_store
job_worker = app_dependencies.job_worker
supervisor_agent = app_dependencies.supervisor_agent
recognition_agent = app_dependencies.recognition_agent
phase_diagram_runtime = app_dependencies.phase_diagram_runtime
lammps_runtime = app_dependencies.lammps_runtime
compute_agent = app_dependencies.compute_agent
chat_agent = app_dependencies.chat_agent
agent_graph = app_dependencies.agent_graph


def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    deps = dependencies or build_app_dependencies()
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.state.dependencies = deps
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def on_startup() -> None:
        ensure_directory(settings.tmp_dir)
        ensure_directory(settings.tmp_dir / settings.runs_dir_name)
        ensure_directory(settings.tmp_dir / settings.memory_dir_name)
        ensure_directory(settings.tmp_dir / "jobs")
        deps.job_worker.start()

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        deps.job_worker.stop()

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app_name=settings.app_name,
            version=settings.app_version,
        )

    @app.get("/api/thermo/registry", response_model=ThermoRegistryResponse)
    def thermo_registry() -> ThermoRegistryResponse:
        cards = load_thermo_database_cards()
        return ThermoRegistryResponse(
            count=len(cards),
            systems=[ThermoRegistryEntry(**card.public_payload()) for card in cards],
        )

    @app.post("/api/thermo/rag/search", response_model=ThermoRagSearchResponse)
    def thermo_rag_search(request: ThermoRagSearchRequest) -> ThermoRagSearchResponse:
        payload = ThermoRagService.search(request.query, top_k=request.top_k)
        return ThermoRagSearchResponse(**payload)

    @app.get("/api/materials-rag/search")
    def materials_rag_search(
        q: str = Query(..., min_length=1),
        top_k: int = Query(5, ge=1, le=20),
        domain: str | None = None,
        doc_type: str | None = None,
        material: str | None = None,
    ) -> JSONResponse:
        return JSONResponse(
            MaterialsRagService.search_payload(
                q,
                domain=domain,
                doc_type=doc_type,
                material=material,
                top_k=top_k,
            )
        )

    @app.get("/api/rag/manager")
    def rag_manager_inventory() -> JSONResponse:
        return JSONResponse(RagDataManager().inventory().model_dump(mode="json"))

    @app.get("/api/rag/manager/search")
    def rag_manager_search(q: str = Query(..., min_length=1), top_k: int = Query(5, ge=1, le=10)) -> JSONResponse:
        return JSONResponse(RagDataManager.search(q, top_k=top_k).model_dump(mode="json"))

    @app.get("/api/system/diagnostics", response_model=SystemDiagnosticsResponse)
    def system_diagnostics() -> SystemDiagnosticsResponse:
        return build_system_diagnostics()

    @app.get("/api/tools/catalog")
    def tools_catalog() -> JSONResponse:
        catalog = deps.agent_graph.tool_router.registry.public_catalog()
        return JSONResponse({"count": len(catalog), "tools": catalog})

    @app.get("/api/skills/catalog")
    def skills_catalog() -> JSONResponse:
        catalog = deps.agent_graph.skill_router.registry.public_catalog()
        return JSONResponse({"count": len(catalog), "skills": catalog})

    @app.get("/api/lammps/registry")
    def lammps_registry() -> JSONResponse:
        return JSONResponse(get_lammps_registry_payload())

    @app.get("/api/runtimes/manager")
    def runtime_manager() -> JSONResponse:
        return JSONResponse(build_runtime_manager_report().model_dump(mode="json"))

    @app.get("/api/config/lammps")
    def get_lammps_config() -> JSONResponse:
        return JSONResponse(lammps_config_public_payload())

    @app.get("/api/config/llm")
    def get_llm_config() -> JSONResponse:
        return JSONResponse(llm_config_public_payload())

    @app.post("/api/config/llm")
    def update_llm_config(payload: dict[str, object]) -> JSONResponse:
        config = update_runtime_llm_config(payload)
        public = llm_config_public_payload()
        public["updated"] = True
        public["llm_enabled"] = config.llm_enabled
        public["require_llm_for_agents"] = config.require_llm_for_agents
        return JSONResponse(public)

    @app.post("/api/config/lammps")
    def update_lammps_config(payload: dict[str, object]) -> JSONResponse:
        config = update_runtime_lammps_config(payload)
        public = lammps_config_public_payload()
        public["updated"] = True
        public["allow_mock_fallback"] = config.allow_mock_fallback
        public["force_mock"] = config.force_mock
        return JSONResponse(public)

    @app.get("/api/runs")
    def list_runs() -> JSONResponse:
        records = deps.artifact_service.list_run_summaries()
        return JSONResponse({"count": len(records), "runs": [record.model_dump(mode="json") for record in records]})

    @app.get("/api/runs/{run_id}")
    def run_summary(run_id: str) -> JSONResponse:
        record = deps.artifact_service.load_run_summary(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return JSONResponse(record.model_dump(mode="json"))

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> JSONResponse:
        deleted = deps.artifact_service.delete_run(run_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Run not found.")
        log_event("artifact.run_deleted", run_id=run_id, message="Run artifact directory deleted.")
        return JSONResponse({"run_id": run_id, "deleted": True})

    @app.get("/api/artifacts/inventory")
    def artifact_inventory(limit: int = Query(500, ge=1, le=2000)) -> JSONResponse:
        report = deps.artifact_service.artifact_inventory(limit=limit)
        log_event("artifact.inventory", message="Artifact inventory generated.", run_count=report.get("run_count"), total_size_bytes=report.get("total_size_bytes"))
        return JSONResponse(report)

    @app.post("/api/artifacts/cleanup")
    def cleanup_artifacts(payload: dict[str, object]) -> JSONResponse:
        keep_latest = payload.get("keep_latest")
        max_age_days = payload.get("max_age_days")
        dry_run = str(payload.get("dry_run", True)).strip().lower() not in {"0", "false", "no", "off"}
        report = deps.artifact_service.cleanup_runs(
            keep_latest=int(keep_latest) if keep_latest is not None else None,
            max_age_days=int(max_age_days) if max_age_days is not None else None,
            dry_run=dry_run,
        )
        log_event(
            "artifact.cleanup",
            level="warning" if not dry_run else "info",
            message="Artifact cleanup evaluated.",
            dry_run=dry_run,
            candidate_count=report.get("candidate_count"),
            deleted_count=report.get("deleted_count"),
            reclaimed_bytes=report.get("reclaimed_bytes"),
        )
        return JSONResponse(report)

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> JSONResponse:
        deleted_runs = deps.artifact_service.delete_conversation(conversation_id)
        deleted_memory = deps.memory_store.delete(conversation_id)
        if deleted_runs == 0 and not deleted_memory:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return JSONResponse(
            {
                "conversation_id": conversation_id,
                "deleted": True,
                "deleted_runs": deleted_runs,
                "deleted_memory": deleted_memory,
            }
        )

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationSnapshotResponse)
    def get_conversation(conversation_id: str) -> ConversationSnapshotResponse:
        snapshot = deps.memory_store.load(conversation_id)
        latest_run = next(
            (record for record in deps.artifact_service.list_run_summaries() if record.conversation_id == conversation_id),
            None,
        )
        has_short_term = bool(
            snapshot.short_term.messages
            or snapshot.short_term.last_run_context.run_id
            or snapshot.short_term.recognition_result
            or snapshot.short_term.current_context_summary
        )
        has_long_term = bool(
            snapshot.long_term.strategic_summary
            or snapshot.long_term.salient_facts
            or snapshot.long_term.research_topics
            or snapshot.long_term.completed_run_summaries
            or snapshot.long_term.user_preferences
            or snapshot.long_term.open_questions
        )
        if not has_short_term and not has_long_term and latest_run is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return ConversationSnapshotResponse(
            conversation_id=conversation_id,
            short_term=snapshot.short_term,
            long_term=snapshot.long_term,
            latest_run=latest_run,
        )

    @app.get("/api/conversations/{conversation_id}/memory-profile")
    def get_conversation_memory_profile(conversation_id: str) -> JSONResponse:
        profile = deps.memory_store.profile(conversation_id)
        profile["shared_memory"] = deps.shared_memory_service.profile()
        return JSONResponse(profile)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_running_run(run_id: str) -> JSONResponse:
        record = deps.artifact_service.load_run_summary(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        cancel_run(run_id)
        return JSONResponse({"run_id": run_id, "status": "cancelled_requested"})

    @app.get("/api/runs/{run_id}/result", response_class=HTMLResponse)
    def run_result(run_id: str) -> HTMLResponse:
        html_content = deps.artifact_service.load_run_html(run_id)
        if html_content is None:
            raise HTTPException(status_code=404, detail="Run result not found.")
        return HTMLResponse(content=html_content)

    @app.get("/api/runs/{run_id}/artifacts/{artifact_name:path}")
    def run_artifact(run_id: str, artifact_name: str):
        artifact_path = deps.artifact_service.resolve_artifact_path(run_id, artifact_name)
        if artifact_path is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        media_type = deps.artifact_service.guess_media_type(artifact_path)
        return FileResponse(path=artifact_path, media_type=media_type, filename=artifact_path.name)

    @app.post("/api/agent/chat", response_model=AgentRunResponse)
    def agent_chat(request: AgentChatRequest) -> AgentRunResponse:
        log_event("api.chat_sync.received", request_id=request.request_id, conversation_id=request.conversation_id, message=request.message)
        return deps.agent_graph.run_chat(request)

    @app.post("/api/jobs/agent-chat", response_model=AgentJobRecord)
    def submit_agent_chat_job(request: AgentChatRequest) -> AgentJobRecord:
        deps.job_worker.start()
        record = deps.job_worker.submit_agent_chat(request)
        log_event(
            "api.job_submitted",
            request_id=request.request_id,
            job_id=record.job_id,
            conversation_id=request.conversation_id,
            message=request.message,
        )
        return record

    @app.get("/api/jobs", response_model=AgentJobListResponse)
    def list_jobs(
        limit: int = Query(50, ge=1, le=200),
        conversation_id: str | None = None,
    ) -> AgentJobListResponse:
        jobs = deps.job_store.list_recent(limit=limit, conversation_id=conversation_id)
        return AgentJobListResponse(count=len(jobs), jobs=jobs)

    @app.get("/api/jobs/{job_id}", response_model=AgentJobRecord)
    def get_job(job_id: str) -> AgentJobRecord:
        record = deps.job_store.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return record

    @app.get("/api/jobs/{job_id}/events")
    def stream_job_events(job_id: str, after: int = Query(0, ge=0)) -> StreamingResponse:
        if deps.job_store.get(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        def event_stream():
            last_event_id = after
            while True:
                emitted = False
                for event_record in deps.job_store.events_after(job_id, last_event_id):
                    last_event_id = event_record.event_id
                    emitted = True
                    event = event_record.event
                    yield (
                        f"id: {event_record.event_id}\n"
                        f"event: {event.type}\n"
                        f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                    )

                record = deps.job_store.get(job_id)
                if record is None:
                    break
                if record.status in TerminalJobStatus and not emitted:
                    break
                deps.job_worker.wait_for_events(timeout=1.0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}/result")
    def get_job_result(job_id: str) -> JSONResponse:
        record = deps.job_store.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if record.status not in TerminalJobStatus:
            return JSONResponse({"ready": False, "job": record.model_dump(mode="json"), "run": None})
        run_id = record.result_run_id or record.run_id
        run_record = deps.artifact_service.load_run_summary(run_id) if run_id else None
        if run_record is None:
            return JSONResponse({"ready": True, "job": record.model_dump(mode="json"), "run": None})
        return JSONResponse(
            {
                "ready": True,
                "job": record.model_dump(mode="json"),
                "run": run_record.model_dump(mode="json"),
            }
        )

    @app.post("/api/jobs/{job_id}/resume", response_model=AgentJobResumeResponse)
    def resume_job(job_id: str, request: AgentJobResumeRequest | None = None) -> AgentJobResumeResponse:
        source_job = deps.job_store.get(job_id)
        if source_job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if source_job.status not in TerminalJobStatus:
            raise HTTPException(status_code=409, detail="Only terminal jobs can be resumed as a new attempt.")
        source_request = deps.job_store.load_request(job_id)
        if source_request is None:
            raise HTTPException(status_code=409, detail="Original job request payload is missing.")

        resume_request = request or AgentJobResumeRequest()
        source_run_id = source_job.result_run_id or source_job.run_id
        source_run = deps.artifact_service.load_run_summary(source_run_id) if source_run_id else None
        source_summary = source_run.summary if source_run and isinstance(source_run.summary, dict) else {}
        source_metadata = source_run.metadata if source_run and isinstance(source_run.metadata, dict) else {}
        lifecycle = source_summary.get("lifecycle") or source_metadata.get("lifecycle") or {}
        partial_report = source_summary.get("partial_report") or source_metadata.get("partial_report") or {}
        preflight_dag = source_summary.get("preflight_dag") or source_metadata.get("preflight_dag") or {}
        degradation = {}
        if isinstance(preflight_dag, dict):
            metadata = preflight_dag.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("degradation"), dict):
                degradation = metadata["degradation"]
        checkpoint_id = resume_request.checkpoint_id.strip()
        if not checkpoint_id and isinstance(partial_report, dict):
            checkpoint_id = str(partial_report.get("last_checkpoint_id") or "").strip()
        if not checkpoint_id and isinstance(lifecycle, dict):
            checkpoint_id = str(lifecycle.get("last_checkpoint_id") or "").strip()
        failed_nodes: list[str] = []
        invalidated_nodes: list[str] = []
        reused_nodes: list[str] = []
        if isinstance(degradation, dict):
            invalidated_nodes = [str(item) for item in degradation.get("invalidated_nodes", []) if str(item).strip()] if isinstance(degradation.get("invalidated_nodes"), list) else []
            reused_nodes = [str(item) for item in degradation.get("reused_nodes", []) if str(item).strip()] if isinstance(degradation.get("reused_nodes"), list) else []
            failure_batch = degradation.get("failure_batch")
            if isinstance(failure_batch, dict):
                if isinstance(failure_batch.get("failed_nodes"), list):
                    failed_nodes = [str(item) for item in failure_batch["failed_nodes"] if str(item).strip()]
                elif isinstance(failure_batch.get("findings"), list):
                    failed_nodes = [
                        str(item.get("node_id"))
                        for item in failure_batch["findings"]
                        if isinstance(item, dict) and str(item.get("node_id") or "").strip()
                    ]

        default_message = "\n".join(
            [
                f"请基于上一轮 job_id={source_job.job_id} / run_id={source_run_id or 'unknown'} 创建新的恢复 attempt。",
                f"checkpoint_id={checkpoint_id or 'none'}；strategy={resume_request.strategy or 'checkpoint_context'}。",
                f"failed_nodes={', '.join(failed_nodes) if failed_nodes else 'none'}；invalidated_nodes={', '.join(invalidated_nodes) if invalidated_nodes else 'none'}；reused_nodes={', '.join(reused_nodes) if reused_nodes else 'auto'}。",
                "请复用安全的 preflight/checkpoint 上下文，但不要声称已经原地恢复真实 LAMMPS timestep；如果不能安全复用，请重新规划并说明原因。",
            ]
        )
        message = resume_request.message.strip() or default_message
        notes_suffix = (
            "\n\n[resume_context]\n"
            f"source_job_id={source_job.job_id}\n"
            f"source_run_id={source_run_id}\n"
            f"checkpoint_id={checkpoint_id}\n"
            f"resume_mode=new_attempt_with_checkpoint_context\n"
            "backend_resume_api_v1=true\n"
        )
        next_request = source_request.model_copy(
            update={
                "request_id": new_request_id(),
                "message": message,
                "notes": f"{source_request.notes}{notes_suffix}",
                "last_run_context": deps.agent_graph.last_run_context_from_record(source_run)
                if source_run is not None
                else source_request.last_run_context,
            }
        )
        deps.job_worker.start()
        resume_mode = "new_attempt_with_checkpoint_context"
        resumed_job = deps.job_worker.submit_agent_chat(
            next_request,
            job_type="agent_resume",
            attempt=source_job.attempt + 1,
            source_job_id=source_job.job_id,
            source_run_id=source_run_id,
            source_checkpoint_id=checkpoint_id,
            resume_mode=resume_mode,
        )
        log_event(
            "api.job_resume_submitted",
            request_id=resumed_job.request_id,
            job_id=resumed_job.job_id,
            conversation_id=resumed_job.conversation_id,
            message="Resume job submitted as a new attempt with checkpoint context.",
            source_job_id=source_job.job_id,
            source_run_id=source_run_id,
            checkpoint_id=checkpoint_id,
        )
        return AgentJobResumeResponse(
            source_job=source_job,
            resumed_job=resumed_job,
            source_run_id=source_run_id,
            source_run_available=source_run is not None,
            checkpoint_id=checkpoint_id,
            resume_mode=resume_mode,
            message="已创建新的恢复 attempt；旧 run 保持只读，真实 LAMMPS 不做危险的 timestep 原地续跑。",
        )

    @app.post("/api/jobs/{job_id}/cancel", response_model=AgentJobRecord)
    def cancel_job(job_id: str) -> AgentJobRecord:
        try:
            return deps.job_worker.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc

    @app.post("/api/agent/prompt-suggestion", response_model=PromptSuggestionResponse)
    def prompt_suggestion(request: PromptSuggestionRequest) -> PromptSuggestionResponse:
        snapshot = deps.memory_store.load(request.conversation_id)
        try:
            return deps.chat_agent.suggest_prompt(
                request=request,
                memory_snapshot=snapshot,
                recognition_result=snapshot.recognition_result,
            )
        except LLMRequiredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"动态 prompt 推荐失败：{exc}") from exc

    @app.post("/api/agent/chat/stream")
    def agent_chat_stream(request: AgentChatRequest) -> StreamingResponse:
        log_event("api.chat_stream.received", request_id=request.request_id, conversation_id=request.conversation_id, message=request.message)

        def event_stream():
            queue: Queue[AgentStreamEvent | None] = Queue()
            latest_run_id = "pending"

            def emit(event: AgentStreamEvent) -> None:
                nonlocal latest_run_id
                latest_run_id = event.run_id
                queue.put(event)

            def worker() -> None:
                try:
                    deps.agent_graph.run_chat(request, event_sink=emit)
                except Exception as exc:  # noqa: BLE001
                    queue.put(
                        AgentStreamEvent(
                            type="run_error",
                            run_id=latest_run_id,
                            payload={"message": str(exc)},
                        )
                    )
                finally:
                    queue.put(None)

            Thread(target=worker, daemon=True).start()

            while True:
                event = queue.get()
                if event is None:
                    break
                yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app(app_dependencies)
