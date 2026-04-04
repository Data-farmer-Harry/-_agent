from __future__ import annotations

import json
import re

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.state import (
    AgentGraphState,
    ConversationTurn,
    LastRunContext,
    MemorySnapshot,
    PromptSuggestionRequest,
    PromptSuggestionResponse,
    RecognitionResult,
)


class ChatAgent:
    CODE_PATTERN = re.compile(r"(代码|code)", flags=re.IGNORECASE)
    CORRECTNESS_PATTERN = re.compile(r"(对不对|正确吗|靠谱吗|准确吗|可信吗|可靠)")
    EXPLAIN_PATTERN = re.compile(r"(解释|讲解|怎么看|说明|这图|这张图|图里|图上|读图)")
    TRACE_PATTERN = re.compile(r"(流程|链路|怎么生成|怎么做的|经过了哪些步骤)")
    RECOGNITION_PATTERN = re.compile(r"(识别结果|识别到了什么|看到了什么)")
    LAMMPS_PATTERN = re.compile(r"(lammps|md|分子动力学|模拟|轨迹|video|gif|ovito|report|thermo)", flags=re.IGNORECASE)
    EUTECTOID_PATTERN = re.compile(r"(共析点|共析反应)")
    PERITECTIC_PATTERN = re.compile(r"(包晶|包晶反应)")

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _compact_turns(messages: list[ConversationTurn], *, limit: int = 8) -> str:
        if not messages:
            return "[]"
        payload = [turn.model_dump(mode="json") for turn in messages[-limit:]]
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _normalize_prompt_suggestion(content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return "请基于当前会话上下文，给我一个更有价值的下一步追问。"

        candidate = lines[0]
        candidate = re.sub(r"^(建议(?:追问|提问)?|推荐(?:追问|提问)?|你可以这样问|下一步可以问)[：:]\s*", "", candidate)
        candidate = re.sub(r"^[>\-\d\.\)\]\s]+", "", candidate).strip()
        if not candidate:
            candidate = lines[0]
        if len(candidate) > 160:
            candidate = candidate[:159].rstrip("，。；;、 ") + "…"
        return candidate

    def suggest_prompt(
        self,
        *,
        request: PromptSuggestionRequest,
        memory_snapshot: MemorySnapshot | None = None,
        recognition_result: RecognitionResult | None = None,
    ) -> PromptSuggestionResponse:
        self.llm_client.require_configured(agent_name="ChatAgent", capability="上下文动态 prompt 推荐")

        snapshot = memory_snapshot or MemorySnapshot(conversation_id=request.conversation_id)
        conversation_history = request.conversation_history or snapshot.messages
        last_run_context = request.last_run_context if request.last_run_context.run_id else snapshot.last_run_context
        current_context_summary = request.current_context_summary or snapshot.current_context_summary
        resolved_recognition = recognition_result or snapshot.recognition_result

        content = self.llm_client.chat_text(
            system_prompt=(
                "You suggest the next user prompt for a materials research agent. "
                "Return exactly one concise Chinese prompt that the user can directly send. "
                "Do not answer the research question itself. "
                "Do not quote long previous messages. "
                "Use the latest conversation context, last run context, and recognition result when helpful. "
                "Prefer concrete follow-up prompts about explanation, verification, risk, next steps, or deeper analysis."
            ),
            user_prompt=(
                f"Conversation ID: {request.conversation_id}\n\n"
                f"Current draft (may be empty):\n{request.draft_message or '(empty)'}\n\n"
                f"Current summary:\n{current_context_summary or '(none)'}\n\n"
                f"Last run context:\n{last_run_context.model_dump_json()}\n\n"
                f"Recognition result:\n{resolved_recognition.model_dump_json() if resolved_recognition else '{}'}\n\n"
                f"Recent conversation turns:\n{self._compact_turns(conversation_history)}\n\n"
                "Return only the recommended next prompt."
            ),
            max_tokens=220,
            temperature=0.45,
        )

        suggestion = self._normalize_prompt_suggestion(content)
        rationale = ""
        if last_run_context.run_id:
            rationale = f"基于最近一次 {last_run_context.route_name or 'agent'} 结果推荐。"
        elif conversation_history:
            rationale = "基于最近会话上下文推荐。"

        return PromptSuggestionResponse(
            suggested_prompt=suggestion,
            rationale=rationale,
            source="llm_prompt_suggester",
        )

    @staticmethod
    def _messages_with_answer(state: AgentGraphState, answer: str) -> list[ConversationTurn]:
        messages = list(state.get("messages", []))
        messages.append(ConversationTurn(role="assistant", content=answer))
        return messages

    @staticmethod
    def _format_recognition(recognition: RecognitionResult) -> str:
        system = recognition.system or "未明确体系"
        phases = "、".join(recognition.phases[:6]) if recognition.phases else "未稳定识别出相区名称"
        labels = "、".join(recognition.labels[:6]) if recognition.labels else "未提取到明显标签"
        points = "；".join(
            f"{item.label or '关键点'}@x={item.composition if item.composition is not None else '?'} T={item.temperature if item.temperature is not None else '?'}"
            for item in recognition.critical_points[:4]
        ) or "未识别出可靠关键点"
        return (
            f"RecognitionAgent 已完成这张图的第一轮结构化识别。体系判断为 {system}，"
            f"图类型是 {recognition.diagram_type}，置信度约 {recognition.confidence:.2f}。"
            f" 当前识别到的相区/相名包括：{phases}。标签摘要：{labels}。关键点摘要：{points}。"
        )

    def _build_phase_followup_answer(self, state: AgentGraphState) -> str:
        request = state["request"]
        context = state.get("last_run_context")
        response = state.get("phase_diagram_result")
        message = request.message.strip()
        review = (response.metadata.get("review") or {}) if response else {}
        accuracy = (response.metadata.get("accuracy") or {}) if response else {}

        if self.CODE_PATTERN.search(message) and context.generated_code_preview:
            preview = "\n".join(context.generated_code_preview.splitlines()[:24])
            suffix = "\n... 其余代码已省略" if len(context.generated_code_preview.splitlines()) > 24 else ""
            return (
                f"上一轮我生成的是一段很薄的本地 Python wrapper，核心目的是调用 pycalphad + TDB 计算 helper。"
                f"\n{preview}{suffix}"
            )

        if self.CORRECTNESS_PATTERN.search(message):
            accuracy_text = ""
            if accuracy:
                accuracy_text = (
                    f" 当前 accuracy gate = {accuracy.get('passed')}，"
                    f"缺失稳定相 = {accuracy.get('missing_required_phases', [])}。"
                )
            return (
                f"这轮结果不是手画示意图，而是走了热力学数据库检索、LLM 写 wrapper、本地 Python 执行和 review。"
                f" 当前 review 摘要是：{review.get('summary', '暂无')}。{accuracy_text}"
            )

        if self.EXPLAIN_PATTERN.search(message):
            return (
                f"这张图的核心信息是：{context.request_summary or context.system_name or '该体系'}。"
                " 横轴表示成分，纵轴表示温度；相界来自本地 pycalphad + TDB 的求解结果，而不是前端静态模板。"
            )

        if self.TRACE_PATTERN.search(message):
            trace_text = " -> ".join(context.trace_summary[:8]) if context.trace_summary else "request_interpreter -> thermo_database_lookup -> phase_diagram_codegen -> python_execute -> phase_diagram_result_review"
            return f"上一轮 agent 链路是：{trace_text}"

        return response.final_message if response else "上一轮相图任务已经完成。"

    def _build_lammps_followup_answer(self, state: AgentGraphState) -> str:
        request = state["request"]
        context = state.get("last_run_context")
        response = state.get("lammps_result")
        message = request.message.strip()
        summary = response.summary if response else {}
        review = (response.metadata.get("review") or {}) if response else {}
        metrics = summary.get("metrics", {}) if isinstance(summary, dict) else {}
        run_mode = str(response.metadata.get("run_mode") or "") if response else ""

        if self.CODE_PATTERN.search(message) and context.generated_code_preview:
            preview = "\n".join(context.generated_code_preview.splitlines()[:30])
            suffix = "\n... 其余脚本已省略" if len(context.generated_code_preview.splitlines()) > 30 else ""
            return f"上一轮我生成的是一份 LAMMPS 输入脚本 `in.lammps`，核心片段如下：\n{preview}{suffix}"

        if self.CORRECTNESS_PATTERN.search(message):
            return (
                f"这轮 LAMMPS 结果的 review 摘要是：{review.get('summary', '暂无')}。"
                f" 当前执行模式是 {run_mode or 'unknown'}，关键指标包括：{metrics or '暂无'}。"
            )

        if self.EXPLAIN_PATTERN.search(message) or self.LAMMPS_PATTERN.search(message):
            artifact_names = ", ".join(context.artifact_names[:8]) if context.artifact_names else "暂无 artifact"
            return (
                f"上一轮 LAMMPS 任务摘要是：{context.request_summary or '暂无'}。"
                f" 这轮保留的主要产物有：{artifact_names}。"
            )

        if self.TRACE_PATTERN.search(message):
            trace_text = " -> ".join(context.trace_summary[:8]) if context.trace_summary else "lammps_request_interpreter -> lammps_registry_lookup -> lammps_validation -> lammps_input_codegen -> lammps_execute -> lammps_postprocess -> lammps_result_review"
            return f"上一轮 LAMMPS agent 链路是：{trace_text}"

        return response.final_message if response else "上一轮 LAMMPS 任务已经完成。"

    def _build_contextual_answer(self, state: AgentGraphState) -> str | None:
        request = state["request"]
        if state.get("phase_diagram_result") is not None:
            return self._build_phase_followup_answer(state)
        if state.get("lammps_result") is not None:
            return self._build_lammps_followup_answer(state)
        if state.get("recognition_result") is not None and (
            state.get("route") and state["route"].name in {"recognition.analyze", "mixed.request"}
            or self.RECOGNITION_PATTERN.search(request.message)
        ):
            return self._format_recognition(state["recognition_result"])
        if state.get("last_run_context") and state["last_run_context"].route_name == "phase_diagram.generate":
            if any(pattern.search(request.message) for pattern in (self.CODE_PATTERN, self.CORRECTNESS_PATTERN, self.EXPLAIN_PATTERN, self.TRACE_PATTERN)):
                return self._build_phase_followup_answer(state)
        if state.get("last_run_context") and state["last_run_context"].route_name == "lammps.generate":
            if any(pattern.search(request.message) for pattern in (self.CODE_PATTERN, self.CORRECTNESS_PATTERN, self.EXPLAIN_PATTERN, self.TRACE_PATTERN, self.LAMMPS_PATTERN)):
                return self._build_lammps_followup_answer(state)
        return None

    def _build_fallback_answer(self, state: AgentGraphState, contextual_answer: str | None) -> str:
        request = state["request"]
        if contextual_answer is not None:
            return contextual_answer
        if self.EUTECTOID_PATTERN.search(request.message):
            return "共析点是某一固定成分下，一个固相在特定温度同时分解为两个新固相的点；它和共晶点不同，共析发生在固态。"
        if self.PERITECTIC_PATTERN.search(request.message):
            return "包晶反应通常指液相与一种固相在固定温度反应，生成另一种固相；它和共析最大的区别是包晶涉及液相，而共析发生在固态。"
        return "当前处于对话模式。你可以继续问材料概念、追问上一轮相图，或者上传截图让我先做识别。"

    def run(self, state: AgentGraphState) -> dict:
        request = state["request"]
        contextual_answer = self._build_contextual_answer(state)
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ChatAgent", capability="对话回答与 follow-up 解释")
            answer = self._build_fallback_answer(state, contextual_answer)
        else:
            try:
                answer = self.llm_client.chat_text(
                    system_prompt=(
                        "You are the ChatAgent for a materials research system. "
                        "Answer clearly in Chinese. "
                        "You must use the provided last_run_context, recognition_result, and contextual grounding when relevant. "
                        "If the user asks about the previous run, answer from that context instead of pretending nothing happened. "
                        "Do not claim tool execution that did not happen in this turn. "
                        "Do not invent browsing,联网,数据库访问,外部检索,插件调用, or hidden system capabilities unless they are explicitly present in the supplied context for this turn or are part of the configured system capabilities. "
                        "If future web research capability is supported, only describe it as available when it is actually configured or invoked; otherwise answer conservatively. "
                        "Do not describe yourself as a generic internet-enabled assistant by default; stay grounded in this materials research agent project and the currently active tools. "
                        "Do not speculate about model provider, model family, or deployment identity unless the user is explicitly asking and the answer is present in the provided context. "
                        "If capability boundaries are unclear, answer conservatively and focus on what the current run, artifacts, and memory actually show."
                    ),
                    user_prompt=(
                        f"User message:\n{request.message}\n\n"
                        f"Current summary:\n{state.get('current_context_summary', '')}\n\n"
                        f"Last run context:\n{state.get('last_run_context').model_dump_json() if state.get('last_run_context') else '{}'}\n\n"
                        f"Recognition result:\n{state.get('recognition_result').model_dump_json() if state.get('recognition_result') else '{}'}\n\n"
                        f"Contextual grounding draft:\n{contextual_answer or ''}\n\n"
                        f"Conversation history:\n{json.dumps([turn.model_dump(mode='json') for turn in state.get('messages', [])[-8:]], ensure_ascii=False)}"
                    ),
                    max_tokens=1200,
                    temperature=0.2,
                )
            except RuntimeError as exc:
                if settings.require_llm_for_agents:
                    raise LLMRequiredError(f"ChatAgent 调用 LLM 生成回答时失败：{exc}") from exc
                answer = self._build_fallback_answer(state, contextual_answer)

        return {
            "final_answer": answer,
            "messages": self._messages_with_answer(state, answer),
            "success": True,
            "error": "",
        }
