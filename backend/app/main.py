import json
from queue import Queue
from threading import Thread

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config import settings
from app.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentChatRequest,
    AgentManifestResponse,
    AgentStreamEvent,
    AgentCatalogResponse,
    DiagramRequest,
    ExecutionResult,
    GenerateAndRunResponse,
    GenerateResponse,
    HealthResponse,
    ImageDiagramRequest,
    RunCodeRequest,
)
from app.services.agent_runtime import AgentRuntime
from app.services.agent_chat_service import AgentChatService
from app.services.agent_decision_service import AgentDecisionService
from app.services.agent_catalog import AgentCatalogService
from app.services.agent_manifest_service import AgentManifestService
from app.services.artifact_service import ArtifactService
from app.services.codegen_service import CodeGenerationService
from app.services.executor_service import LocalPythonExecutor
from app.services.phase_diagram_agent_service import PhaseDiagramAgentService
from app.services.phase_diagram_html_service import PhaseDiagramHtmlService
from app.services.phase_diagram_image_service import PhaseDiagramImageService
from app.services.planner_service import PlannerService
from app.services.prompt_builder import PromptBuilder
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry
from app.tools.load_latest_result_tool import LoadLatestResultTool
from app.tools.lammps_command_router_tool import LammpsCommandRouterTool
from app.tools.phase_diagram_codegen_tool import PhaseDiagramCodegenTool
from app.tools.phase_diagram_html_redraw_tool import PhaseDiagramHtmlRedrawTool
from app.tools.phase_diagram_html_review_tool import PhaseDiagramHtmlReviewTool
from app.tools.phase_diagram_image_parse_tool import PhaseDiagramImageParseTool
from app.tools.phase_diagram_image_render_tool import PhaseDiagramImageRenderTool
from app.tools.phase_diagram_repair_tool import PhaseDiagramRepairTool
from app.tools.phase_diagram_result_review_tool import PhaseDiagramResultReviewTool
from app.tools.python_execute_tool import PythonExecuteTool
from app.utils.file_utils import ensure_directory

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

artifact_service = ArtifactService(root_dir=settings.tmp_dir)
prompt_builder = PromptBuilder()
codegen_service = CodeGenerationService(prompt_builder=prompt_builder)
image_service = PhaseDiagramImageService()
html_service = PhaseDiagramHtmlService()
phase_agent_service = PhaseDiagramAgentService(codegen_service=codegen_service)
decision_service = AgentDecisionService()
executor = LocalPythonExecutor(
    artifact_service=artifact_service,
    python_executable=settings.python_executable,
)
catalog_service = AgentCatalogService()
manifest_service = AgentManifestService(catalog_service=catalog_service)
chat_service = AgentChatService(phase_agent_service=phase_agent_service)
tool_registry = ToolRegistry()
tool_registry.register(PhaseDiagramCodegenTool(codegen_service=codegen_service))
tool_registry.register(PhaseDiagramHtmlRedrawTool(artifact_service=artifact_service, html_service=html_service))
tool_registry.register(PhaseDiagramHtmlReviewTool(html_service=html_service))
tool_registry.register(PhaseDiagramResultReviewTool(phase_agent_service=phase_agent_service, html_service=html_service))
tool_registry.register(PhaseDiagramImageParseTool(image_service=image_service))
tool_registry.register(PhaseDiagramImageRenderTool(artifact_service=artifact_service, image_service=image_service))
tool_registry.register(PhaseDiagramRepairTool(codegen_service=codegen_service))
tool_registry.register(PythonExecuteTool(executor=executor))
tool_registry.register(LoadLatestResultTool(artifact_service=artifact_service))
tool_registry.register(LammpsCommandRouterTool())
task_router = TaskRouter(catalog_service=catalog_service, decision_service=decision_service)
planner_service = PlannerService()
agent_runtime = AgentRuntime(
    task_router=task_router,
    planner_service=planner_service,
    tool_registry=tool_registry,
    artifact_service=artifact_service,
)


@app.on_event("startup")
def on_startup() -> None:
    ensure_directory(settings.tmp_dir)
    ensure_directory(settings.tmp_dir / settings.runs_dir_name)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.app_version,
    )


@app.get("/api/latest-result", response_class=HTMLResponse)
def latest_result() -> HTMLResponse:
    tool_result = tool_registry.get("load_latest_html_artifact").run({}, {})
    if not tool_result.success:
        raise HTTPException(status_code=404, detail="No generated result available yet.")
    return HTMLResponse(content=tool_result.output["html_content"])


@app.get("/api/agent/catalog", response_model=AgentCatalogResponse)
def agent_catalog() -> AgentCatalogResponse:
    return catalog_service.build_catalog(tool_registry)


@app.get("/api/agent/manifest", response_model=AgentManifestResponse)
def agent_manifest() -> AgentManifestResponse:
    return manifest_service.build_manifest(tool_registry)


@app.get("/api/runs/{run_id}/result", response_class=HTMLResponse)
def run_result(run_id: str) -> HTMLResponse:
    html_content = artifact_service.load_run_html(run_id)
    if html_content is None:
        raise HTTPException(status_code=404, detail="Run result not found.")
    return HTMLResponse(content=html_content)


@app.post("/api/generate", response_model=GenerateResponse)
def generate_code(request: DiagramRequest) -> GenerateResponse:
    tool_result = tool_registry.get("phase_diagram_codegen").run({"diagram_request": request.model_dump()}, {})
    return GenerateResponse(success=tool_result.success, prompt=tool_result.output.get("prompt", ""), generated_code=tool_result.output.get("generated_code", ""))


@app.post("/api/run", response_model=ExecutionResult)
def run_code(request: RunCodeRequest) -> ExecutionResult:
    run_id = artifact_service.create_run_id()
    result = executor.execute(run_id=run_id, code=request.code)
    if result.success and result.html_content is not None:
        artifact_service.write_latest_html(result.html_content)
    return ExecutionResult(**result.to_dict())


@app.post("/api/agent/run", response_model=AgentRunResponse)
def agent_run(request: AgentRunRequest) -> AgentRunResponse:
    return agent_runtime.run(request)


@app.post("/api/agent/chat", response_model=AgentRunResponse)
def agent_chat(request: AgentChatRequest) -> AgentRunResponse:
    return agent_runtime.run(chat_service.build_run_request(request))


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
                agent_runtime.run(chat_service.build_run_request(request), event_sink=emit)
            except Exception as exc:
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
            yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(), ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/generate-and-run/stream")
def generate_and_run_stream(request: DiagramRequest) -> StreamingResponse:
    def event_stream():
        queue: Queue[AgentStreamEvent | None] = Queue()
        latest_run_id = "pending"

        def emit(event: AgentStreamEvent) -> None:
            nonlocal latest_run_id
            latest_run_id = event.run_id
            queue.put(event)

        def worker() -> None:
            try:
                agent_runtime.run(
                    AgentRunRequest(
                        user_input=f"Generate a phase diagram for {request.system_name}",
                        task_type_hint="phase_diagram.generate",
                        diagram_request=request,
                    ),
                    event_sink=emit,
                )
            except Exception as exc:
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
            yield f"event: {event.type}\ndata: {json.dumps(event.model_dump(), ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/generate-and-run", response_model=GenerateAndRunResponse)
def generate_and_run(request: DiagramRequest) -> GenerateAndRunResponse:
    agent_response = agent_runtime.run(
        AgentRunRequest(
            user_input=f"Generate a phase diagram for {request.system_name}",
            task_type_hint="phase_diagram.generate",
            diagram_request=request,
        )
    )

    return GenerateAndRunResponse(
        success=agent_response.success,
        stdout=agent_response.stdout,
        stderr=agent_response.stderr,
        html_content=agent_response.html_content,
        html_path=agent_response.html_path,
        generated_code=agent_response.generated_code or "",
        prompt=agent_response.metadata.get("prompt", ""),
        run_id=agent_response.run_id,
        route=agent_response.route.name,
        route_reason=agent_response.route.reason,
        workspace_id=agent_response.route.workspace_id,
        selected_tool=agent_response.route.selected_tool,
        entry_tool=agent_response.route.entry_tool,
        available_tools=agent_response.route.available_tools,
        reserved_tools=agent_response.route.reserved_tools,
        input_channels=agent_response.route.input_channels,
        deliverable=agent_response.route.deliverable,
        narrative=agent_response.route.narrative,
        plan_steps=agent_response.plan_steps,
        trace=agent_response.trace,
        termination_reason=agent_response.termination_reason,
    )


@app.post("/api/phase-diagram/from-image", response_model=GenerateAndRunResponse)
def generate_from_image(request: ImageDiagramRequest) -> GenerateAndRunResponse:
    agent_response = agent_runtime.run(
        AgentRunRequest(
            user_input=f"Create a calibrated phase diagram page from uploaded screenshot {request.filename or '(unnamed)'}",
            task_type_hint="phase_diagram.from_image",
            image_diagram_request=request,
        )
    )

    return GenerateAndRunResponse(
        success=agent_response.success,
        stdout=agent_response.stdout,
        stderr=agent_response.stderr,
        html_content=agent_response.html_content,
        html_path=agent_response.html_path,
        generated_code=agent_response.generated_code or "",
        prompt=agent_response.metadata.get("prompt", ""),
        run_id=agent_response.run_id,
        route=agent_response.route.name,
        route_reason=agent_response.route.reason,
        workspace_id=agent_response.route.workspace_id,
        selected_tool=agent_response.route.selected_tool,
        entry_tool=agent_response.route.entry_tool,
        available_tools=agent_response.route.available_tools,
        reserved_tools=agent_response.route.reserved_tools,
        input_channels=agent_response.route.input_channels,
        deliverable=agent_response.route.deliverable,
        narrative=agent_response.route.narrative,
        plan_steps=agent_response.plan_steps,
        trace=agent_response.trace,
        termination_reason=agent_response.termination_reason,
    )
