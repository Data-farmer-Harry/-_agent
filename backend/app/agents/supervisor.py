from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.orchestration import DAGNode, DAGPlan, DAGValidationError, validate_dag_plan
from app.state import AgentGraphState, TaskRoute


class SupervisorAgent:
    ROUTE_CONTRACTS: dict[str, tuple[str, str]] = {
        "conversation.answer": ("chat", "none"),
        "recognition.analyze": ("recognition", "none"),
        "phase_diagram.generate": ("compute", "phase_diagram"),
        "lammps.generate": ("compute", "lammps"),
        "mixed.request": ("recognition", "phase_diagram"),
    }
    LLM_REVIEW_CONFIDENCE_THRESHOLD = 0.78
    CONFIDENCE_WEIGHTS: dict[str, float] = {
        "route_evidence": 0.45,
        "candidate_separation": 0.25,
        "critical_checks": 0.20,
        "advisory_checks": 0.10,
    }
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
        "加热",
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
    LAMMPS_TASK_PATTERN = re.compile(r"heating|heat|equilibration|equilibrate|加热|升温|平衡|弛豫|nvt|npt", flags=re.IGNORECASE)
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
            wants_generate = any(
                token in lowered or token in message
                for token in ("做一张", "来一张", "相图计算", "相图绘制", "plot the phase diagram", "compute the phase diagram")
            )
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

        return {
            "route_name": route_name,
            "next_step": next_step,
            "compute_domain": compute_domain,
            "intent": intent,
            "reason": reason,
            "source": "heuristic_supervisor",
            "clarification_slots": lammps_missing_slots if intent == "clarify_lammps_request" else [],
        }

    @classmethod
    def _route_evidence(cls, state: AgentGraphState) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
        """Build route support from observable signals only.

        The selected route and any LLM-reported confidence are intentionally
        excluded. This prevents a route from making itself look more certain
        merely because a heuristic or model selected it.
        """

        message = state["request"].message
        lowered = message.lower()
        assets = state.get("uploaded_assets", [])
        has_image = any(asset.media_type.startswith("image/") for asset in assets)
        recognition = state.get("recognition_result")
        last_run = state.get("last_run_context")
        has_recognition_context = bool(recognition and (recognition.system or recognition.raw_summary or recognition.labels))
        has_phase_system = bool(
            state["request"].system_name.strip()
            or cls.SYSTEM_PATTERN.search(message)
            or (recognition and recognition.system)
            or (last_run and last_run.route_name == "phase_diagram.generate" and last_run.system_name)
        )
        mentions_phase = "相图" in message or "phase diagram" in lowered
        mentions_lammps = any(token in lowered for token in cls.LAMMPS_CORE_HINTS) or any(
            token in lowered for token in cls.LAMMPS_SOFT_HINTS
        )
        wants_generate = any(token in lowered for token in cls.GENERATE_VERBS)
        wants_recognition = any(token in lowered for token in cls.RECOGNITION_HINTS)
        wants_run = any(
            token in message
            for token in ("运行", "执行", "跑", "请用", "给我", "做一个", "做一轮", "模拟一下", "再跑")
        )
        wants_explain = any(token in lowered or token in message for token in (*cls.EXPLAIN_HINTS, *cls.LAMMPS_EXPLAIN_HINTS))
        lammps_slots_complete = all(
            pattern.search(message)
            for pattern in (
                cls.LAMMPS_MATERIAL_PATTERN,
                cls.LAMMPS_TASK_PATTERN,
                cls.LAMMPS_TEMPERATURE_PATTERN,
                cls.LAMMPS_STEPS_PATTERN,
            )
        )

        raw_scores = {
            "conversation.answer": 0.24,
            "recognition.analyze": 0.08,
            "phase_diagram.generate": 0.08,
            "lammps.generate": 0.08,
            "mixed.request": 0.04,
        }
        evidence: dict[str, list[dict[str, Any]]] = {
            route: [{"signal": "route_prior", "weight": weight}]
            for route, weight in raw_scores.items()
        }

        def add(route: str, signal: str, weight: float) -> None:
            raw_scores[route] += weight
            evidence[route].append({"signal": signal, "weight": weight})

        if wants_explain or message.rstrip().endswith(("?", "？")):
            add("conversation.answer", "question_or_explanation", 0.65)
        if not has_image and not wants_generate and not wants_recognition and not (mentions_lammps and wants_run):
            add("conversation.answer", "direct_chat_default", 0.75)
        if mentions_lammps and wants_explain and not wants_run:
            add("conversation.answer", "lammps_explanation_without_execution", 0.70)
        if last_run and last_run.run_id and any(token in message for token in cls.FOLLOW_UP_REFERENCES):
            add("conversation.answer", "previous_run_follow_up", 0.45)
        if mentions_lammps and wants_run and not lammps_slots_complete:
            add("conversation.answer", "incomplete_lammps_slots_require_clarification", 1.50)
        if mentions_phase and wants_generate and not has_phase_system:
            add("conversation.answer", "missing_phase_system_requires_clarification", 1.10)
        if wants_recognition and not has_image and not has_recognition_context:
            add("conversation.answer", "missing_image_requires_clarification", 0.90)

        if has_image:
            add("recognition.analyze", "image_attached", 0.72)
        if wants_recognition:
            add("recognition.analyze", "recognition_intent", 0.50)

        if mentions_phase and wants_generate:
            add("phase_diagram.generate", "phase_generation_intent", 0.85)
        if wants_generate and has_phase_system:
            add("phase_diagram.generate", "phase_system_available", 0.35)
        if wants_generate and has_recognition_context and not has_image:
            add("phase_diagram.generate", "recognition_context_reusable", 0.40)

        if mentions_lammps:
            add("lammps.generate", "lammps_domain_signal", 0.45)
        if mentions_lammps and wants_run:
            add("lammps.generate", "execution_intent", 0.80)
        if mentions_lammps and wants_run and lammps_slots_complete:
            add("lammps.generate", "complete_execution_slots", 0.30)

        if has_image and wants_generate:
            add("mixed.request", "image_plus_generation", 1.20)
        if has_image and wants_generate and wants_recognition:
            add("mixed.request", "explicit_recognize_then_generate", 0.25)

        total = sum(raw_scores.values()) or 1.0
        probabilities = {route: round(score / total, 6) for route, score in raw_scores.items()}
        return probabilities, {
            route: [*items, {"signal": "raw_total", "weight": round(raw_scores[route], 6)}]
            for route, items in evidence.items()
        }

    @classmethod
    def _candidate_route_scores(cls, state: AgentGraphState, decision: dict[str, Any] | None = None) -> dict[str, float]:
        del decision  # Kept for compatibility with older callers.
        scores, _ = cls._route_evidence(state)
        return scores

    @classmethod
    def _supervisor_audit(cls, state: AgentGraphState, decision: dict[str, Any]) -> dict[str, Any]:
        route_name = str(decision.get("route_name") or "conversation.answer")
        request = state["request"]
        message = request.message
        lowered = message.lower()
        assets = state.get("uploaded_assets", [])
        has_image = any(asset.media_type.startswith("image/") for asset in assets)
        recognition = state.get("recognition_result")
        last_run = state.get("last_run_context")
        has_recognition_context = bool(recognition and (recognition.system or recognition.raw_summary or recognition.labels))
        has_phase_system = bool(
            request.system_name.strip()
            or cls.SYSTEM_PATTERN.search(message)
            or (recognition and recognition.system)
            or (last_run and last_run.route_name == "phase_diagram.generate" and last_run.system_name)
        )
        recent_lammps = bool(last_run and last_run.route_name == "lammps.generate")

        route_scores, route_evidence = cls._route_evidence(state)
        ranked = sorted(route_scores.items(), key=lambda item: (-item[1], item[0]))
        top_route, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = max(0.0, top_score - second_score)
        selected_evidence_items = route_evidence.get(route_name, [])
        selected_raw_score = next(
            (float(item["weight"]) for item in selected_evidence_items if item.get("signal") == "raw_total"),
            0.0,
        )

        expected_next, expected_domain = cls.ROUTE_CONTRACTS.get(route_name, ("", ""))
        route_contract_ok = bool(expected_next) and (
            str(decision.get("next_step") or "") == expected_next
            and str(decision.get("compute_domain") or "none") == expected_domain
        )
        asset_ok = (
            route_name not in {"recognition.analyze", "mixed.request"}
            or (route_name == "recognition.analyze" and has_image)
            or (route_name == "mixed.request" and (has_image or has_recognition_context))
        )

        missing_lammps_slots: list[str] = []
        if route_name == "lammps.generate" and not recent_lammps:
            if not cls.LAMMPS_MATERIAL_PATTERN.search(message):
                missing_lammps_slots.append("material")
            if not cls.LAMMPS_TASK_PATTERN.search(message):
                missing_lammps_slots.append("task_type")
            if not cls.LAMMPS_TEMPERATURE_PATTERN.search(message):
                missing_lammps_slots.append("temperature")
            if not cls.LAMMPS_STEPS_PATTERN.search(message):
                missing_lammps_slots.append("steps")

        compute_ok = True
        compute_detail = "No compute prerequisite is required."
        if route_name == "phase_diagram.generate":
            compute_ok = has_phase_system
            compute_detail = "A material system is available." if compute_ok else "A phase-diagram system is missing."
        elif route_name == "lammps.generate":
            compute_ok = not missing_lammps_slots
            compute_detail = (
                "LAMMPS execution slots are complete."
                if compute_ok
                else f"Missing LAMMPS slots: {', '.join(missing_lammps_slots)}."
            )
        elif route_name == "mixed.request":
            compute_ok = has_image or has_recognition_context
            compute_detail = "Recognition can provide the material system." if compute_ok else "Mixed execution lacks image context."

        ambiguous_domains = ("相图" in message or "phase diagram" in lowered) and (
            "lammps" in lowered or "分子动力学" in message
        )
        signal_aligned = route_name == top_route or margin < 0.12

        plan = DAGPlan(
            plan_id="supervisor-route-audit",
            global_timeout_seconds=2.0,
            nodes=[
                DAGNode(node_id="signal_extract", node_type="supervisor_signal", critical=True),
                DAGNode(node_id="candidate_score", node_type="supervisor_score", dependencies=["signal_extract"], critical=False),
                DAGNode(node_id="asset_prerequisite", node_type="prerequisite", dependencies=["candidate_score"], critical=True),
                DAGNode(node_id="compute_prerequisite", node_type="prerequisite", dependencies=["candidate_score"], critical=True),
                DAGNode(
                    node_id="route_contract",
                    node_type="contract",
                    dependencies=["asset_prerequisite", "compute_prerequisite"],
                    critical=True,
                ),
                DAGNode(node_id="commit_route", node_type="commit", dependencies=["route_contract"], critical=True),
            ],
        )
        dag_valid = True
        dag_error = ""
        dag_order: list[str] = []
        try:
            validate_dag_plan(plan)
            dag_order = plan.topological_order()
        except DAGValidationError as exc:
            dag_valid = False
            dag_error = str(exc)

        checks = [
            {
                "node_id": "signal_extract",
                "passed": bool(selected_evidence_items),
                "critical": True,
                "detail": f"Collected {max(0, len(selected_evidence_items) - 1)} deterministic signals for {route_name}.",
            },
            {
                "node_id": "candidate_score",
                "passed": signal_aligned,
                "critical": False,
                "detail": f"selected={route_name}, top={top_route}, probability_margin={margin:.3f}",
            },
            {
                "node_id": "asset_prerequisite",
                "passed": asset_ok,
                "critical": True,
                "detail": "Image/recognition context is available." if asset_ok else "Image input is required for this route.",
            },
            {
                "node_id": "compute_prerequisite",
                "passed": compute_ok,
                "critical": True,
                "detail": compute_detail,
            },
            {
                "node_id": "route_contract",
                "passed": route_contract_ok,
                "critical": True,
                "detail": f"expected next={expected_next}, domain={expected_domain}",
            },
            {
                "node_id": "commit_route",
                "passed": asset_ok and compute_ok and route_contract_ok and dag_valid,
                "critical": True,
                "detail": "All execution prerequisites permit route commit.",
            },
            {
                "node_id": "domain_ambiguity",
                "passed": not ambiguous_domains,
                "critical": False,
                "detail": "Phase-diagram and LAMMPS signals overlap." if ambiguous_domains else "No cross-domain conflict.",
            },
            {
                "node_id": "dag_topology",
                "passed": dag_valid,
                "critical": True,
                "detail": dag_error or "Supervisor audit DAG is acyclic and complete.",
            },
        ]
        critical_failures = [
            str(check["node_id"])
            for check in checks
            if bool(check["critical"]) and not bool(check["passed"])
        ]
        warning_nodes = [
            str(check["node_id"])
            for check in checks
            if not bool(check["critical"]) and not bool(check["passed"])
        ]
        critical_checks = [check for check in checks if bool(check["critical"])]
        advisory_checks = [check for check in checks if not bool(check["critical"])]
        critical_pass_rate = sum(1 for check in critical_checks if check["passed"]) / max(1, len(critical_checks))
        advisory_pass_rate = sum(1 for check in advisory_checks if check["passed"]) / max(1, len(advisory_checks))
        route_evidence_strength = min(1.0, selected_raw_score / 1.25)
        candidate_separation = (
            max(0.0, top_score - second_score) / max(top_score, 1e-9)
            if route_name == top_route
            else 0.0
        )
        components = {
            "route_evidence": round(route_evidence_strength, 6),
            "candidate_separation": round(candidate_separation, 6),
            "critical_checks": round(critical_pass_rate, 6),
            "advisory_checks": round(advisory_pass_rate, 6),
        }
        calibrated_confidence = sum(cls.CONFIDENCE_WEIGHTS[name] * value for name, value in components.items())
        applied_penalties: list[dict[str, Any]] = []
        if critical_failures:
            calibrated_confidence *= 0.55
            applied_penalties.append({"reason": "critical_dag_failure", "operation": "multiply", "value": 0.55})
        if ambiguous_domains:
            calibrated_confidence -= 0.10
            applied_penalties.append({"reason": "cross_domain_ambiguity", "operation": "subtract", "value": 0.10})
        if not signal_aligned:
            calibrated_confidence *= 0.70
            applied_penalties.append({"reason": "selected_route_not_top_candidate", "operation": "multiply", "value": 0.70})
        calibrated_confidence = round(max(0.0, min(calibrated_confidence, 1.0)), 6)
        requires_review = bool(
            critical_failures
            or warning_nodes
            or margin < 0.18
            or calibrated_confidence < cls.LLM_REVIEW_CONFIDENCE_THRESHOLD
        )
        deterministic_slot_clarification = bool(
            str(decision.get("intent") or "") == "clarify_lammps_request"
            and decision.get("clarification_slots")
            and not critical_failures
        )
        if deterministic_slot_clarification:
            requires_review = False
        return {
            "schema_version": "supervisor-route-audit/v2",
            "passed": not critical_failures,
            "requires_llm_review": requires_review,
            "review_triggered": requires_review,
            "calibrated_confidence": calibrated_confidence,
            "candidate_scores": route_scores,
            "candidate_evidence": route_evidence,
            "top_route": top_route,
            "confidence_margin": round(margin, 6),
            "confidence_formula": {
                "source": "deterministic_supervisor_v2",
                "llm_confidence_used": False,
                "expression": "0.45*route_evidence + 0.25*candidate_separation + 0.20*critical_checks + 0.10*advisory_checks, then deterministic penalties",
                "weights": cls.CONFIDENCE_WEIGHTS,
                "components": components,
                "penalties": applied_penalties,
            },
            "checks": checks,
            "critical_failures": critical_failures,
            "warning_nodes": warning_nodes,
            "missing_lammps_slots": missing_lammps_slots,
            "dag": {
                "plan_id": plan.plan_id,
                "valid": dag_valid,
                "topological_order": dag_order,
                "node_count": len(plan.nodes),
                "error": dag_error,
            },
            "llm_reviewed": False,
        }

    @classmethod
    def _with_supervisor_audit(cls, state: AgentGraphState, decision: dict[str, Any]) -> dict[str, Any]:
        audit = cls._supervisor_audit(state, decision)
        return {
            **decision,
            "confidence": audit["calibrated_confidence"],
            "supervisor_audit": audit,
        }

    @classmethod
    def _safe_fallback_after_dag_failure(
        cls,
        state: AgentGraphState,
        rejected: dict[str, Any],
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        route_name = str(rejected.get("route_name") or "")
        failed = set(str(item) for item in audit.get("critical_failures", []))
        warnings = set(str(item) for item in audit.get("warning_nodes", []))
        if "domain_ambiguity" in warnings:
            intent = "clarify_ambiguous_request"
            reason = "Phase-diagram and LAMMPS execution signals overlap. Ask the user to choose one execution objective before continuing."
        elif "asset_prerequisite" in failed:
            intent = "clarify_image_request"
            reason = "The selected route requires an uploaded image or reusable recognition context. Ask for the missing image before execution."
        elif "compute_prerequisite" in failed and route_name == "phase_diagram.generate":
            intent = "clarify_phase_request"
            reason = "The phase-diagram route is missing a material system. Ask for the binary/ternary system before execution."
        elif "compute_prerequisite" in failed and route_name == "lammps.generate":
            intent = "clarify_lammps_request"
            missing = audit.get("missing_lammps_slots") or []
            reason = f"The LAMMPS route is missing required slots: {', '.join(str(item) for item in missing)}."
        else:
            intent = "clarify_ambiguous_request"
            reason = "The proposed route failed the supervisor DAG contract. Ask for clarification instead of executing an unsafe path."

        fallback: dict[str, Any] = {
            "route_name": "conversation.answer",
            "next_step": "chat",
            "compute_domain": "none",
            "intent": intent,
            "reason": reason,
            "source": "supervisor_dag_fallback",
        }
        if intent == "clarify_lammps_request":
            fallback["clarification_slots"] = list(audit.get("missing_lammps_slots") or [])
        fallback_audit = cls._supervisor_audit(state, fallback)
        fallback_audit["llm_reviewed"] = bool(audit.get("llm_reviewed"))
        if fallback_audit["llm_reviewed"]:
            fallback_audit["requires_llm_review"] = False
        fallback_audit["rejected_route"] = route_name
        fallback_audit["rejected_failures"] = sorted(failed)
        return {
            **fallback,
            "confidence": fallback_audit["calibrated_confidence"],
            "supervisor_audit": fallback_audit,
        }

    @classmethod
    def _needs_llm_review(cls, state: AgentGraphState, heuristic: dict[str, Any]) -> bool:
        """Reserve the LLM supervisor for genuinely ambiguous routing cases.

        Explicit image, compute, clarification, and ordinary chat intents are
        already covered by deterministic rules. Running another model call for
        those cases adds latency without changing the route in normal traffic.
        """

        if str(heuristic.get("intent") or "") == "clarify_lammps_request":
            return False
        audit = heuristic.get("supervisor_audit") if isinstance(heuristic.get("supervisor_audit"), dict) else {}
        if audit.get("requires_llm_review") is True:
            return True
        message = state["request"].message
        lowered = message.lower()
        if float(heuristic.get("confidence") or 0.0) < 0.75:
            return True
        if len(message) > 1400:
            return True
        mentions_phase = "相图" in message or "phase diagram" in lowered
        mentions_lammps = "lammps" in lowered or "分子动力学" in message
        if mentions_phase and mentions_lammps:
            return True
        last_run = state.get("last_run_context")
        generic_follow_up = any(token in message for token in ("改一下", "继续", "再来", "重新做", "按刚才的"))
        if last_run and last_run.run_id and generic_follow_up and len(message) < 80:
            return True
        return False

    def decide(self, state: AgentGraphState) -> dict[str, Any]:
        heuristic = self._with_supervisor_audit(state, self._heuristic_decision(state))
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="SupervisorAgent", capability="任务路由决策")
            heuristic_audit = heuristic.get("supervisor_audit") or {}
            if heuristic_audit.get("passed") is False:
                return self._safe_fallback_after_dag_failure(state, heuristic, heuristic_audit)
            return heuristic

        if not self._needs_llm_review(state, heuristic):
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
                    "4b) lammps.generate means real local LAMMPS execution by default. Never require the user to say 'real' or 'no mock'; mock is only an explicit developer/demo opt-in and must not be inferred from an ordinary simulation request. "
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
                    "Return JSON with keys: route_name, next_step, compute_domain, intent, reason.\n"
                    "Valid next_step values: chat, recognition, compute.\n"
                    "Valid compute_domain values: none, phase_diagram, lammps.\n"
                    "Use the heuristic baseline only as a fallback reference; make an independent routing judgment from the current conversation context.\n"
                    f"User message:\n{request.message}\n\n"
                    f"Conversation summary:\n{state.get('current_context_summary', '')}\n\n"
                    f"Recent history:\n{json.dumps(history, ensure_ascii=False)}\n\n"
                    f"Uploaded assets:\n{json.dumps(assets, ensure_ascii=False)}\n\n"
                    f"Recognition result:\n{recognition_result.model_dump_json() if recognition_result else '{}'}\n\n"
                    f"Last run context:\n{last_run_context.model_dump_json() if last_run_context else '{}'}\n\n"
                    f"Heuristic baseline and DAG audit:\n{json.dumps(heuristic, ensure_ascii=False)}"
                ),
                max_tokens=600,
                temperature=0.1,
                capability="supervisor.route",
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
        # The selected route owns its execution contract. LLM-provided
        # next_step/compute_domain values are advisory and cannot violate the
        # Supervisor DAG.
        next_step, compute_domain = self.ROUTE_CONTRACTS[route_name]

        canonical_intent = str(payload.get("intent") or heuristic["intent"]).strip() or heuristic["intent"]
        heuristic_audit = heuristic.get("supervisor_audit") or {}
        heuristic_failures = set(str(item) for item in heuristic_audit.get("critical_failures", []))
        heuristic_route = str(heuristic.get("route_name") or "")
        if route_name == "conversation.answer":
            if "asset_prerequisite" in heuristic_failures:
                canonical_intent = "clarify_image_request"
            elif "compute_prerequisite" in heuristic_failures and heuristic_route == "phase_diagram.generate":
                canonical_intent = "clarify_phase_request"
            elif "compute_prerequisite" in heuristic_failures and heuristic_route == "lammps.generate":
                canonical_intent = "clarify_lammps_request"

        try:
            llm_reported_confidence = max(0.0, min(float(payload.get("confidence")), 1.0))
        except (TypeError, ValueError):
            llm_reported_confidence = None

        llm_decision: dict[str, Any] = {
            "route_name": route_name,
            "next_step": next_step,
            "compute_domain": compute_domain,
            "intent": canonical_intent,
            "reason": str(payload.get("reason") or heuristic["reason"]).strip() or heuristic["reason"],
            "source": "llm_supervisor",
            # Retained only for offline calibration analysis. It never enters
            # the runtime confidence formula or an execution gate.
            "llm_reported_confidence": llm_reported_confidence,
        }
        final_audit = self._supervisor_audit(state, llm_decision)
        final_audit["llm_reviewed"] = True
        final_audit["requires_llm_review"] = False
        final_audit["llm_reported_confidence"] = llm_reported_confidence
        if not final_audit["passed"]:
            return self._safe_fallback_after_dag_failure(state, llm_decision, final_audit)
        return {
            **llm_decision,
            "confidence": final_audit["calibrated_confidence"],
            "supervisor_audit": final_audit,
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
