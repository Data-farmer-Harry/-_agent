from __future__ import annotations

from dataclasses import dataclass
import json
from queue import Queue
from threading import Thread

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from app.graph import AgentAppGraph
from app.agents.chat import ChatAgent
from app.agents.compute import ComputeAgent
from app.config import llm_config_public_payload, settings, update_runtime_llm_config
from app.core.cancellation import cancel_run
from app.diagnostics import build_system_diagnostics
from app.core.llm import LLMRequiredError
from app.memory import MemoryStore
from app.lammps import get_lammps_registry_payload, lammps_config_public_payload, update_runtime_lammps_config
from app.runtimes.lammps import LammpsRuntime
from app.runtimes.phase_diagram import PhaseDiagramRuntime
from app.agents.recognition import RecognitionAgent
from app.state import (
    AgentChatRequest,
    AgentRunResponse,
    AgentStreamEvent,
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
    supervisor_agent: SupervisorAgent | None = None,
    recognition_agent: RecognitionAgent | None = None,
    phase_diagram_runtime: PhaseDiagramRuntime | None = None,
    lammps_runtime: LammpsRuntime | None = None,
    compute_agent: ComputeAgent | None = None,
    chat_agent: ChatAgent | None = None,
) -> AppDependencies:
    artifact_service = artifact_service or ArtifactService(root_dir=settings.tmp_dir)
    memory_store = memory_store or MemoryStore(root_dir=settings.tmp_dir)
    supervisor_agent = supervisor_agent or SupervisorAgent()
    recognition_agent = recognition_agent or RecognitionAgent()
    phase_diagram_runtime = phase_diagram_runtime or PhaseDiagramRuntime(artifact_service=artifact_service)
    lammps_runtime = lammps_runtime or LammpsRuntime(artifact_service=artifact_service)
    compute_agent = compute_agent or ComputeAgent(
        phase_diagram_runtime=phase_diagram_runtime,
        lammps_runtime=lammps_runtime,
    )
    chat_agent = chat_agent or ChatAgent()
    agent_graph = AgentAppGraph(
        artifact_service=artifact_service,
        memory_store=memory_store,
        supervisor=supervisor_agent,
        recognition_agent=recognition_agent,
        compute_agent=compute_agent,
        chat_agent=chat_agent,
    )
    return AppDependencies(
        artifact_service=artifact_service,
        memory_store=memory_store,
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

    @app.get("/api/system/diagnostics", response_model=SystemDiagnosticsResponse)
    def system_diagnostics() -> SystemDiagnosticsResponse:
        return build_system_diagnostics()

    @app.get("/api/lammps/registry")
    def lammps_registry() -> JSONResponse:
        return JSONResponse(get_lammps_registry_payload())

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
        return JSONResponse({"run_id": run_id, "deleted": True})

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
        return deps.agent_graph.run_chat(request)

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
