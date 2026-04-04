from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.chat import ChatAgent
from app.agents.compute import ComputeAgent
from app.memory import MemoryStore
from app.agents.recognition import RecognitionAgent
from app.core.llm import LLMRequiredError
from app.state import (
    AgentChatRequest,
    AgentGraphState,
    AgentRunResponse,
    AgentStreamEvent,
    ArtifactRef,
    ConversationTurn,
    MemorySnapshot,
    PlanStep,
    TaskRoute,
    ToolObservation,
)
from app.agents.supervisor import SupervisorAgent
from app.core.artifacts import ArtifactService


class AgentAppGraph:
    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        memory_store: MemoryStore,
        supervisor: SupervisorAgent,
        recognition_agent: RecognitionAgent,
        compute_agent: ComputeAgent,
        chat_agent: ChatAgent,
    ) -> None:
        self.artifact_service = artifact_service
        self.memory_store = memory_store
        self.supervisor = supervisor
        self.recognition_agent = recognition_agent
        self.compute_agent = compute_agent
        self.chat_agent = chat_agent
        self.graph = self._build_graph()

    @staticmethod
    def _emit(state: AgentGraphState, event: AgentStreamEvent) -> None:
        sink = state.get("event_sink")
        if sink:
            sink(event)

    def _record_step(
        self,
        state: AgentGraphState,
        *,
        tool_name: str,
        success: bool,
        summary: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        description: str,
        stage: str,
    ) -> dict[str, Any]:
        step_index = len(state.get("plan_steps", [])) + 1
        step = PlanStep(
            index=step_index,
            tool_name=tool_name,
            input=input_data,
            status="running",
            retryable=False,
            description=description,
            stage=stage,
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
            artifacts=[],
        )
        plan_steps = [*state.get("plan_steps", []), final_step]
        trace = [*state.get("trace", []), observation]
        self._emit(
            state,
            AgentStreamEvent(
                type="step_completed" if success else "step_failed",
                run_id=state["run_id"],
                payload={"plan_step": final_step.model_dump(mode="json"), "observation": observation.model_dump(mode="json")},
            ),
        )
        return {"plan_steps": plan_steps, "trace": trace}

    def load_memory_node(self, state: AgentGraphState) -> dict[str, Any]:
        snapshot = self.memory_store.merge_request(state["request"])
        messages = [*snapshot.messages, ConversationTurn(role="user", content=state["request"].message)]
        updates = self._record_step(
            state,
            tool_name="load_memory",
            success=True,
            summary="已加载本轮会话记忆和上一轮上下文。",
            input_data={"conversation_id": state["conversation_id"]},
            output_data={"message_count": len(snapshot.messages), "has_last_run": bool(snapshot.last_run_context.run_id)},
            description="Load short-term memory and previous run context before routing.",
            stage="load_memory",
        )
        return {
            **updates,
            "memory_snapshot": snapshot,
            "messages": messages,
            "uploaded_assets": state["request"].uploaded_assets or snapshot.uploaded_assets,
            "last_run_context": state["request"].last_run_context if state["request"].last_run_context.run_id else snapshot.last_run_context,
            "recognition_result": snapshot.recognition_result,
            "current_context_summary": snapshot.current_context_summary,
        }

    def supervisor_node(self, state: AgentGraphState) -> dict[str, Any]:
        decision = self.supervisor.decide(state)
        route = self.supervisor.build_route(decision)
        updates = self._record_step(
            state,
            tool_name="SupervisorAgent",
            success=True,
            summary=f"Supervisor 已将本轮请求判定为 {route.name}。",
            input_data={"message": state["request"].message},
            output_data=decision,
            description="Route the request between chat, recognition, phase-diagram generation, and mixed flows.",
            stage="supervisor",
        )
        return {
            **updates,
            "user_intent": route.name,
            "next_step": str(decision.get("next_step") or "chat"),
            "compute_domain": route.compute_domain,
            "route": route,
            "supervisor_decision": decision,
        }

    def recognition_node(self, state: AgentGraphState) -> dict[str, Any]:
        result = self.recognition_agent.recognize(state)
        summary = f"RecognitionAgent 已完成截图结构化识别，system={result.system or 'unknown'}。"
        updates = self._record_step(
            state,
            tool_name="RecognitionAgent",
            success=True,
            summary=summary,
            input_data={"asset_count": len(state.get("uploaded_assets", []))},
            output_data=result.model_dump(mode="json"),
            description="Interpret the uploaded phase-diagram image into structured fields.",
            stage="recognition",
        )
        return {
            **updates,
            "recognition_result": result,
        }

    def compute_node(self, state: AgentGraphState) -> dict[str, Any]:
        result = self.compute_agent.run(state, state.get("supervisor_decision", {}))
        return result

    def chat_node(self, state: AgentGraphState) -> dict[str, Any]:
        chat_result = self.chat_agent.run(state)
        updates = self._record_step(
            state,
            tool_name="ChatAgent",
            success=True,
            summary="ChatAgent 已生成最终回复。",
            input_data={"message": state["request"].message},
            output_data={"answer_preview": chat_result["final_answer"][:300]},
            description="Generate the final dialog response using current results and prior context.",
            stage="chat",
        )
        return {
            **updates,
            "final_answer": chat_result["final_answer"],
            "messages": chat_result["messages"],
            "success": chat_result.get("success", True),
            "error": chat_result.get("error", ""),
        }

    def summarize_context_node(self, state: AgentGraphState) -> dict[str, Any]:
        summary = self.memory_store.summarize(state.get("messages", []), state.get("last_run_context"))
        updates = self._record_step(
            state,
            tool_name="summarize_context",
            success=True,
            summary="已更新当前对话摘要。",
            input_data={"message_count": len(state.get("messages", []))},
            output_data={"summary_length": len(summary)},
            description="Condense the conversation while preserving last-run context for follow-up turns.",
            stage="summarize",
        )
        return {**updates, "current_context_summary": summary}

    def save_memory_node(self, state: AgentGraphState) -> dict[str, Any]:
        snapshot = self.memory_store.build_next_snapshot(
            conversation_id=state["conversation_id"],
            messages=state.get("messages", []),
            uploaded_assets=state.get("uploaded_assets", []),
            recognition_result=state.get("recognition_result"),
            last_run_context=state.get("last_run_context"),
            current_context_summary=state.get("current_context_summary", ""),
        )
        path = self.memory_store.save(snapshot)
        updates = self._record_step(
            state,
            tool_name="save_memory",
            success=True,
            summary="已保存会话记忆。",
            input_data={"conversation_id": state["conversation_id"]},
            output_data={"path": str(path)},
            description="Persist the updated short-term memory snapshot for follow-up turns.",
            stage="save_memory",
        )
        return {**updates, "memory_snapshot": snapshot}

    def respond_node(self, state: AgentGraphState) -> dict[str, Any]:
        updates = self._record_step(
            state,
            tool_name="respond",
            success=True,
            summary="响应已整理完成，准备返回给前端。",
            input_data={"route": state["route"].name if state.get("route") else ""},
            output_data={"success": state.get("success", True)},
            description="Finalize the response payload after graph execution.",
            stage="respond",
        )
        return updates

    @staticmethod
    def _route_after_supervisor(state: AgentGraphState) -> str:
        next_step = state.get("next_step", "chat")
        if next_step == "recognition":
            return "recognition"
        if next_step == "compute":
            return "compute"
        return "chat"

    @staticmethod
    def _route_after_recognition(state: AgentGraphState) -> str:
        if state.get("user_intent") == "mixed.request":
            return "compute"
        return "chat"

    def _build_graph(self):
        graph = StateGraph(AgentGraphState)
        graph.add_node("load_memory", self.load_memory_node)
        graph.add_node("supervisor", self.supervisor_node)
        graph.add_node("recognition", self.recognition_node)
        graph.add_node("compute", self.compute_node)
        graph.add_node("chat", self.chat_node)
        graph.add_node("summarize_context", self.summarize_context_node)
        graph.add_node("save_memory", self.save_memory_node)
        graph.add_node("respond", self.respond_node)

        graph.add_edge(START, "load_memory")
        graph.add_edge("load_memory", "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            self._route_after_supervisor,
            {
                "recognition": "recognition",
                "compute": "compute",
                "chat": "chat",
            },
        )
        graph.add_conditional_edges(
            "recognition",
            self._route_after_recognition,
            {
                "compute": "compute",
                "chat": "chat",
            },
        )
        graph.add_edge("compute", "chat")
        graph.add_edge("chat", "summarize_context")
        graph.add_edge("summarize_context", "save_memory")
        graph.add_edge("save_memory", "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    def _build_response(self, state: AgentGraphState) -> AgentRunResponse:
        compute_response = state.get("phase_diagram_result") or state.get("lammps_result")
        route = state.get("route") or TaskRoute(name="conversation.answer", reason="")
        request_message = state["request"].message
        if compute_response is not None:
            metadata = {
                **compute_response.metadata,
                "current_context_summary": state.get("current_context_summary", ""),
                "runtime_final_message": compute_response.final_message,
                "chat_final_message": state.get("final_answer", ""),
            }
            summary = {"request_message": request_message, **compute_response.summary}
            return AgentRunResponse(
                success=bool(compute_response.success),
                run_id=state["run_id"],
                conversation_id=state["conversation_id"],
                route=route,
                final_message=state.get("final_answer") or compute_response.final_message,
                artifacts=compute_response.artifacts,
                plan_steps=state.get("plan_steps", []),
                trace=state.get("trace", []),
                generated_code=compute_response.generated_code,
                stdout=compute_response.stdout,
                stderr=compute_response.stderr,
                html_content=compute_response.html_content,
                html_path=compute_response.html_path,
                termination_reason=compute_response.termination_reason,
                metadata=metadata,
                recognition_result=state.get("recognition_result"),
                current_context_summary=state.get("current_context_summary", ""),
                summary=summary,
                run_status=compute_response.run_status,
            )

        return AgentRunResponse(
            success=bool(state.get("success", True)),
            run_id=state["run_id"],
            conversation_id=state["conversation_id"],
            route=route,
            final_message=state.get("final_answer", ""),
            artifacts=state.get("artifact_messages", []),
            plan_steps=state.get("plan_steps", []),
            trace=state.get("trace", []),
            generated_code=None,
            stdout="",
            stderr="",
            html_content=None,
            html_path=None,
            termination_reason=state.get("termination_reason", "conversation_answered"),
            metadata={"current_context_summary": state.get("current_context_summary", "")},
            recognition_result=state.get("recognition_result"),
            current_context_summary=state.get("current_context_summary", ""),
            summary={"request_message": request_message},
        )

    def run_chat(self, request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
        run_id = self.artifact_service.create_run_id()
        initial_route = TaskRoute(name="supervisor.dispatch", reason="The global 4-agent graph is starting.")
        initial_state: AgentGraphState = {
            "run_id": run_id,
            "conversation_id": request.conversation_id,
            "request": request,
            "messages": [],
            "uploaded_assets": request.uploaded_assets,
            "user_intent": "",
            "next_step": "",
            "compute_domain": "none",
            "route": initial_route,
            "recognition_result": None,
            "phase_diagram_result": None,
            "lammps_result": None,
            "last_run_context": request.last_run_context,
            "artifact_messages": [],
            "current_context_summary": "",
            "final_answer": "",
            "error": "",
            "success": True,
            "termination_reason": "",
            "response_metadata": {},
            "plan_steps": [],
            "trace": [],
            "event_sink": event_sink,
            "memory_snapshot": MemorySnapshot(conversation_id=request.conversation_id),
        }
        self._emit(
            initial_state,
            AgentStreamEvent(
                type="run_started",
                run_id=run_id,
                payload={"route": initial_route.model_dump(mode="json"), "message": request.message},
            ),
        )
        try:
            final_state = self.graph.invoke(initial_state)
            response = self._build_response(final_state)
        except Exception as exc:  # noqa: BLE001
            final_state = {
                **initial_state,
                "success": False,
                "error": str(exc),
                "termination_reason": "llm_required" if isinstance(exc, LLMRequiredError) else "graph_execution_failed",
                "final_answer": str(exc),
            }
            response = AgentRunResponse(
                success=False,
                run_id=run_id,
                conversation_id=request.conversation_id,
                route=final_state["route"],
                final_message=str(exc),
                artifacts=final_state.get("artifact_messages", []),
                plan_steps=final_state.get("plan_steps", []),
                trace=final_state.get("trace", []),
                generated_code=None,
                stdout="",
                stderr="",
                html_content=None,
                html_path=None,
                termination_reason=final_state["termination_reason"],
                metadata={
                    "current_context_summary": final_state.get("current_context_summary", ""),
                    "error_type": exc.__class__.__name__,
                },
                recognition_result=final_state.get("recognition_result"),
                current_context_summary=final_state.get("current_context_summary", ""),
            )
            self._emit(
                final_state,
                AgentStreamEvent(
                    type="run_error",
                    run_id=run_id,
                    payload={
                        "message": str(exc),
                        "termination_reason": final_state["termination_reason"],
                        "error_type": exc.__class__.__name__,
                    },
                ),
            )
        self._emit(
            final_state,
            AgentStreamEvent(
                type="run_completed",
                run_id=run_id,
                payload={"response": response.model_dump(mode="json", exclude={"html_content"})},
            ),
        )
        self.artifact_service.write_run_summary(response)
        return response
