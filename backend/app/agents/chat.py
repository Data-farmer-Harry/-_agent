from __future__ import annotations

import html as html_lib
import json
import re
from time import perf_counter

from app.config import settings
from app.core.artifacts import ArtifactService
from app.core.llm import LLMClient, LLMRequiredError
from app.core.llm_capabilities import LLMCapability
from app.materials_rag.normalizer import extract_materials, infer_domain_hint, normalize_material
from app.materials_rag.service import MaterialsRagService
from app.materials_rag.context_builder import build_materials_rag_context
from app.recognition_reconstruction.service import RecognitionReconstructionService
from app.state import (
    AgentGraphState,
    ArtifactRef,
    AxisSpec,
    CriticalPoint,
    ConversationTurn,
    LastRunContext,
    MemorySnapshot,
    PromptSuggestionRequest,
    PromptSuggestionResponse,
    RecognitionResult,
    ResultProfile,
    RunRecordSummary,
)
from app.thermo.registry import get_thermo_database_card
from app.utils.path_utils import write_json_file, write_text_file


class ChatAgent:
    CODE_PATTERN = re.compile(r"(代码|code)", flags=re.IGNORECASE)
    CORRECTNESS_PATTERN = re.compile(r"(对不对|正确吗|靠谱吗|准确吗|可信吗|可靠)")
    EXPLAIN_PATTERN = re.compile(r"(解释|讲解|怎么看|说明|这图|这张图|图里|图上|读图)")
    TRACE_PATTERN = re.compile(r"(流程|链路|怎么生成|怎么做的|经过了哪些步骤)")
    INTERACTIVE_HTML_PATTERN = re.compile(
        r"(交互式\s*html|交互式\s*页面|交互式\s*结果|interactive\s*html|result\.html|html\s*文件)",
        flags=re.IGNORECASE,
    )
    DATA_IMAGE_SRC_PATTERN = re.compile(
        r"\bsrc\s*=\s*([\"'])(data:image/(?:png|jpeg|jpg|webp|gif);base64,[^\"']+)\1",
        flags=re.IGNORECASE,
    )
    RECOGNITION_PATTERN = re.compile(r"(识别结果|识别到了什么|看到了什么)")
    LAMMPS_PATTERN = re.compile(r"(lammps|md|分子动力学|模拟|轨迹|video|gif|ovito|report|thermo)", flags=re.IGNORECASE)
    EUTECTOID_PATTERN = re.compile(r"(共析点|共析反应)")
    PERITECTIC_PATTERN = re.compile(r"(包晶|包晶反应)")
    MATERIALS_RAG_PATTERN = re.compile(
        r"(fix\s+nvt|fix\s+npt|fix\s+nph|pair_style|pair\s+coeff|eam|lj|openkim|msd|rdf|lost atoms|non-numeric pressure|"
        r"langevin|deform|meam|tersoff|reaxff|buckingham|mliap|centro|cna|voronoi|stress/atom|"
        r"materials project|jarvis|aflow|matminer|formation energy|energy above hull|band gap|bandgap|elastic|phonon|defect|"
        r"vacancy|interstitial|substitutional|dislocation|grain boundary|stacking fault|arrhenius|high entropy alloy|"
        r"势函数|热浴|恒温系综|恒压恒温|扩散系数|径向分布函数|液相线|固相线|热力学|材料基础|lammps\s*报错|报错怎么办|"
        r"形成能|凸包|带隙|弹性|声子|缺陷|空位|间隙原子|替位|位错|晶界|层错|激活能|高熵合金|"
        r"共晶|共析|包晶|杠杆定律|连线|calphad|gibbs|相律|自由度|混溶间隙|"
        r"马氏体|奥氏体|铁素体|渗碳体|珠光体|贝氏体|细晶强化|米勒指数|"
        r"xrd|ebsd|eds|edx|dsc|物相鉴定|金相|纳米压痕|形状记忆合金|金属玻璃|腐蚀|"
        r"蠕变|调幅分解|旋节分解|加工-结构-性能-服役)",
        flags=re.IGNORECASE,
    )
    EXPLICIT_RAG_PATTERN = re.compile(
        r"(知识库|知识增强|检索|查资料|依据|引用|来源|文献|参考资料|reference|citation|grounded|rag)",
        flags=re.IGNORECASE,
    )
    HIGH_VALUE_RAG_PATTERN = re.compile(
        r"(fix\s+nvt|fix\s+npt|fix\s+nph|pair_style|pair\s+coeff|thermo_style|stress/atom|centro/atom|"
        r"cna/atom|voronoi/atom|lost atoms|non-numeric pressure|out of range atoms|bond atoms missing|"
        r"illegal command|shake atoms missing|lammps\s*报错|报错怎么办|error\b|eam|openkim|meam|tersoff|"
        r"reaxff|buckingham|mliap|势函数|potential|materials project|jarvis|aflow|matminer|"
        r"formation energy|energy above hull|band gap|bandgap|晶体结构|晶格常数|形成能|凸包|带隙)",
        flags=re.IGNORECASE,
    )
    COMPLEX_RAG_PATTERN = re.compile(
        r"(为什么|为何|如何|机制|机理|影响|比较|评估|验证|选择|适合|偏差|稳定性|训练域|"
        r"耦合|失效|根因|诊断|是否意味着|how|why|mechanism|compare|validate|diagnos)",
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        artifact_service: ArtifactService | None = None,
        materials_rag_service: MaterialsRagService | None = None,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.artifact_service = artifact_service or ArtifactService()
        self.materials_rag_service = materials_rag_service or MaterialsRagService()

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
        self.llm_client.require_configured(agent_name="ChatAgent", capability=LLMCapability.PROMPT_SUGGEST)

        snapshot = memory_snapshot or MemorySnapshot(conversation_id=request.conversation_id)
        conversation_history = request.conversation_history or snapshot.messages
        last_run_context = request.last_run_context if request.last_run_context.run_id else snapshot.last_run_context
        current_context_summary = request.current_context_summary or snapshot.current_context_summary
        resolved_recognition = recognition_result or snapshot.recognition_result
        long_term_summary = snapshot.long_term.strategic_summary
        retrieval_query = request.draft_message or (
            request.conversation_history[-1].content if request.conversation_history else ""
        )
        long_term_hits = []
        if memory_snapshot is not None:
            from app.memory import build_long_term_memory_hits

            long_term_hits = build_long_term_memory_hits(
                query=retrieval_query or current_context_summary or long_term_summary,
                snapshot=snapshot.long_term,
                limit=5,
            )

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
                f"Long-term memory:\n{long_term_summary or '(none)'}\n\n"
                f"Retrieved long-term hints:\n{json.dumps(long_term_hits, ensure_ascii=False)}\n\n"
                f"Last run context:\n{last_run_context.model_dump_json()}\n\n"
                f"Recognition result:\n{resolved_recognition.model_dump_json() if resolved_recognition else '{}'}\n\n"
                f"Recent conversation turns:\n{self._compact_turns(conversation_history)}\n\n"
                "Return only the recommended next prompt."
            ),
            max_tokens=220,
            temperature=0.45,
            capability=LLMCapability.PROMPT_SUGGEST,
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

    @staticmethod
    def _safe_float(value: object, default: float | None = None) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _extract_embedded_phase_image_data_url(cls, html_content: str | None) -> str | None:
        if not html_content:
            return None
        match = cls.DATA_IMAGE_SRC_PATTERN.search(html_content)
        return match.group(2) if match else None

    @classmethod
    def _temperature_range_from_context(cls, context: LastRunContext, summary: dict[str, object] | None) -> tuple[float, float]:
        if summary:
            raw_range = summary.get("temperature_range")
            if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
                low = cls._safe_float(raw_range[0])
                high = cls._safe_float(raw_range[1])
                if low is not None and high is not None and high > low:
                    return low, high

        hints = " ".join(part for part in [context.request_summary, context.final_message, context.review_summary] if part)
        kelvin_values = [float(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*K\b", hints, flags=re.IGNORECASE)]
        if len(kelvin_values) >= 2:
            low, high = sorted(kelvin_values[:2])
            if high > low:
                return low, high
        return 300.0, 1800.0

    @staticmethod
    def _phase_list_from_context(context: LastRunContext, summary: dict[str, object] | None) -> list[str]:
        phases: list[str] = []
        if summary:
            accuracy = summary.get("accuracy")
            if isinstance(accuracy, dict):
                stable_phases = accuracy.get("stable_phases_seen")
                if isinstance(stable_phases, list):
                    phases.extend(str(phase) for phase in stable_phases if phase)

        card = get_thermo_database_card(context.system_name)
        if card is not None:
            phases.extend(card.phases)

        seen: set[str] = set()
        normalized: list[str] = []
        for phase in phases:
            label = str(phase).strip()
            if not label or label in seen:
                continue
            seen.add(label)
            normalized.append(label)
        return normalized or ["LIQUID", "Solid solution", "Secondary phase"]

    @staticmethod
    def _is_phase_run_context(context: LastRunContext | None) -> bool:
        if context is None or not context.run_id:
            return False
        return context.compute_domain == "phase_diagram" or context.route_name in {"phase_diagram.generate", "mixed.request"}

    @staticmethod
    def _request_summary_from_record(record: RunRecordSummary) -> str:
        summary = record.summary if isinstance(record.summary, dict) else {}
        system_name = str(summary.get("system_name") or "").strip()
        diagram_type = str(summary.get("diagram_type") or "").strip()
        temperature_range = summary.get("temperature_range")
        if isinstance(temperature_range, (list, tuple)) and len(temperature_range) >= 2 and system_name:
            return f"{system_name} {diagram_type} {temperature_range[0]}-{temperature_range[1]} K".strip()
        if system_name:
            return " ".join(part for part in [system_name, diagram_type] if part).strip()
        return str(summary.get("request_message") or "").strip()[:240]

    @classmethod
    def _phase_context_from_run_record(cls, record: RunRecordSummary) -> LastRunContext:
        summary = record.summary if isinstance(record.summary, dict) else {}
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        review = metadata.get("review") if isinstance(metadata.get("review"), dict) else {}
        return LastRunContext(
            run_id=record.run_id,
            route_name=record.route.name,
            compute_domain="phase_diagram",
            system_name=str(summary.get("system_name") or ""),
            final_message=record.final_message[:1200],
            generated_code_preview="",
            review_summary=str(review.get("summary") or ""),
            selected_tool=record.route.selected_tool or "",
            generation_source=str(metadata.get("generation_source") or summary.get("generation_source") or ""),
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

    def _resolve_recent_phase_context(self, state: AgentGraphState) -> LastRunContext | None:
        context = state.get("last_run_context")
        if self._is_phase_run_context(context):
            return context

        conversation_id = state.get("conversation_id") or state["request"].conversation_id
        for record in self.artifact_service.list_run_summaries(limit=200):
            if record.conversation_id != conversation_id:
                continue
            if record.route.name not in {"phase_diagram.generate", "mixed.request"}:
                continue
            if not any(artifact.name == "result.html" for artifact in record.artifacts):
                continue
            return self._phase_context_from_run_record(record)
        return None

    def _build_phase_result_proxy(self, context: LastRunContext) -> RecognitionResult:
        record = self.artifact_service.load_run_summary(context.run_id) if context.run_id else None
        summary = record.summary if record is not None else {}
        system_name = str(summary.get("system_name") or context.system_name or "Calculated phase diagram")
        temperature_min, temperature_max = self._temperature_range_from_context(context, summary)
        card = get_thermo_database_card(system_name)
        x_axis_label = card.x_axis_label if card is not None else ""
        if not x_axis_label:
            parts = [part.strip() for part in system_name.split("-") if part.strip()]
            x_component = parts[-1] if len(parts) >= 2 else "B"
            x_axis_label = f"Mole fraction {x_component}"

        result_profile = summary.get("result_profile")
        confidence = 0.86
        if isinstance(result_profile, dict):
            confidence = self._safe_float(result_profile.get("confidence"), confidence) or confidence
        accuracy = summary.get("accuracy")
        if isinstance(accuracy, dict) and accuracy.get("passed") is True:
            confidence = max(confidence, 0.90)

        cp_temperature = temperature_min + (temperature_max - temperature_min) * 0.55
        critical_points = [
            CriticalPoint(
                label="simulated eutectic point",
                composition=0.5,
                temperature=cp_temperature,
                notes="Generated as an adjustable invariant-point proxy from the previous calculated phase-diagram result.",
            )
        ]
        raw_summary = (
            f"上一轮 {system_name} 相图结果已被转换为交互式模拟器。"
            "该面板复用已计算相图的体系、温度范围和相区信息，滑条变化为几何模拟，不代表重新求解热力学平衡。"
        )
        return RecognitionResult(
            system=system_name,
            diagram_type=str(summary.get("diagram_type") or "binary"),  # type: ignore[arg-type]
            x_axis=AxisSpec(label=x_axis_label, minimum=0.0, maximum=1.0, unit=""),
            y_axis=AxisSpec(label="Temperature", minimum=temperature_min, maximum=temperature_max, unit="K"),
            phases=self._phase_list_from_context(context, summary),
            critical_points=critical_points,
            labels=[system_name, "interactive simulator", "calculated phase diagram"],
            confidence=confidence,
            source="phase_diagram_result_reconstruction",
            raw_summary=raw_summary,
        )

    @staticmethod
    def _build_phase_result_profile(context: LastRunContext, recognition_result: RecognitionResult) -> ResultProfile:
        return ResultProfile(
            category="Calculated Result Simulator",
            source_label="previous pycalphad phase-diagram result",
            mode_label="interactive geometric simulator",
            trust_level="medium",
            confidence=recognition_result.confidence,
            trust_statement=(
                "This panel is generated from the previous calculated phase-diagram result and rendered deterministically in the chat page. "
                "The sliders move a geometric invariant-point projection; they do not launch a new pycalphad equilibrium solve."
            ),
            assumptions=[
                "System, temperature range, and phase labels come from the previous calculated run context.",
                "The eutectic/key-point motion is a qualitative interactive projection for exploration and UI review.",
            ],
            warnings=[
                "Slider movement is not a new thermodynamic calculation.",
                "Use the original calculated result for quantitative phase-boundary interpretation.",
            ],
            evidence=[
                f"Origin run: {context.run_id}",
                f"Origin system: {recognition_result.system}",
                f"Phases: {', '.join(recognition_result.phases[:6])}",
                "Rendering pipeline: calculated run -> schema -> validator -> curve fitting -> deterministic HTML renderer",
            ],
        )

    def _build_phase_result_simulator_payload(self, state: AgentGraphState) -> dict | None:
        request = state["request"]
        context = self._resolve_recent_phase_context(state)
        if not self._is_phase_run_context(context):
            return None
        if not self.INTERACTIVE_HTML_PATTERN.search(request.message):
            return None

        target_run_id = str(state.get("run_id") or context.run_id)
        origin_html = self.artifact_service.load_run_html(context.run_id)
        source_image_data_url = self._extract_embedded_phase_image_data_url(origin_html)
        recognition_result = self._build_phase_result_proxy(context)
        reconstruction_service = RecognitionReconstructionService()
        schema = reconstruction_service.build_schema(
            recognition_result,
            request_message=request.message,
            source_image_data_url=source_image_data_url,
        )
        schema = schema.model_copy(
            update={
                "warnings": [
                    "This simulator is reconstructed from the previous calculated phase-diagram result via internal image-aware recognition, not from a fresh image upload.",
                    "Static HTML reconstruction is currently prioritized over interactive deformation to improve visual fidelity.",
                ],
                "notes": [
                    "The previous calculated phase image is used only as an internal recognition input when available; it is never embedded into the final HTML.",
                    "The LLM does not directly write the final HTML; it only supplies run context and routing intent.",
                    "Schema validation, tracing, contour vectorization, and HTML/canvas rendering are deterministic.",
                    "The current priority is visual reconstruction fidelity, so interactive controls may be reduced or deferred.",
                ],
                "raw_summary": recognition_result.raw_summary,
            }
        )
        if source_image_data_url:
            geometry = reconstruction_service.fit_geometry_from_image(
                schema,
                source_image_data_url=source_image_data_url,
            )
            simulation_render_mode = "image_aware_vector_canvas_reconstruction"
        else:
            geometry = reconstruction_service.fit_geometry(schema)
            simulation_render_mode = "deterministic_canvas_schema_reconstruction"
        result_profile = self._build_phase_result_profile(context, recognition_result)
        html_content = reconstruction_service.render_html(
            schema,
            geometry,
            result_profile,
            source_image_data_url=source_image_data_url,
        )
        result_path = self.artifact_service.get_result_path(target_run_id)
        write_text_file(result_path, html_content)
        json_path = self.artifact_service.get_artifact_path(target_run_id, "phase_result_simulator.json")
        write_json_file(
            json_path,
            {
                "origin_run_id": context.run_id,
                "recognition_proxy": recognition_result.model_dump(mode="json"),
                "reconstruction_schema": schema.model_dump(mode="json"),
                "geometry_model": geometry.model_dump(mode="json"),
                "result_profile": result_profile.model_dump(mode="json"),
                "simulation_render_mode": simulation_render_mode,
                "source_image_found": bool(source_image_data_url),
                "source_image_inference_used": bool(source_image_data_url),
                "source_image_used": False,
            },
        )
        artifacts = [
            self.artifact_service.build_artifact_ref(
                kind="html",
                name="result.html",
                path=result_path,
                content=None,
                url=self.artifact_service.build_artifact_url(target_run_id, "result.html"),
                metadata={
                    "source": "phase_diagram_followup_interactive_simulator",
                    "origin_run_id": context.run_id,
                    "system_name": recognition_result.system,
                    "mode": "calculated_result_to_interactive_simulator",
                    "simulation_render_mode": simulation_render_mode,
                    "source_image_found": bool(source_image_data_url),
                    "source_image_inference_used": bool(source_image_data_url),
                    "source_image_used": False,
                },
            ),
            self.artifact_service.build_artifact_ref(
                kind="json",
                name="phase_result_simulator.json",
                path=json_path,
                content=None,
                url=self.artifact_service.build_artifact_url(target_run_id, "phase_result_simulator.json"),
                metadata={
                    "source": "phase_diagram_followup_interactive_simulator",
                    "origin_run_id": context.run_id,
                    "mode": "schema_and_geometry",
                },
            ),
        ]
        system_hint = f"{recognition_result.system} " if recognition_result.system else ""
        answer = (
            f"我已经先对上一轮 {system_hint}相图做了内部识别，再把它重建为不含原始图像的交互式 HTML，并直接渲染在当前对话里。"
            "最终展示的是新生成的 HTML/canvas，相图原始图像只在内部识别阶段使用，不会直接回显上一轮结果页。"
        )
        return {
            "answer": answer,
            "artifact_messages": artifacts,
            "html_content": html_content,
            "html_path": str(result_path),
            "response_metadata": {
                "generated_phase_simulator": True,
                "origin_run_id": context.run_id,
                "origin_route_name": context.route_name,
                "simulation_mode": "calculated_result_to_interactive_simulator",
                "simulation_render_mode": simulation_render_mode,
                "source_image_found": bool(source_image_data_url),
                "source_image_inference_used": bool(source_image_data_url),
                "source_image_used": False,
            },
            "response_summary": {
                "followup_action": "generate_phase_result_interactive_simulator",
                "origin_run_id": context.run_id,
                "interactive_controls": ["temperature", "pressure_factor"],
                "simulation_render_mode": simulation_render_mode,
                "source_image_found": bool(source_image_data_url),
                "source_image_inference_used": bool(source_image_data_url),
                "source_image_used": False,
                "reconstruction_schema": schema.model_dump(mode="json"),
                "geometry_model": geometry.model_dump(mode="json"),
                "result_profile": result_profile.model_dump(mode="json"),
            },
            "termination_reason": "conversation_answered_with_html_artifact",
        }

    def _build_phase_html_followup_payload(self, state: AgentGraphState) -> dict | None:
        request = state["request"]
        context = self._resolve_recent_phase_context(state)
        if not self._is_phase_run_context(context):
            return None
        if not self.INTERACTIVE_HTML_PATTERN.search(request.message):
            return None

        try:
            simulator_payload = self._build_phase_result_simulator_payload(state)
        except Exception:  # noqa: BLE001
            simulator_payload = None
        return simulator_payload


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

    @staticmethod
    def _build_lammps_clarification_answer(state: AgentGraphState) -> str | None:
        decision = state.get("supervisor_decision") or {}
        if decision.get("intent") != "clarify_lammps_request":
            return None

        missing = [str(item) for item in decision.get("clarification_slots", []) if str(item).strip()]
        labels = {
            "material": "材料（目前支持 Al / Cu / Ni）",
            "task_type": "任务类型（heating / equilibration，或说明用 NVT/NPT 平衡）",
            "temperature": "目标温度（K）",
            "steps": "运行步数（steps）",
        }
        needed = "、".join(labels.get(item, item) for item in missing) or "材料、任务类型、温度和步数"
        return (
            "这条看起来是要跑 LAMMPS，但我不想替你偷填关键参数然后误跑。"
            f"还需要你补充：{needed}。\n"
            "你可以直接这样说：`请用 LAMMPS 做 Cu heating，800K，4000 steps，NVT，EAM 势函数。`"
        )

    @staticmethod
    def _build_supervisor_clarification_answer(state: AgentGraphState) -> str | None:
        decision = state.get("supervisor_decision") or {}
        intent = str(decision.get("intent") or "")
        if intent == "clarify_phase_request":
            return (
                "可以生成相图，但还缺少材料体系。请告诉我是哪个二元或三元体系，"
                "例如 `Al-Zn 二元相图`；如果你有指定温度范围，也可以一起给我。"
            )
        if intent == "clarify_image_request":
            return "这条任务需要读取原图，但当前对话里没有可用图片。请上传相图或截图后再发送识别要求。"
        if intent == "clarify_ambiguous_request":
            return (
                "这条请求同时包含了不同执行方向，我暂时不能安全确定路线。"
                "请明确你要的是：LAMMPS 模拟、新生成相图，还是识别一张已上传的相图。"
            )
        return None

    def _build_contextual_answer(self, state: AgentGraphState) -> str | None:
        clarification = self._build_supervisor_clarification_answer(state)
        if clarification is not None:
            return clarification
        clarification = self._build_lammps_clarification_answer(state)
        if clarification is not None:
            return clarification
        request = state["request"]
        if state.get("phase_diagram_result") is not None:
            return self._build_phase_followup_answer(state)
        if state.get("lammps_result") is not None:
            return self._build_lammps_followup_answer(state)
        if state.get("recognition_result") is not None and (
            state.get("route") and state["route"].name in {"recognition.analyze", "mixed.request"}
            or self.RECOGNITION_PATTERN.search(request.message)
            or self.EXPLAIN_PATTERN.search(request.message)
        ):
            return self._format_recognition(state["recognition_result"])
        if state.get("last_run_context") and state["last_run_context"].route_name == "phase_diagram.generate":
            if any(pattern.search(request.message) for pattern in (self.CODE_PATTERN, self.CORRECTNESS_PATTERN, self.EXPLAIN_PATTERN, self.TRACE_PATTERN)):
                return self._build_phase_followup_answer(state)
        if state.get("last_run_context") and state["last_run_context"].route_name == "lammps.generate":
            if any(pattern.search(request.message) for pattern in (self.CODE_PATTERN, self.CORRECTNESS_PATTERN, self.EXPLAIN_PATTERN, self.TRACE_PATTERN, self.LAMMPS_PATTERN)):
                return self._build_lammps_followup_answer(state)
        return None

    @classmethod
    def _materials_rag_gate(cls, state: AgentGraphState, contextual_answer: str | None) -> dict[str, object]:
        """Decide whether retrieval is worth paying for on this turn.

        RAG is an on-demand grounding tool, not a mandatory ChatAgent stage.
        Cheap definitions, greetings, project questions, and grounded follow-ups
        stay on the direct LLM path. Retrieval is reserved for explicit source
        requests, high-value technical lookups, and complex specialist queries.
        """

        message = state["request"].message
        route = state.get("route")
        intent = route.intent if route is not None else ""
        domain = infer_domain_hint(message)
        is_materials_query = bool(domain or cls.MATERIALS_RAG_PATTERN.search(message))
        explicit_grounding = bool(cls.EXPLICIT_RAG_PATTERN.search(message))

        if contextual_answer is not None and not (explicit_grounding and is_materials_query):
            return {"use": False, "reason": "contextual_follow_up", "domain_hint": domain}
        if intent in {
            "clarify_lammps_request",
            "clarify_phase_request",
            "clarify_image_request",
            "clarify_ambiguous_request",
            "follow_up_about_previous_run",
            "rehydrate_previous_phase_html",
        } and not explicit_grounding:
            return {"use": False, "reason": "route_does_not_need_retrieval", "domain_hint": domain}
        if explicit_grounding and is_materials_query:
            return {"use": True, "reason": "explicit_grounding_request", "domain_hint": domain}
        if cls.HIGH_VALUE_RAG_PATTERN.search(message):
            return {"use": True, "reason": "specialist_lookup", "domain_hint": domain}
        if (
            is_materials_query
            and len(message.strip()) >= 18
            and cls.COMPLEX_RAG_PATTERN.search(message)
        ):
            return {"use": True, "reason": "complex_specialist_question", "domain_hint": domain}
        return {"use": False, "reason": "direct_answer_sufficient", "domain_hint": domain}

    @classmethod
    def _should_use_materials_rag(cls, state: AgentGraphState, contextual_answer: str | None) -> bool:
        return bool(cls._materials_rag_gate(state, contextual_answer)["use"])

    @classmethod
    def _infer_materials_rag_filters(cls, state: AgentGraphState) -> dict[str, str | None]:
        message = state["request"].message
        lowered = message.lower()
        domain = infer_domain_hint(message)
        doc_type: str | None = None
        if any(
            token in lowered or token in message
            for token in (
                "lost atoms",
                "non-numeric pressure",
                "pair coeff",
                "out of range atoms",
                "bond atoms missing",
                "illegal command",
                "shake atoms missing",
                "报错",
                "error",
            )
        ):
            domain = "lammps"
            doc_type = "error_cookbook"
        elif any(
            token in lowered or token in message
            for token in ("eam", "lj", "openkim", "meam", "tersoff", "reaxff", "buckingham", "mliap", "势函数", "potential")
        ):
            domain = "lammps"
            doc_type = "potential_card"
        elif any(
            token in lowered or token in message
            for token in (
                "fix nvt",
                "fix npt",
                "fix nph",
                "fix langevin",
                "fix deform",
                "pair_style",
                "msd",
                "rdf",
                "thermo_style",
                "stress/atom",
                "centro/atom",
                "cna/atom",
                "voronoi/atom",
            )
        ):
            domain = "lammps"
            doc_type = "command_card"
        elif any(token in lowered or token in message for token in ("共晶", "共析", "包晶", "液相线", "固相线", "杠杆定律", "calphad", "gibbs")):
            domain = "thermodynamics"
            doc_type = None
        elif any(
            token in lowered or token in message
            for token in (
                "materials project",
                "jarvis",
                "aflow",
                "matminer",
                "formation energy",
                "energy above hull",
                "band gap",
                "bandgap",
                "elastic",
                "phonon",
                "defect",
                "vacancy",
                "interstitial",
                "substitutional",
                "dislocation",
                "grain boundary",
                "stacking fault",
                "arrhenius",
                "形成能",
                "凸包",
                "带隙",
                "弹性",
                "声子",
                "缺陷",
                "空位",
                "间隙原子",
                "替位",
                "位错",
                "晶界",
                "层错",
                "激活能",
            )
        ):
            domain = "materials"
            doc_type = None

        material = next(iter(extract_materials(message)), None)
        if material is None:
            last_run_context = state.get("last_run_context")
            material = normalize_material(last_run_context.system_name if last_run_context else None)
        return {"domain": domain, "doc_type": doc_type, "material": material}

    def _resolve_materials_rag(self, state: AgentGraphState, contextual_answer: str | None) -> dict[str, object]:
        started_at = perf_counter()
        gate = self._materials_rag_gate(state, contextual_answer)
        if not gate["use"]:
            return {
                "hits": [],
                "context": "",
                "domain": None,
                "doc_type": None,
                "material": None,
                "duration_ms": 0.0,
                "gate_reason": gate["reason"],
                "requested": False,
            }

        filters = self._infer_materials_rag_filters(state)
        hits = self.materials_rag_service.search(
            state["request"].message,
            domain=filters["domain"],
            doc_type=filters["doc_type"],
            material=filters["material"],
            top_k=4,
        )
        # Reuse the same retrieval result. build_context() performs a search of
        # its own and previously doubled query embedding/reranker calls.
        context = build_materials_rag_context(
            query=state["request"].message,
            hits=hits,
            max_items=3,
        )
        return {
            "hits": hits,
            "context": context,
            "domain": filters["domain"],
            "doc_type": filters["doc_type"],
            "material": filters["material"],
            "evidence_hits": [hit.model_dump(mode="json") for hit in hits],
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "gate_reason": gate["reason"],
            "requested": True,
        }

    @staticmethod
    def _build_materials_rag_fallback_answer(
        *,
        query: str,
        contextual_answer: str | None,
        rag_payload: dict[str, object],
    ) -> str | None:
        hits = list(rag_payload.get("hits", []))
        if not hits:
            return None

        lead = contextual_answer or "我先基于材料知识库给你一个保守解释。"
        snippets: list[str] = [lead]
        for hit in hits[:2]:
            document = hit.document
            snippets.append(f"{document.title}：{document.content}")
            if document.source:
                snippets.append(f"来源：{document.source}")
        snippets.append("这些内容是知识增强结果，不会覆盖真实 registry、参数校验或本地计算链路。")
        return "\n".join(snippets)

    @staticmethod
    def _build_concise_lammps_completion(state: AgentGraphState) -> str | None:
        result = state.get("lammps_result")
        if result is None:
            return None

        summary = result.summary if isinstance(result.summary, dict) else {}
        request_payload = summary.get("request") if isinstance(summary.get("request"), dict) else {}
        metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
        quality = summary.get("quality") if isinstance(summary.get("quality"), dict) else {}
        mode = str(summary.get("mode") or quality.get("run_mode") or "unknown")

        if not result.success:
            return "LAMMPS 本轮未通过完整执行或质量检查；错误、日志和已有产物已保留在结果区。"
        if mode != "real":
            return "本轮是 mock 流程演示，不可作为科学结果；结果区仅保留静态图和基础产物。"

        material = str(request_payload.get("material") or "材料")
        task_type = str(request_payload.get("task_type") or "simulation")
        initial_temp = request_payload.get("initial_temp")
        target_temp = request_payload.get("temperature")
        steps = request_payload.get("steps")
        final_temp = metrics.get("final_temp")
        quality_passed = bool(quality.get("scientific_result_passed"))

        conditions: list[str] = []
        if isinstance(initial_temp, (int, float)) and isinstance(target_temp, (int, float)):
            conditions.append(f"{initial_temp:g}→{target_temp:g} K")
        elif isinstance(target_temp, (int, float)):
            conditions.append(f"{target_temp:g} K")
        if isinstance(steps, (int, float)):
            conditions.append(f"{steps:g} steps")

        metric_text = f"最终温度 {final_temp:.1f} K" if isinstance(final_temp, (int, float)) else "结果已返回"
        quality_text = "质量门通过" if quality_passed else "请查看质量提示"
        condition_text = f"，{' / '.join(conditions)}" if conditions else ""
        return f"真实 LAMMPS 已完成：{material} {task_type}{condition_text}；{metric_text}，{quality_text}。GIF、MP4、热力学图和轨迹已放在结果区。"

    def _build_fallback_answer(self, state: AgentGraphState, contextual_answer: str | None) -> str:
        request = state["request"]
        tool_answer = self._build_tool_result_answer(state)
        if tool_answer:
            return tool_answer
        if contextual_answer is not None:
            return contextual_answer
        if self.EUTECTOID_PATTERN.search(request.message):
            return "共析点是某一固定成分下，一个固相在特定温度同时分解为两个新固相的点；它和共晶点不同，共析发生在固态。"
        if self.PERITECTIC_PATTERN.search(request.message):
            return "包晶反应通常指液相与一种固相在固定温度反应，生成另一种固相；它和共析最大的区别是包晶涉及液相，而共析发生在固态。"
        return "当前处于对话模式。你可以继续问材料概念、追问上一轮相图，或者上传截图让我先做识别。"

    @staticmethod
    def _format_tool_results(state: AgentGraphState) -> str:
        tool_results = state.get("tool_results", [])
        if not tool_results:
            return "(none)"
        compacted: list[dict[str, object]] = []
        for result in tool_results[-6:]:
            if not isinstance(result, dict):
                continue
            output = result.get("output") if isinstance(result.get("output"), dict) else {}
            output_text = json.dumps(output, ensure_ascii=False, default=str)
            if len(output_text) > 5000:
                output_text = output_text[:5000] + "..."
            compacted.append(
                {
                    "tool_name": result.get("tool_name"),
                    "success": result.get("success"),
                    "summary": result.get("summary"),
                    "output": output_text,
                    "artifacts": result.get("artifacts", []),
                    "error": result.get("error", ""),
                }
            )
        return json.dumps(compacted, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_tool_result_answer(state: AgentGraphState) -> str:
        tool_results = [result for result in state.get("tool_results", []) if isinstance(result, dict)]
        if not tool_results:
            return ""
        snippets: list[str] = []
        for result in tool_results:
            tool_name = str(result.get("tool_name") or "")
            success = bool(result.get("success"))
            output = result.get("output") if isinstance(result.get("output"), dict) else {}
            if not success:
                snippets.append(f"{tool_name} 执行失败：{result.get('error') or result.get('summary')}")
                continue
            if tool_name == "file.read":
                metadata = output.get("metadata", {}) if isinstance(output.get("metadata"), dict) else {}
                source = metadata.get("name") or metadata.get("path") or "上传文件"
                preview = str(output.get("preview") or "")
                if len(preview) > 1200:
                    preview = preview[:1200].rstrip() + "…"
                snippets.append(
                    f"已读取 {source}，共 {output.get('line_count', '?')} 行、{output.get('char_count', '?')} 字符。"
                    f"\n\n内容预览：\n{preview}"
                )
            elif tool_name == "workspace.search":
                matches = output.get("matches", []) if isinstance(output.get("matches"), list) else []
                lines = []
                for index, item in enumerate(matches[:8], start=1):
                    if not isinstance(item, dict):
                        continue
                    snippet = ""
                    snippets_payload = item.get("snippets", []) if isinstance(item.get("snippets"), list) else []
                    if snippets_payload and isinstance(snippets_payload[0], dict):
                        snippet = f" line {snippets_payload[0].get('line')}: {snippets_payload[0].get('text')}"
                    lines.append(f"{index}. {item.get('relative_path') or item.get('path')}{snippet}")
                snippets.append(
                    f"工作区搜索完成：query={output.get('query')!r}，匹配 {output.get('match_count', 0)} 个。"
                    + ("\n" + "\n".join(lines) if lines else "")
                )
            elif tool_name == "data.profile":
                profile = output.get("profile", {}) if isinstance(output.get("profile"), dict) else {}
                numeric = profile.get("numeric_columns", {}) if isinstance(profile.get("numeric_columns"), dict) else {}
                preview_cols = list(numeric.keys())[:8]
                snippets.append(
                    f"数据概况完成：type={profile.get('type')}，行数={profile.get('row_count', 'unknown')}，"
                    f"数值列={len(numeric)}。主要数值列：{preview_cols}。"
                )
            elif tool_name == "structure.convert":
                artifact = output.get("artifact", {}) if isinstance(output.get("artifact"), dict) else {}
                snippets.append(
                    f"结构转换完成：{output.get('source_format')} → {output.get('target_format')}，"
                    f"原子数 {output.get('atom_count')}，元素 {output.get('elements', [])}。"
                    f" 输出文件：{artifact.get('name') or 'converted_structure'}。"
                )
            elif tool_name == "physics.check":
                warnings = output.get("warnings", []) if isinstance(output.get("warnings"), list) else []
                recommendations = output.get("recommendations", []) if isinstance(output.get("recommendations"), list) else []
                conversions = output.get("conversions", {}) if isinstance(output.get("conversions"), dict) else {}
                snippets.append(
                    "物理/单位校验结果："
                    f"\n换算：{json.dumps(conversions, ensure_ascii=False)}"
                    f"\n风险：{'; '.join(str(item) for item in warnings) if warnings else '未发现明显红旗'}"
                    f"\n建议：{'; '.join(str(item) for item in recommendations)}"
                )
            elif tool_name == "report.generate":
                artifact = output.get("artifact", {}) if isinstance(output.get("artifact"), dict) else {}
                snippets.append(f"报告已生成：{artifact.get('name')}，路径：{artifact.get('path') or artifact.get('url')}")
            elif tool_name == "literature.search":
                results = output.get("results", []) if isinstance(output.get("results"), list) else []
                lines = []
                for index, item in enumerate(results[:5], start=1):
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        f"{index}. {item.get('title') or '(untitled)'}"
                        f" ({item.get('year') or 'n.d.'})"
                        f" — {item.get('authors') or 'unknown authors'}"
                        f" DOI: {item.get('doi') or 'N/A'}"
                    )
                snippets.append("文献候选：\n" + ("\n".join(lines) if lines else "未检索到候选。"))
            else:
                snippets.append(str(result.get("summary") or f"{tool_name} 已执行。"))
        return "\n\n".join(snippets)

    @staticmethod
    def _format_shared_memory_context(state: AgentGraphState) -> str:
        context = state.get("shared_memory_context") or {}
        if not isinstance(context, dict) or not context.get("selected_item_ids"):
            return "(none)"
        payload = {
            "selected_item_ids": context.get("selected_item_ids", []),
            "forced_retention_ids": context.get("forced_retention_ids", []),
            "retrieval_backend": context.get("retrieval_backend", ""),
            "items": [
                {
                    "memory_id": candidate.get("item", {}).get("memory_id"),
                    "item_type": candidate.get("item", {}).get("item_type"),
                    "subject": candidate.get("item", {}).get("subject"),
                    "predicate": candidate.get("item", {}).get("predicate"),
                    "value": candidate.get("item", {}).get("value"),
                    "unit": candidate.get("item", {}).get("unit"),
                    "text": candidate.get("item", {}).get("text"),
                    "authority": candidate.get("item", {}).get("authority"),
                    "reasons": candidate.get("reasons", []),
                }
                for candidate in context.get("candidates", [])[:8]
                if isinstance(candidate, dict)
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _format_skill_context(state: AgentGraphState) -> str:
        context = str(state.get("skill_context") or "").strip()
        if not context:
            return "(none)"
        if len(context) > 6000:
            return context[:5999] + "…"
        return context

    def run(self, state: AgentGraphState) -> dict:
        request = state["request"]
        phase_html_payload = self._build_phase_html_followup_payload(state)
        if phase_html_payload is not None:
            answer = phase_html_payload["answer"]
            return {
                "final_answer": answer,
                "messages": self._messages_with_answer(state, answer),
                "success": True,
                "error": "",
                "artifact_messages": phase_html_payload["artifact_messages"],
                "html_content": phase_html_payload["html_content"],
                "html_path": phase_html_payload["html_path"],
                "response_metadata": phase_html_payload["response_metadata"],
                "response_summary": phase_html_payload["response_summary"],
                "termination_reason": phase_html_payload["termination_reason"],
            }

        contextual_answer = self._build_contextual_answer(state)
        concise_lammps_answer = self._build_concise_lammps_completion(state)
        materials_rag_payload = self._resolve_materials_rag(state, contextual_answer)
        materials_rag_context = str(materials_rag_payload.get("context") or "")
        supervisor_intent = str((state.get("supervisor_decision") or {}).get("intent") or "")
        deterministic_clarification = supervisor_intent in {
            "clarify_lammps_request",
            "clarify_phase_request",
            "clarify_image_request",
            "clarify_ambiguous_request",
        }
        last_run_context = state.get("last_run_context")
        shared_memory_context = state.get("shared_memory_context") if isinstance(state.get("shared_memory_context"), dict) else {}
        skill_decision = state.get("skill_decision") if isinstance(state.get("skill_decision"), dict) else {}
        has_selected_skill = bool(skill_decision.get("selected_skills"))
        lean_direct_chat = bool(
            not materials_rag_context
            and contextual_answer is None
            and not state.get("tool_results")
            and not has_selected_skill
            and state.get("recognition_result") is None
            and not (last_run_context and last_run_context.run_id)
            and not shared_memory_context.get("forced_retention_ids")
        )
        chat_prompt_mode = "deterministic_clarification" if deterministic_clarification else "full_context"
        chat_max_tokens = 0 if deterministic_clarification else 1200
        if concise_lammps_answer is not None:
            chat_prompt_mode = "deterministic_lammps_summary"
            chat_max_tokens = 0
            answer = concise_lammps_answer
        elif deterministic_clarification and contextual_answer is not None:
            answer = contextual_answer
        elif not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="ChatAgent", capability=LLMCapability.CHAT_ANSWER)
            answer = self._build_materials_rag_fallback_answer(
                query=request.message,
                contextual_answer=contextual_answer,
                rag_payload=materials_rag_payload,
            ) or self._build_fallback_answer(state, contextual_answer)
        else:
            if lean_direct_chat:
                chat_prompt_mode = "lean_direct"
                chat_max_tokens = 800
                system_prompt = (
                    "You are the ChatAgent for a materials research system. "
                    "Answer the current user directly, clearly, and concisely in Chinese. "
                    "Do not invent browsing, tool execution, database access, or experimental results that are not present. "
                    "Do not describe yourself as a generic internet-enabled assistant; stay grounded in this materials research agent. "
                    "For scientific uncertainty, state the boundary instead of guessing."
                )
                user_prompt = (
                    f"User message:\n{request.message}\n\n"
                    f"Conversation summary:\n{state.get('current_context_summary', '')}\n\n"
                    f"Relevant memory:\n{json.dumps(state.get('long_term_memory_hits', [])[:3], ensure_ascii=False)}\n\n"
                    f"Recent history:\n{json.dumps([turn.model_dump(mode='json') for turn in state.get('messages', [])[-8:]], ensure_ascii=False)}"
                )
            else:
                chat_prompt_mode = "rag_grounded" if materials_rag_context else "full_context"
                chat_max_tokens = 600 if materials_rag_context else 1200
                system_prompt = (
                    "You are the ChatAgent for a materials research system. "
                    "Answer clearly in Chinese. "
                    "You must use the provided last_run_context, recognition_result, and contextual grounding when relevant. "
                    "Treat shared memory locked facts as explicit user or execution constraints; do not silently override them. "
                    "When materials RAG context is provided, use it as grounded background knowledge and prefer it over unsupported free-form speculation. "
                    "For a RAG-grounded technical answer, synthesize a compact complete checklist with the diagnosis order, 3-5 key checks, source titles, and the next information needed. Keep the complete answer within 500 Chinese characters unless the user explicitly requests a long report; omit long introductions and repeated explanations. "
                    "If the user asks about the previous run, answer from that context instead of pretending nothing happened. "
                    "Do not claim tool execution that did not happen in this turn. "
                    "Do not invent browsing,联网,数据库访问,外部检索,插件调用, or hidden system capabilities unless they are explicitly present in the supplied context for this turn or are part of the configured system capabilities. "
                    "If future web research capability is supported, only describe it as available when it is actually configured or invoked; otherwise answer conservatively. "
                    "Do not describe yourself as a generic internet-enabled assistant by default; stay grounded in this materials research agent project and the currently active tools. "
                    "Do not speculate about model provider, model family, or deployment identity unless the user is explicitly asking and the answer is present in the provided context. "
                    "If capability boundaries are unclear, answer conservatively and focus on what the current run, artifacts, and memory actually show."
                )
                user_prompt = (
                    f"User message:\n{request.message}\n\n"
                    f"Current summary:\n{state.get('current_context_summary', '')}\n\n"
                    f"Retrieved long-term memory:\n{json.dumps(state.get('long_term_memory_hits', []), ensure_ascii=False)}\n\n"
                    f"Shared memory context:\n{self._format_shared_memory_context(state)}\n\n"
                    f"Selected skill guidance:\n{self._format_skill_context(state)}\n\n"
                    f"Tool results from this turn:\n{self._format_tool_results(state)}\n\n"
                    f"Last run context:\n{last_run_context.model_dump_json() if last_run_context else '{}'}\n\n"
                    f"Recognition result:\n{state.get('recognition_result').model_dump_json() if state.get('recognition_result') else '{}'}\n\n"
                    f"Materials RAG context:\n{materials_rag_context or '(none)'}\n\n"
                    f"Contextual grounding draft:\n{contextual_answer or ''}\n\n"
                    f"Conversation history:\n{json.dumps([turn.model_dump(mode='json') for turn in state.get('messages', [])[-8:]], ensure_ascii=False)}"
                )
            try:
                answer = self.llm_client.chat_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=chat_max_tokens,
                    temperature=0.2,
                    capability=LLMCapability.RAG_ANSWER if materials_rag_context else LLMCapability.CHAT_ANSWER,
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
            "artifact_messages": [],
            "html_content": "",
            "html_path": "",
            "response_metadata": {
                "chat_prompt_mode": chat_prompt_mode,
                "chat_max_tokens": chat_max_tokens,
                "materials_rag": {
                    "used": bool(materials_rag_payload.get("hits")),
                    "requested": bool(materials_rag_payload.get("requested")),
                    "gate_reason": materials_rag_payload.get("gate_reason", ""),
                    "hit_count": len(materials_rag_payload.get("hits", [])),
                    "domain": materials_rag_payload.get("domain"),
                    "doc_type": materials_rag_payload.get("doc_type"),
                    "material": materials_rag_payload.get("material"),
                    "titles": [hit.document.title for hit in materials_rag_payload.get("hits", [])[:3]],
                    "duration_ms": materials_rag_payload.get("duration_ms", 0.0),
                    "embedding_backends": list(
                        dict.fromkeys(hit.embedding_backend for hit in materials_rag_payload.get("hits", []) if hit.embedding_backend)
                    ),
                    "reranker_backends": list(
                        dict.fromkeys(hit.reranker_backend for hit in materials_rag_payload.get("hits", []) if hit.reranker_backend)
                    ),
                },
                "shared_memory_context_used": bool(
                    isinstance(state.get("shared_memory_context"), dict)
                    and state.get("shared_memory_context", {}).get("selected_item_ids")
                ),
            },
            "response_summary": {
                "materials_rag": {
                    "used": bool(materials_rag_payload.get("hits")),
                    "requested": bool(materials_rag_payload.get("requested")),
                    "gate_reason": materials_rag_payload.get("gate_reason", ""),
                    "titles": [hit.document.title for hit in materials_rag_payload.get("hits", [])[:3]],
                }
            },
            "rag_evidence": {
                "kind": "materials_rag",
                "query": request.message,
                "domain": materials_rag_payload.get("domain"),
                "doc_type": materials_rag_payload.get("doc_type"),
                "material": materials_rag_payload.get("material"),
                "hits": materials_rag_payload.get("evidence_hits", []),
            },
            "termination_reason": "conversation_answered",
        }
