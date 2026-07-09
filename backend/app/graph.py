from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.chat import ChatAgent
from app.agents.compute import ComputeAgent
from app.config import settings
from app.memory import MemoryStore
from app.shared_memory import SharedMemoryService
from app.shared_memory.agent_integration import (
    build_lammps_execution_fact_items,
    build_materials_rag_evidence_items,
    build_run_rag_evidence_items,
    build_user_constraint_items,
    conversation_scope,
    write_results_metadata,
)
from app.agents.recognition import RecognitionAgent
from app.core.agent_protocol import build_agent_envelope, summarize_protocol_messages
from app.core.llm import LLMRequiredError
from app.core.observability import log_event, new_request_id
from app.state import (
    AgentChatRequest,
    AgentGraphState,
    AgentRunResponse,
    AgentStreamEvent,
    ArtifactRef,
    ConversationTurn,
    LastRunContext,
    MemorySnapshot,
    PlanStep,
    RunRecordSummary,
    TaskRoute,
    ToolObservation,
)
from app.agents.supervisor import SupervisorAgent
from app.core.artifacts import ArtifactService
from app.skills import SkillRouter, build_default_skill_registry
from app.tools import ToolExecutor, ToolRouter, build_default_tool_registry


def retrieval_metadata_like(retrieval: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(retrieval, dict):
        return {"available": False}
    if retrieval.get("available") is False:
        return {"available": False, "error": retrieval.get("error", "")}
    return {
        "available": True,
        "backend": retrieval.get("retrieval_backend", ""),
        "scope_filter": retrieval.get("scope_filter", []),
        "selected_item_ids": retrieval.get("selected_item_ids", []),
        "forced_retention_ids": retrieval.get("forced_retention_ids", []),
        "dropped_reasons": retrieval.get("dropped_reasons", {}),
        "estimated_before_bytes": retrieval.get("estimated_before_bytes", 0),
        "estimated_after_bytes": retrieval.get("estimated_after_bytes", 0),
    }


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
        shared_memory_service: SharedMemoryService | None = None,
        tool_router: ToolRouter | None = None,
        tool_executor: ToolExecutor | None = None,
        skill_router: SkillRouter | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.memory_store = memory_store
        memory_root = getattr(getattr(memory_store, "paths", None), "root_dir", None)
        self.shared_memory = shared_memory_service or SharedMemoryService(
            root_dir=memory_root or (settings.tmp_dir / settings.memory_dir_name)
        )
        self.supervisor = supervisor
        self.recognition_agent = recognition_agent
        self.compute_agent = compute_agent
        self.chat_agent = chat_agent
        tool_registry = build_default_tool_registry()
        self.tool_router = tool_router or ToolRouter(tool_registry)
        self.tool_executor = tool_executor or ToolExecutor(tool_registry)
        self.skill_router = skill_router or SkillRouter(build_default_skill_registry())
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
        artifacts: list[ArtifactRef] | None = None,
    ) -> dict[str, Any]:
        envelope = build_agent_envelope(
            run_id=state["run_id"],
            conversation_id=state.get("conversation_id", "default"),
            sender=tool_name,
            receiver="AgentGraph",
            message_type=f"{stage}.observation",
            payload_schema=f"{tool_name}.{stage}.v1",
            payload={
                "input": input_data,
                "output": output_data,
                "success": success,
                "summary": summary,
            },
        )
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
            AgentStreamEvent(
                type="step_started",
                run_id=state["run_id"],
                payload={"plan_step": step.model_dump(mode="json"), "request_id": state.get("request_id", "")},
            ),
        )
        log_event(
            "agent.step_started",
            request_id=state.get("request_id", ""),
            run_id=state["run_id"],
            conversation_id=state.get("conversation_id", "default"),
            message=description,
            tool_name=tool_name,
            step_index=step_index,
            stage=stage,
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
            metadata={"agent_protocol": envelope.public_metadata()},
        )
        plan_steps = [*state.get("plan_steps", []), final_step]
        trace = [*state.get("trace", []), observation]
        self._emit(
            state,
            AgentStreamEvent(
                type="step_completed" if success else "step_failed",
                run_id=state["run_id"],
                payload={
                    "plan_step": final_step.model_dump(mode="json"),
                    "observation": observation.model_dump(mode="json"),
                    "request_id": state.get("request_id", ""),
                },
            ),
        )
        log_event(
            "agent.step_completed" if success else "agent.step_failed",
            level="info" if success else "warning",
            request_id=state.get("request_id", ""),
            run_id=state["run_id"],
            conversation_id=state.get("conversation_id", "default"),
            message=summary,
            tool_name=tool_name,
            step_index=step_index,
            stage=stage,
            success=success,
        )
        return {
            "plan_steps": plan_steps,
            "trace": trace,
            "protocol_messages": [*state.get("protocol_messages", []), envelope],
        }

    @staticmethod
    def _is_actionable_last_run_context(context: LastRunContext | None) -> bool:
        if context is None or not context.run_id:
            return False
        if context.compute_domain in {"phase_diagram", "lammps"}:
            return True
        return context.route_name in {"recognition.analyze", "mixed.request"}

    @staticmethod
    def _request_summary_from_record(record: RunRecordSummary) -> str:
        summary = record.summary if isinstance(record.summary, dict) else {}
        if record.route.compute_domain == "phase_diagram" or record.route.name in {"phase_diagram.generate", "mixed.request"}:
            system_name = str(summary.get("system_name") or "").strip()
            diagram_type = str(summary.get("diagram_type") or "").strip()
            temperature_range = summary.get("temperature_range")
            if (
                isinstance(temperature_range, (list, tuple))
                and len(temperature_range) >= 2
                and system_name
            ):
                low, high = temperature_range[0], temperature_range[1]
                return f"{system_name} {diagram_type} {low}-{high} K".strip()
            if system_name:
                return " ".join(part for part in [system_name, diagram_type] if part).strip()

        request_message = str(summary.get("request_message") or "").strip()
        return request_message[:240]

    @classmethod
    def _last_run_context_from_record(cls, record: RunRecordSummary) -> LastRunContext:
        summary = record.summary if isinstance(record.summary, dict) else {}
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
        compute_domain = str(record.route.compute_domain or "none")
        if compute_domain not in {"phase_diagram", "lammps", "none"}:
            compute_domain = "none"
        if compute_domain == "none":
            if record.route.name in {"phase_diagram.generate", "mixed.request"}:
                compute_domain = "phase_diagram"
            elif record.route.name == "lammps.generate":
                compute_domain = "lammps"
        return LastRunContext(
            run_id=record.run_id,
            route_name=record.route.name,
            compute_domain=compute_domain,
            system_name=str(summary.get("system_name") or ""),
            final_message=record.final_message[:1200],
            generated_code_preview="",
            review_summary=str(review.get("summary") or ""),
            selected_tool=record.route.selected_tool or "",
            generation_source=str(metadata.get("generation_source") or summary.get("generation_source") or metadata.get("run_mode") or ""),
            request_summary=cls._request_summary_from_record(record),
            review_passed=review.get("passed") if isinstance(review.get("passed"), bool) else None,
            review_issues=[str(item) for item in review.get("issues", [])] if isinstance(review.get("issues"), list) else [],
            review_advisory_issues=[str(item) for item in review.get("advisory_issues", [])]
            if isinstance(review.get("advisory_issues"), list)
            else [],
            trace_summary=[f"{item.tool_name}: {item.summary}" for item in record.trace[-8:]],
            recognition_summary="",
            artifact_names=[artifact.name for artifact in record.artifacts],
        )

    @classmethod
    def last_run_context_from_record(cls, record: RunRecordSummary) -> LastRunContext:
        return cls._last_run_context_from_record(record)

    def _resolve_last_run_context(
        self,
        *,
        conversation_id: str,
        request_context: LastRunContext,
        snapshot_context: LastRunContext,
    ) -> LastRunContext:
        if self._is_actionable_last_run_context(request_context):
            return request_context
        if self._is_actionable_last_run_context(snapshot_context):
            return snapshot_context

        for record in self.artifact_service.list_run_summaries(limit=200):
            if record.conversation_id != conversation_id:
                continue
            if record.route.name == "conversation.answer":
                continue
            return self._last_run_context_from_record(record)

        if snapshot_context.run_id:
            return snapshot_context
        if request_context.run_id:
            return request_context
        return LastRunContext()

    def _retrieve_shared_memory(self, *, conversation_id: str, query: str, top_k: int = 8) -> tuple[dict[str, Any], str]:
        try:
            result = self.shared_memory.retrieve(
                query=query,
                scope=conversation_scope(conversation_id, include_global=True),
                top_k=top_k,
                prompt_budget_bytes=12_288,
            )
            return result.model_dump(mode="json"), ""
        except Exception as exc:  # noqa: BLE001 - shared memory must never block core agent execution.
            return {"available": False, "error": str(exc)}, str(exc)

    def _write_shared_memory_items(self, items) -> tuple[list[dict[str, Any]], str]:
        try:
            results = [self.shared_memory.write(item) for item in items]
            return write_results_metadata(results), ""
        except Exception as exc:  # noqa: BLE001 - degrade to old MemoryStore on shared-memory failures.
            return [{"error": str(exc)}], str(exc)

    @staticmethod
    def _shared_memory_response_metadata(
        *,
        retrieval: dict[str, Any] | None = None,
        writes: list[dict[str, Any]] | None = None,
        stage: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"stage": stage}
        if retrieval is not None:
            payload["retrieval"] = retrieval_metadata_like(retrieval)
        if writes is not None:
            payload["writes"] = writes
            valid_writes = [item for item in writes if not item.get("error")]
            payload["write_count"] = len(valid_writes)
            payload["conflict_count"] = sum(len(item.get("conflict_ids") or []) for item in valid_writes)
            payload["needs_user_count"] = len([item for item in valid_writes if item.get("needs_user")])
            payload["quarantined_count"] = len([item for item in valid_writes if item.get("quarantined")])
            payload["conflicted_count"] = len([item for item in valid_writes if item.get("conflicted")])
            payload["unsafe_write_count"] = payload["quarantined_count"] + payload["conflicted_count"]
        if error:
            payload["error"] = error
        return {"shared_memory": payload}

    def load_memory_node(self, state: AgentGraphState) -> dict[str, Any]:
        snapshot = self.memory_store.merge_request(state["request"])
        resolved_last_run_context = self._resolve_last_run_context(
            conversation_id=state["conversation_id"],
            request_context=state["request"].last_run_context,
            snapshot_context=snapshot.last_run_context,
        )
        messages = [*snapshot.messages, ConversationTurn(role="user", content=state["request"].message)]
        shared_retrieval, shared_error = self._retrieve_shared_memory(
            conversation_id=state["conversation_id"],
            query=state["request"].message,
        )
        updates = self._record_step(
            state,
            tool_name="load_memory",
            success=True,
            summary="已加载本轮会话记忆和上一轮上下文。",
            input_data={"conversation_id": state["conversation_id"]},
            output_data={
                "message_count": len(snapshot.messages),
                "has_last_run": bool(resolved_last_run_context.run_id),
                "long_term_topics": snapshot.long_term.research_topics[:6],
                "shared_memory_selected": len(shared_retrieval.get("selected_item_ids", [])) if isinstance(shared_retrieval, dict) else 0,
                "shared_memory_error": shared_error,
            },
            description="Load short-term memory and previous run context before routing.",
            stage="load_memory",
        )
        long_term_hits = self.memory_store.retrieve_long_term_context(
            query=state["request"].message,
            snapshot=snapshot,
            conversation_id=state["conversation_id"],
            limit=6,
        )
        return {
            **updates,
            "memory_snapshot": snapshot,
            "messages": messages,
            "uploaded_assets": state["request"].uploaded_assets or snapshot.uploaded_assets,
            "last_run_context": resolved_last_run_context,
            "recognition_result": snapshot.recognition_result,
            "current_context_summary": snapshot.current_context_summary,
            "long_term_memory_hits": long_term_hits,
            "shared_memory_context": shared_retrieval,
            "shared_memory_events": [
                *state.get("shared_memory_events", []),
                {"stage": "load_memory", "retrieval": retrieval_metadata_like(shared_retrieval), "error": shared_error},
            ],
            "response_metadata": {
                **state.get("response_metadata", {}),
                **self._shared_memory_response_metadata(retrieval=shared_retrieval, stage="load_memory", error=shared_error),
            },
        }

    def supervisor_node(self, state: AgentGraphState) -> dict[str, Any]:
        decision = self.supervisor.decide(state)
        route = self.supervisor.build_route(decision)
        shared_writes, write_error = self._write_shared_memory_items(
            build_user_constraint_items(
                request=state["request"],
                route=route,
                decision=decision,
                run_id=state["run_id"],
            )
        )
        shared_retrieval, retrieval_error = self._retrieve_shared_memory(
            conversation_id=state["conversation_id"],
            query=state["request"].message,
        )
        updates = self._record_step(
            state,
            tool_name="SupervisorAgent",
            success=True,
            summary=f"Supervisor 已将本轮请求判定为 {route.name}。",
            input_data={"message": state["request"].message},
            output_data={
                **decision,
                "shared_memory_write_count": len([item for item in shared_writes if not item.get("error")]),
                "shared_memory_selected": len(shared_retrieval.get("selected_item_ids", [])) if isinstance(shared_retrieval, dict) else 0,
                "shared_memory_error": write_error or retrieval_error,
            },
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
            "shared_memory_context": shared_retrieval,
            "shared_memory_events": [
                *state.get("shared_memory_events", []),
                {
                    "stage": "supervisor",
                    "writes": shared_writes,
                    "retrieval": retrieval_metadata_like(shared_retrieval),
                    "error": write_error or retrieval_error,
                },
            ],
            "response_metadata": {
                **state.get("response_metadata", {}),
                **self._shared_memory_response_metadata(
                    retrieval=shared_retrieval,
                    writes=shared_writes,
                    stage="supervisor",
                    error=write_error or retrieval_error,
                ),
            },
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
        next_state: dict[str, Any] = {
            **updates,
            "recognition_result": result,
        }
        if state.get("user_intent") == "recognition.analyze" and self._should_build_recognition_simulator(state):
            simulator_bundle = self.recognition_agent.build_simulation_bundle(state=state, recognition_result=result)
            simulator_updates = self._record_step(
                {**state, **next_state},
                tool_name="RecognitionAgent.simulator",
                success=True,
                summary="RecognitionAgent 已生成可交互的相图模拟器。",
                input_data={"recognized_system": result.system, "critical_point_count": len(result.critical_points)},
                output_data={
                    "html_path": simulator_bundle.html_path,
                    "artifact_count": len(simulator_bundle.artifacts),
                    "category": simulator_bundle.result_profile.category,
                },
                description="Turn the recognized phase-diagram structure into an interactive temperature/pressure simulator.",
                stage="recognition_simulator",
            )
            next_state.update(
                {
                    **simulator_updates,
                    "artifact_messages": simulator_bundle.artifacts,
                    "html_content": simulator_bundle.html_content,
                    "html_path": simulator_bundle.html_path,
                    "response_metadata": {
                        **state.get("response_metadata", {}),
                        **simulator_bundle.metadata,
                    },
                    "response_summary": {
                        **state.get("response_summary", {}),
                        **simulator_bundle.summary,
                    },
                }
            )
        return next_state

    @staticmethod
    def _should_build_recognition_simulator(state: AgentGraphState) -> bool:
        decision = state.get("supervisor_decision") or {}
        if str(decision.get("intent") or "").strip() == "recognize_image_to_interactive_simulator":
            return True

        message = state["request"].message
        lowered = message.lower()
        html_hints = (
            "交互式html",
            "交互式 html",
            "交互式页面",
            "交互式结果",
            "interactive html",
            "result.html",
            "html文件",
            "html render",
            "重构",
            "复原成html",
            "渲染成html",
        )
        return any(token in lowered or token in message for token in html_hints)

    def compute_node(self, state: AgentGraphState) -> dict[str, Any]:
        result = self.compute_agent.run(state, state.get("supervisor_decision", {}))
        compute_response = result.get("lammps_result") or result.get("phase_diagram_result")
        shared_writes: list[dict[str, Any]] = []
        write_error = ""
        if compute_response is not None and getattr(compute_response, "route", None) is not None:
            shared_items = build_run_rag_evidence_items(response=compute_response)
            if compute_response.route.compute_domain == "lammps" or compute_response.route.name == "lammps.generate":
                shared_items = [*build_lammps_execution_fact_items(response=compute_response), *shared_items]
            shared_writes, write_error = self._write_shared_memory_items(shared_items)
        shared_retrieval, retrieval_error = self._retrieve_shared_memory(
            conversation_id=state["conversation_id"],
            query=state["request"].message,
        )
        result["shared_memory_context"] = shared_retrieval
        result["shared_memory_events"] = [
            *state.get("shared_memory_events", []),
            {
                "stage": "compute",
                "writes": shared_writes,
                "retrieval": retrieval_metadata_like(shared_retrieval),
                "error": write_error or retrieval_error,
            },
        ]
        result["response_metadata"] = {
            **state.get("response_metadata", {}),
            **result.get("response_metadata", {}),
            **self._shared_memory_response_metadata(
                retrieval=shared_retrieval,
                writes=shared_writes,
                stage="compute",
                error=write_error or retrieval_error,
            ),
        }
        return result

    def _run_tool_policy(self, state: AgentGraphState) -> dict[str, Any]:
        decision = self.tool_router.decide(state)
        decision_payload = decision.model_dump()
        if not decision.need_tool:
            return {
                "tool_decision": decision_payload,
                "response_metadata": {
                    **state.get("response_metadata", {}),
                    "tool_policy": decision_payload,
                    "tool_results": [],
                },
            }

        working_state: AgentGraphState = {
            **state,
            "tool_results": list(state.get("tool_results", [])),
            "artifact_messages": list(state.get("artifact_messages", [])),
        }
        tool_results: list[dict[str, Any]] = []
        for call in decision.selected_calls:
            if call.requires_confirmation or not call.auto_execute:
                result = self.tool_executor.execute(call, self.tool_executor.build_context(working_state, self.artifact_service))
            else:
                result = self.tool_executor.execute(call, self.tool_executor.build_context(working_state, self.artifact_service))
            result_payload = result.model_dump()
            tool_results.append(result_payload)
            updated_artifacts = [*working_state.get("artifact_messages", []), *result.artifacts]
            step_updates = self._record_step(
                working_state,
                tool_name=call.tool_name,
                success=result.success,
                summary=result.summary,
                input_data=call.arguments,
                output_data=result.output,
                description=f"Execute generic tool {call.tool_name} selected by ToolPolicy.",
                stage="tool_call",
                artifacts=result.artifacts,
            )
            working_state = {
                **working_state,
                **step_updates,
                "artifact_messages": updated_artifacts,
                "tool_results": [*working_state.get("tool_results", []), result_payload],
            }

        return {
            "plan_steps": working_state.get("plan_steps", []),
            "trace": working_state.get("trace", []),
            "protocol_messages": working_state.get("protocol_messages", []),
            "artifact_messages": working_state.get("artifact_messages", []),
            "tool_results": working_state.get("tool_results", []),
            "tool_decision": decision_payload,
            "response_metadata": {
                **state.get("response_metadata", {}),
                "tool_policy": decision_payload,
                "tool_results": tool_results,
            },
            "response_summary": {
                **state.get("response_summary", {}),
                "tool_policy": {
                    "need_tool": decision.need_tool,
                    "selected_tools": [call.tool_name for call in decision.selected_calls],
                },
            },
        }

    def _run_skill_router(self, state: AgentGraphState) -> dict[str, Any]:
        decision = self.skill_router.decide(state)
        context = self.skill_router.build_context(decision)
        payload = decision.model_dump()
        return {
            "skill_decision": payload,
            "skill_context": context,
            "response_metadata": {
                **state.get("response_metadata", {}),
                "skill_policy": payload,
            },
            "response_summary": {
                **state.get("response_summary", {}),
                "skills": [skill["skill_id"] for skill in payload.get("selected_skills", [])],
            },
        }

    def chat_node(self, state: AgentGraphState) -> dict[str, Any]:
        skill_updates = self._run_skill_router(state)
        state = {**state, **skill_updates}
        tool_updates = self._run_tool_policy(state)
        state = {**state, **tool_updates}
        chat_result = self.chat_agent.run(state)
        rag_evidence = chat_result.get("rag_evidence") if isinstance(chat_result.get("rag_evidence"), dict) else {}
        shared_writes: list[dict[str, Any]] = []
        write_error = ""
        if rag_evidence and rag_evidence.get("kind") == "materials_rag":
            shared_writes, write_error = self._write_shared_memory_items(
                build_materials_rag_evidence_items(
                    conversation_id=state["conversation_id"],
                    query=str(rag_evidence.get("query") or state["request"].message),
                    hits=list(rag_evidence.get("hits") or []),
                    run_id=state["run_id"],
                    stage="chat_materials_rag",
                    domain=rag_evidence.get("domain"),
                    doc_type=rag_evidence.get("doc_type"),
                    material=rag_evidence.get("material"),
                )
            )
        shared_retrieval, retrieval_error = self._retrieve_shared_memory(
            conversation_id=state["conversation_id"],
            query=state["request"].message,
        )
        updates = self._record_step(
            state,
            tool_name="ChatAgent",
            success=True,
            summary="ChatAgent 已生成最终回复。",
            input_data={"message": state["request"].message},
            output_data={
                "answer_preview": chat_result["final_answer"][:300],
                "shared_memory_write_count": len([item for item in shared_writes if not item.get("error")]),
                "shared_memory_selected": len(shared_retrieval.get("selected_item_ids", [])) if isinstance(shared_retrieval, dict) else 0,
                "shared_memory_error": write_error or retrieval_error,
            },
            description="Generate the final dialog response using current results and prior context.",
            stage="chat",
        )
        return {
            **updates,
            "final_answer": chat_result["final_answer"],
            "messages": chat_result["messages"],
            "success": chat_result.get("success", True),
            "error": chat_result.get("error", ""),
            "artifact_messages": chat_result.get("artifact_messages") or state.get("artifact_messages", []),
            "html_content": chat_result.get("html_content") or state.get("html_content", ""),
            "html_path": chat_result.get("html_path") or state.get("html_path", ""),
            "shared_memory_context": shared_retrieval,
            "shared_memory_events": [
                *state.get("shared_memory_events", []),
                {
                    "stage": "chat",
                    "writes": shared_writes,
                    "retrieval": retrieval_metadata_like(shared_retrieval),
                    "error": write_error or retrieval_error,
                },
            ],
            "response_metadata": {
                **state.get("response_metadata", {}),
                **chat_result.get("response_metadata", {}),
                **self._shared_memory_response_metadata(
                    retrieval=shared_retrieval,
                    writes=shared_writes,
                    stage="chat",
                    error=write_error or retrieval_error,
                ),
            },
            "response_summary": {
                **state.get("response_summary", {}),
                **chat_result.get("response_summary", {}),
            },
            "termination_reason": (
                chat_result.get("termination_reason")
                if chat_result.get("artifact_messages")
                or chat_result.get("html_content")
                or chat_result.get("termination_reason") != "conversation_answered"
                else state.get("termination_reason", "conversation_answered")
            ),
        }

    def summarize_context_node(self, state: AgentGraphState) -> dict[str, Any]:
        previous_long_term = state.get("memory_snapshot").long_term if state.get("memory_snapshot") else None
        summary = self.memory_store.summarize(
            state.get("messages", []),
            state.get("last_run_context"),
            recognition_result=state.get("recognition_result"),
            previous_long_term=previous_long_term,
            conversation_id=state["conversation_id"],
        )
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
            previous_snapshot=state.get("memory_snapshot"),
        )
        saved_paths = self.memory_store.save(snapshot)
        updates = self._record_step(
            state,
            tool_name="save_memory",
            success=True,
            summary="已保存会话记忆。",
            input_data={"conversation_id": state["conversation_id"]},
            output_data={key: str(value) for key, value in saved_paths.items()},
            description="Persist both short-term and long-term memory snapshots for follow-up turns.",
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
        protocol_summary = summarize_protocol_messages(state.get("protocol_messages", []))
        if compute_response is not None:
            metadata = {
                **compute_response.metadata,
                **state.get("response_metadata", {}),
                "request_id": state.get("request_id", ""),
                "current_context_summary": state.get("current_context_summary", ""),
                "runtime_final_message": compute_response.final_message,
                "chat_final_message": state.get("final_answer", ""),
                "agent_protocol": protocol_summary,
                "shared_memory_events": state.get("shared_memory_events", [])[-8:],
            }
            summary = {"request_message": request_message, "request_id": state.get("request_id", ""), **compute_response.summary}
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
            html_content=state.get("html_content"),
            html_path=state.get("html_path"),
            termination_reason=state.get("termination_reason", "conversation_answered"),
            metadata={
                **state.get("response_metadata", {}),
                "request_id": state.get("request_id", ""),
                "current_context_summary": state.get("current_context_summary", ""),
                "agent_protocol": protocol_summary,
                "shared_memory_events": state.get("shared_memory_events", [])[-8:],
            },
            recognition_result=state.get("recognition_result"),
            current_context_summary=state.get("current_context_summary", ""),
            summary={"request_message": request_message, "request_id": state.get("request_id", ""), **state.get("response_summary", {})},
        )

    def run_chat(self, request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
        run_id = self.artifact_service.create_run_id()
        request_id = request.request_id or new_request_id()
        initial_route = TaskRoute(name="supervisor.dispatch", reason="The global 4-agent graph is starting.")
        initial_state: AgentGraphState = {
            "run_id": run_id,
            "request_id": request_id,
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
            "html_content": "",
            "html_path": "",
            "current_context_summary": "",
            "final_answer": "",
            "error": "",
            "success": True,
            "termination_reason": "",
            "response_metadata": {},
            "response_summary": {},
            "plan_steps": [],
            "trace": [],
            "event_sink": event_sink,
            "memory_snapshot": MemorySnapshot(conversation_id=request.conversation_id),
            "shared_memory_context": {},
            "shared_memory_events": [],
            "protocol_messages": [],
            "tool_decision": {},
            "tool_results": [],
            "skill_decision": {},
            "skill_context": "",
        }
        self._emit(
            initial_state,
            AgentStreamEvent(
                type="run_started",
                run_id=run_id,
                payload={"route": initial_route.model_dump(mode="json"), "message": request.message, "request_id": request_id},
            ),
        )
        log_event(
            "run.started",
            request_id=request_id,
            run_id=run_id,
            conversation_id=request.conversation_id,
            message=request.message,
            route_name=initial_route.name,
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
                html_content=final_state.get("html_content"),
                html_path=final_state.get("html_path"),
                termination_reason=final_state["termination_reason"],
                metadata={
                    **final_state.get("response_metadata", {}),
                    "current_context_summary": final_state.get("current_context_summary", ""),
                    "error_type": exc.__class__.__name__,
                    "request_id": request_id,
                    "shared_memory_events": final_state.get("shared_memory_events", [])[-8:],
                },
                recognition_result=final_state.get("recognition_result"),
                current_context_summary=final_state.get("current_context_summary", ""),
                summary={"request_message": request.message, "request_id": request_id, **final_state.get("response_summary", {})},
            )
            self._emit(
                final_state,
                AgentStreamEvent(
                    type="run_error",
                    run_id=run_id,
                    payload={
                        "request_id": request_id,
                        "message": str(exc),
                        "termination_reason": final_state["termination_reason"],
                        "error_type": exc.__class__.__name__,
                    },
                ),
            )
            log_event(
                "run.failed",
                level="error",
                request_id=request_id,
                run_id=run_id,
                conversation_id=request.conversation_id,
                message=str(exc),
                termination_reason=final_state["termination_reason"],
                error_type=exc.__class__.__name__,
            )
        self._emit(
            final_state,
            AgentStreamEvent(
                type="run_completed",
                run_id=run_id,
                payload={"response": response.model_dump(mode="json", exclude={"html_content"}), "request_id": request_id},
            ),
        )
        self.artifact_service.write_run_summary(response)
        log_event(
            "run.completed",
            request_id=request_id,
            run_id=run_id,
            conversation_id=request.conversation_id,
            message=response.final_message,
            route_name=response.route.name,
            success=response.success,
            run_status=response.run_status,
            artifact_count=len(response.artifacts),
            trace_count=len(response.trace),
        )
        return response
