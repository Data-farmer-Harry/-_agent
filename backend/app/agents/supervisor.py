from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.state import AgentGraphState, TaskRoute


class SupervisorAgent:
    GENERATE_VERBS = ("生成", "绘制", "画", "plot", "draw", "generate", "重画", "重生成", "重新生成", "计算")
    RECOGNITION_HINTS = ("识别", "解析", "recognize", "read this diagram", "根据截图", "上传截图", "from image", "截图")
    IMAGE_ANALYSIS_HINTS = ("相区", "关键点", "坐标轴", "phase field", "axis", "label", "eutectic", "共晶", "critical point")
    FOLLOW_UP_GENERATE_HINTS = ("改成", "改一下", "重新生成", "再生成", "重画", "update", "modify", "regenerate")
    FOLLOW_UP_REFERENCES = ("刚刚", "刚才", "上一张", "那张图", "这张图", "上一轮", "上一个结果", "刚生成")
    FOLLOW_UP_CHAT_HINTS = ("代码", "code", "对不对", "正确", "靠谱吗", "准确", "解释", "讲解", "流程", "怎么生成", "为什么", "刚刚生成了什么")
    EXPLAIN_HINTS = ("解释", "讲解", "说明", "怎么看", "读图", "分析一下")
    FOLLOW_UP_HTML_HINTS = ("交互式html", "交互式 html", "交互式页面", "交互式结果", "interactive html", "result.html", "html文件")
    RECOGNITION_TO_GENERATE_HINTS = ("识别结果", "识别出的", "根据你刚才识别", "根据刚才识别", "对应体系", "按这张图", "按刚才那张图")
    LAMMPS_CORE_HINTS = (
        "lammps",
        "molecular dynamics",
        "分子动力学",
        "dump",
        "trajectory",
        "ovito",
        "nvt",
        "npt",
    )
    LAMMPS_SOFT_HINTS = (
        "md",
        "模拟",
        "势函数",
        "升温",
        "equilibration",
        "heating",
        "eam",
        "lj",
    )
    LAMMPS_EXPLAIN_HINTS = (
        "怎么用",
        "是什么",
        "区别",
        "解释",
        "说明",
        "为什么",
        "报错",
        "怎么办",
        "适合",
        "选择",
        "推荐",
        "concept",
        "explain",
        "why",
        "error",
    )
    LAMMPS_MATERIAL_PATTERN = re.compile(r"\b(al|cu|ni|aluminum|aluminium|copper|nickel)\b|铝|铜|镍", flags=re.IGNORECASE)
    LAMMPS_TEMPERATURE_PATTERN = re.compile(r"\b\d{2,5}\s*(?:k|kelvin)\b|温度\s*\d{2,5}|升到\s*\d{2,5}", flags=re.IGNORECASE)
    LAMMPS_STEPS_PATTERN = re.compile(r"\b\d{3,7}\s*steps?\b|\d{3,7}\s*步|步数\s*\d{3,7}", flags=re.IGNORECASE)
    LAMMPS_TASK_PATTERN = re.compile(r"heating|heat|equilibration|equilibrate|升温|平衡|弛豫|nvt|npt", flags=re.IGNORECASE)
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    def _heuristic_decision(self, state: AgentGraphState) -> dict[str, Any]:
        message = state["request"].message
        lowered = message.lower()
        uploaded_assets = state.get("uploaded_assets", [])
        has_image = any(asset.media_type.startswith("image/") for asset in uploaded_assets)
        last_run_context = state.get("last_run_context")
        recent_run = bool(last_run_context and last_run_context.run_id)
        recent_phase_run = bool(last_run_context and last_run_context.route_name == "phase_diagram.generate")
        recent_recognition_result = state.get("recognition_result")
        recent_recognition = bool(
            recent_recognition_result
            and (
                recent_recognition_result.system
                or recent_recognition_result.raw_summary
                or recent_recognition_result.labels
            )
        )
        mentions_phase_diagram = "phase diagram" in lowered or "相图" in message
        system_detected = bool(self.SYSTEM_PATTERN.search(message))
        image_reference_present = has_image and any(
            token in message for token in ("这张图", "这张截图", "上传的图", "上传的截图", "这幅图", "该图")
        )
        wants_generate = any(token in lowered for token in self.GENERATE_VERBS)
        if not wants_generate and mentions_phase_diagram:
            wants_generate = (
                any(token in lowered for token in ("温度", "temperature", "范围", "区间", "liquidus", "solidus"))
                or system_detected
            ) and any(token in message for token in ("请", "帮", "给我", "想要", "需要", "算", "计算", "做一张"))
        wants_recognition = any(token in lowered for token in self.RECOGNITION_HINTS)
        if has_image and not wants_generate:
            wants_recognition = wants_recognition or any(token in lowered or token in message for token in self.IMAGE_ANALYSIS_HINTS)
        if has_image and not wants_generate and not wants_recognition:
            wants_recognition = True
        follow_up_generate = recent_phase_run and any(token in lowered for token in self.FOLLOW_UP_GENERATE_HINTS) and (
            any(token in message for token in self.FOLLOW_UP_REFERENCES) or "图" in message
        )
        recognition_to_generate = recent_recognition and not has_image and wants_generate and (
            mentions_phase_diagram
            or any(token in message for token in self.RECOGNITION_TO_GENERATE_HINTS)
            or any(token in message for token in self.FOLLOW_UP_REFERENCES)
        )
        wants_interactive_html = any(token in lowered or token in message for token in self.FOLLOW_UP_HTML_HINTS)
        wants_explain = any(token in lowered or token in message for token in self.EXPLAIN_HINTS)
        explicit_follow_up_chat = not has_image and any(token in message for token in self.FOLLOW_UP_REFERENCES) and any(
            token in lowered or token in message for token in self.FOLLOW_UP_CHAT_HINTS
        )
        explanation_follow_up = (
            not has_image
            and not wants_generate
            and not wants_interactive_html
            and wants_explain
            and (recent_run or recent_recognition)
        )
        follow_up_chat = (explicit_follow_up_chat and not recognition_to_generate) or (
            (recent_run or recent_recognition)
            and any(token in message for token in self.FOLLOW_UP_REFERENCES)
            and any(token in lowered or token in message for token in self.FOLLOW_UP_CHAT_HINTS)
            and not recognition_to_generate
        ) or explanation_follow_up
        image_html_request = has_image and wants_interactive_html
        html_follow_up = recent_phase_run and wants_interactive_html
        lammps_core = any(token in lowered for token in self.LAMMPS_CORE_HINTS)
        lammps_soft = any(token in lowered for token in self.LAMMPS_SOFT_HINTS)
        lammps_run_verbs = any(token in message for token in ("运行", "执行", "跑", "做一个", "做一轮", "返回", "给我", "模拟一下", "再跑"))
        lammps_explanation = (lammps_core or lammps_soft) and any(token in lowered or token in message for token in self.LAMMPS_EXPLAIN_HINTS)
        wants_lammps = (lammps_core or (lammps_soft and lammps_run_verbs)) and not mentions_phase_diagram
        if lammps_explanation and not lammps_run_verbs:
            wants_lammps = False
        lammps_follow_up_generate = bool(last_run_context and last_run_context.route_name == "lammps.generate") and (
            any(token in message for token in self.FOLLOW_UP_REFERENCES) or "再跑" in message or "改成" in message
        ) and (lammps_run_verbs or any(token in lowered for token in ("temperature", "steps", "nvt", "npt", "温度", "步数")))
        lammps_missing_slots: list[str] = []
        if wants_lammps and not lammps_follow_up_generate:
            if not self.LAMMPS_MATERIAL_PATTERN.search(message):
                lammps_missing_slots.append("material")
            if not self.LAMMPS_TASK_PATTERN.search(message):
                lammps_missing_slots.append("task_type")
            if not self.LAMMPS_TEMPERATURE_PATTERN.search(message):
                lammps_missing_slots.append("temperature")
            if not self.LAMMPS_STEPS_PATTERN.search(message):
                lammps_missing_slots.append("steps")
        should_clarify_lammps = wants_lammps and len(lammps_missing_slots) >= 1 and not lammps_follow_up_generate

        if image_html_request:
            route_name = "recognition.analyze"
            next_step = "recognition"
            compute_domain = "none"
            intent = "recognize_image_to_interactive_simulator"
            reason = "The user uploaded a phase-diagram image and asked for an interactive HTML view, so route to RecognitionAgent's image-to-simulator path rather than starting a fresh calculated phase-diagram run."
        elif html_follow_up:
            route_name = "conversation.answer"
            next_step = "chat"
            compute_domain = "none"
            intent = "rehydrate_previous_phase_html"
            reason = "The user wants to reopen or regenerate the interactive HTML view for the previous phase-diagram result, so this should stay in follow-up chat instead of triggering a fresh compute run."
        elif follow_up_chat:
            route_name = "conversation.answer"
            next_step = "chat"
            compute_domain = "none"
            intent = "follow_up_about_previous_run"
            reason = "The user is asking about the previous generated artifact or code rather than requesting a fresh calculation."
        elif has_image and wants_generate and (wants_recognition or image_reference_present):
            route_name = "mixed.request"
            next_step = "recognition"
            compute_domain = "phase_diagram"
            intent = "recognize_then_generate"
            reason = "The user provided or referenced an image and also asked for a generated phase-diagram artifact."
        elif should_clarify_lammps:
            route_name = "conversation.answer"
            next_step = "chat"
            compute_domain = "none"
            intent = "clarify_lammps_request"
            reason = (
                "The user appears to want a LAMMPS run, but required slots are missing: "
                f"{', '.join(lammps_missing_slots)}. Ask a concise clarification before executing."
            )
        elif wants_lammps or lammps_follow_up_generate:
            route_name = "lammps.generate"
            next_step = "compute"
            compute_domain = "lammps"
            intent = "run_lammps_simulation"
            reason = "The user asked for a molecular dynamics / LAMMPS task that should be executed through the local compute runtime."
        elif lammps_explanation:
            route_name = "conversation.answer"
            next_step = "chat"
            compute_domain = "none"
            intent = "explain_lammps_or_materials_concept"
            reason = "The user asked for LAMMPS/materials explanation or error diagnosis rather than a fresh MD execution."
        elif wants_generate or follow_up_generate or recognition_to_generate:
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
        if route_name == "conversation.answer" and intent == "clarify_lammps_request":
            confidence = 0.52
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
            "clarification_slots": lammps_missing_slots if intent == "clarify_lammps_request" else [],
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
        recognition_result = state.get("recognition_result")

        try:
            payload = self.llm_client.chat_json(
                system_prompt=(
                    "You are the SupervisorAgent for a materials research system. "
                    "Choose exactly one route_name from: conversation.answer, recognition.analyze, phase_diagram.generate, lammps.generate, mixed.request. "
                    "Priority rules: "
                    "1) If the user uploaded/referenced an image and wants interpretation, extraction, axes, phase fields, labels, key points, or an interactive HTML simulator from that image, choose recognition.analyze. "
                    "2) If the user uploaded/referenced an image and also wants redraw, regenerate, compute, or build a new phase-diagram artifact, choose mixed.request. "
                    "3) If the user wants a new locally computed phase diagram without relying on uploaded image interpretation, choose phase_diagram.generate. "
                    "4) If the user wants molecular dynamics, trajectories, OVITO, or LAMMPS, choose lammps.generate. "
                    "4a) If the user asks how a LAMMPS command, potential, ensemble, or error works without asking to run a simulation, choose conversation.answer so the materials RAG can answer. "
                    "5) Use conversation.answer only for normal discussion or pure follow-up explanation. "
                    "5a) If there is a recent phase-diagram run and the user asks to reopen, regenerate, or show the interactive HTML/result.html for that existing result, choose conversation.answer rather than phase_diagram.generate. "
                    "6) If there is a previous recognition result already in context and the user now asks to generate or redraw a corresponding computed phase diagram, choose phase_diagram.generate. "
                    "7) If there is a recent LAMMPS run and the user is only asking to explain the previous simulation, potential choice, or outputs, choose conversation.answer instead of lammps.generate. "
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
                    f"Recognition result:\n{recognition_result.model_dump_json() if recognition_result else '{}'}\n\n"
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

        if heuristic.get("intent") == "clarify_lammps_request":
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
