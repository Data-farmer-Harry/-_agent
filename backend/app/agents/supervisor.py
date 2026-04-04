from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.state import AgentGraphState, TaskRoute


class SupervisorAgent:
    GENERATE_VERBS = ("生成", "绘制", "画", "plot", "draw", "generate", "重画", "重生成", "重新生成")
    RECOGNITION_HINTS = ("识别", "看看", "解析", "recognize", "read this diagram", "根据截图", "上传截图", "from image", "截图")
    FOLLOW_UP_GENERATE_HINTS = ("改成", "改一下", "重新生成", "再生成", "重画", "update", "modify", "regenerate")
    FOLLOW_UP_REFERENCES = ("刚刚", "刚才", "上一张", "那张图", "这张图", "上一轮", "上一个结果", "刚生成")
    FOLLOW_UP_CHAT_HINTS = ("代码", "code", "对不对", "正确", "靠谱吗", "准确", "解释", "讲解", "流程", "怎么生成", "为什么", "刚刚生成了什么")
    LAMMPS_HINTS = (
        "lammps",
        "md",
        "molecular dynamics",
        "模拟",
        "分子动力学",
        "势函数",
        "dump",
        "trajectory",
        "ovito",
        "升温",
        "equilibration",
        "heating",
        "nvt",
        "npt",
        "eam",
        "lj",
    )
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    def _heuristic_decision(self, state: AgentGraphState) -> dict[str, Any]:
        message = state["request"].message
        lowered = message.lower()
        uploaded_assets = state.get("uploaded_assets", [])
        has_image = any(asset.media_type.startswith("image/") for asset in uploaded_assets)
        recent_phase_run = bool(state.get("last_run_context") and state["last_run_context"].route_name == "phase_diagram.generate")
        mentions_phase_diagram = "phase diagram" in lowered or "相图" in message
        wants_generate = any(token in lowered for token in self.GENERATE_VERBS)
        if not wants_generate and mentions_phase_diagram:
            wants_generate = any(token in lowered for token in ("温度", "temperature", "范围", "区间", "liquidus", "solidus")) or bool(self.SYSTEM_PATTERN.search(message))
        wants_recognition = has_image or any(token in lowered for token in self.RECOGNITION_HINTS)
        follow_up_generate = recent_phase_run and any(token in lowered for token in self.FOLLOW_UP_GENERATE_HINTS) and (
            any(token in message for token in self.FOLLOW_UP_REFERENCES) or "图" in message
        )
        follow_up_chat = recent_phase_run and any(token in message for token in self.FOLLOW_UP_REFERENCES) and any(
            token in lowered or token in message for token in self.FOLLOW_UP_CHAT_HINTS
        )
        system_detected = bool(self.SYSTEM_PATTERN.search(message))
        wants_lammps = any(token in lowered for token in self.LAMMPS_HINTS) and not mentions_phase_diagram

        if follow_up_chat:
            route_name = "conversation.answer"
            next_step = "chat"
            compute_domain = "none"
            intent = "follow_up_about_previous_run"
            reason = "The user is asking about the previous generated artifact or code rather than requesting a fresh calculation."
        elif wants_generate and wants_recognition:
            route_name = "mixed.request"
            next_step = "recognition"
            compute_domain = "phase_diagram"
            intent = "recognize_then_generate"
            reason = "The user provided or referenced an image and also asked for a generated phase-diagram artifact."
        elif wants_lammps:
            route_name = "lammps.generate"
            next_step = "compute"
            compute_domain = "lammps"
            intent = "run_lammps_simulation"
            reason = "The user asked for a molecular dynamics / LAMMPS task that should be executed through the local compute runtime."
        elif wants_generate or follow_up_generate:
            route_name = "phase_diagram.generate"
            next_step = "compute"
            compute_domain = "phase_diagram"
            intent = "generate_phase_diagram"
            reason = "The user asked for a concrete phase-diagram artifact that should be computed locally."
        elif wants_recognition:
            route_name = "recognition.analyze"
            next_step = "recognition"
            compute_domain = "none"
            intent = "recognize_phase_diagram"
            reason = "The user wants to inspect or explain an uploaded phase-diagram image before any local calculation."
        else:
            route_name = "conversation.answer"
            next_step = "chat"
            compute_domain = "none"
            intent = "answer_question"
            reason = "The user is asking for explanation or follow-up conversation without requesting a new computed artifact."

        confidence = 0.7
        if route_name == "phase_diagram.generate" and system_detected and wants_generate:
            confidence = 0.86
        if route_name == "mixed.request":
            confidence = 0.82

        return {
            "route_name": route_name,
            "next_step": next_step,
            "compute_domain": compute_domain,
            "intent": intent,
            "reason": reason,
            "confidence": confidence,
            "source": "heuristic_supervisor",
        }

    def decide(self, state: AgentGraphState) -> dict[str, Any]:
        heuristic = self._heuristic_decision(state)
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="SupervisorAgent", capability="任务路由决策")
            return heuristic

        request = state["request"]
        history = [turn.model_dump(mode="json") for turn in state.get("messages", [])[-8:]]
        assets = [
            {"name": asset.name, "media_type": asset.media_type, "size_bytes": asset.size_bytes}
            for asset in state.get("uploaded_assets", [])
        ]
        last_run_context = state.get("last_run_context")

        try:
            payload = self.llm_client.chat_json(
                system_prompt=(
                    "You are the SupervisorAgent for a materials research system. "
                    "Choose exactly one route_name from: conversation.answer, recognition.analyze, phase_diagram.generate, lammps.generate, mixed.request. "
                    "Choose mixed.request when the user both supplied/referenced an image and wants a new generated phase diagram or redraw from the recognition result. "
                    "Choose recognition.analyze when the user mainly wants the uploaded phase-diagram image interpreted. "
                    "Choose phase_diagram.generate when the user wants a newly computed phase diagram artifact. "
                    "Choose lammps.generate when the user wants a molecular dynamics / LAMMPS simulation or post-processing run. "
                    "Choose conversation.answer for normal discussion or follow-up explanation. Return JSON only."
                ),
                user_prompt=(
                    "Return JSON with keys: route_name, next_step, compute_domain, intent, reason, confidence.\n"
                    "Valid next_step values: chat, recognition, compute.\n"
                    "Valid compute_domain values: none, phase_diagram, lammps.\n"
                    "Use the heuristic baseline only as a fallback reference; make an independent routing judgment from the current conversation context.\n"
                    f"User message:\n{request.message}\n\n"
                    f"Conversation summary:\n{state.get('current_context_summary', '')}\n\n"
                    f"Recent history:\n{json.dumps(history, ensure_ascii=False)}\n\n"
                    f"Uploaded assets:\n{json.dumps(assets, ensure_ascii=False)}\n\n"
                    f"Last run context:\n{last_run_context.model_dump_json() if last_run_context else '{}'}\n\n"
                    f"Heuristic baseline:\n{json.dumps(heuristic, ensure_ascii=False)}"
                ),
                max_tokens=600,
                temperature=0.1,
            )
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"SupervisorAgent 调用 LLM 进行任务路由时失败：{exc}") from exc
            return heuristic

        if not payload:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("SupervisorAgent 需要结构化 LLM 路由结果，但本次没有得到有效 JSON。")
            return heuristic

        route_name = str(payload.get("route_name") or heuristic["route_name"]).strip()
        valid_routes = {"conversation.answer", "recognition.analyze", "phase_diagram.generate", "lammps.generate", "mixed.request"}
        if route_name not in valid_routes:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"SupervisorAgent 返回了无效 route_name={route_name!r}，因此无法以真实 agent 方式继续。")
            route_name = heuristic["route_name"]
        next_step = str(payload.get("next_step") or heuristic["next_step"]).strip()
        if next_step not in {"chat", "recognition", "compute"}:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"SupervisorAgent 返回了无效 next_step={next_step!r}，因此无法以真实 agent 方式继续。")
            next_step = heuristic["next_step"]
        compute_domain = str(payload.get("compute_domain") or heuristic.get("compute_domain") or "none").strip()
        if compute_domain not in {"none", "phase_diagram", "lammps"}:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"SupervisorAgent 返回了无效 compute_domain={compute_domain!r}，因此无法以真实 agent 方式继续。")
            compute_domain = str(heuristic.get("compute_domain") or "none")

        try:
            confidence = max(0.0, min(float(payload.get("confidence", heuristic["confidence"])), 1.0))
        except (TypeError, ValueError):
            confidence = float(heuristic["confidence"])

        return {
            "route_name": route_name,
            "next_step": next_step,
            "compute_domain": compute_domain,
            "intent": str(payload.get("intent") or heuristic["intent"]).strip() or heuristic["intent"],
            "reason": str(payload.get("reason") or heuristic["reason"]).strip() or heuristic["reason"],
            "confidence": confidence,
            "source": "llm_supervisor",
        }

    def build_route(self, decision: dict[str, Any]) -> TaskRoute:
        return TaskRoute(
            name=str(decision.get("route_name") or "conversation.answer"),
            workspace_id="materials_agent",
            reason=str(decision.get("reason") or ""),
            selected_tool=str(decision.get("next_step") or ""),
            intent=str(decision.get("intent") or ""),
            decision_source=str(decision.get("source") or "supervisor"),
            decision_confidence=float(decision.get("confidence")) if decision.get("confidence") is not None else None,
            compute_domain=str(decision.get("compute_domain") or "none"),
        )
