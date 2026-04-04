from __future__ import annotations

import re
from typing import Any, Dict, List

from src.reasoning.llm_adapter import LLMAdapter
from src.reasoning.request_validator import validate_request
from src.schemas.state import AgentState
from src.utils.attachments import extract_attachment_text
from src.utils.constants import REQUIRED_REQUEST_FIELDS


class ConversationAgent:
    MATERIAL_ALIASES = {
        "al": "Al",
        "aluminum": "Al",
        "aluminium": "Al",
        "铝": "Al",
        "cu": "Cu",
        "copper": "Cu",
        "铜": "Cu",
        "ni": "Ni",
        "nickel": "Ni",
        "镍": "Ni",
    }

    EXECUTION_KEYWORDS = (
        "run",
        "simulate",
        "simulation",
        "calculate",
        "execute",
        "start",
        "launch",
        "运行",
        "模拟",
        "计算",
        "执行",
        "启动",
        "生成脚本",
        "帮我跑",
        "帮我算",
        "做一个",
    )
    HELP_KEYWORDS = (
        "what is",
        "how to",
        "help",
        "explain",
        "introduce",
        "difference",
        "什么是",
        "怎么用",
        "如何",
        "帮助",
        "介绍",
        "解释",
        "区别",
        "教程",
    )
    GREETING_KEYWORDS = ("hello", "hi", "hey", "你好", "您好", "嗨")
    MD_TOPIC_KEYWORDS = (
        "lammps",
        "md",
        "molecular dynamics",
        "dump",
        "trajectory",
        "ovito",
        "thermo",
        "势函数",
        "分子动力学",
        "轨迹",
        "输入脚本",
        "in文件",
        "dump文件",
        "nvt",
        "npt",
        "eam",
        "lj",
        "这个系统",
    )

    def __init__(self, llm: LLMAdapter | None = None) -> None:
        self.llm = llm or LLMAdapter()

    def handle(self, state: AgentState) -> AgentState:
        normalized = dict(state.normalized_request)
        state.attachments = self._prepare_attachments(state.attachments)
        extracted, parse_source = self._extract_request(state.user_query, state.attachments)
        normalized.update({k: v for k, v in extracted.items() if v is not None})

        intent = self._classify_intent(state.user_query, extracted)
        state.intent = intent
        state.normalized_request = normalized

        if intent == "general_help":
            state.missing_fields = []
            state.validation = self._conversation_validation()
            state.parse_source = "conversation"
            state.route = "conversation"
            state.normalized_request = {}  # Clear so frontend won't show param box
            state.messages.append(
                {
                    "role": "assistant",
                    "content": self._append_attachment_notice(
                        self._build_general_reply(state.user_query, state.attachments),
                        state.attachments,
                    ),
                }
            )
            return state

        validation = validate_request(normalized)
        missing_fields = validation["missing_fields"]
        state.missing_fields = missing_fields
        state.validation = validation
        state.parse_source = parse_source
        if missing_fields or not validation["is_reasonable"]:
            state.route = "conversation"
            state.messages.append(
                {
                    "role": "assistant",
                    "content": self._append_attachment_notice(
                        self._clarification_message(missing_fields, normalized, validation),
                        state.attachments,
                    ),
                }
            )
        else:
            state.route = "md_run"
            state.messages.append(
                {
                    "role": "assistant",
                    "content": self._append_attachment_notice(
                        self._ready_message(validation, parse_source),
                        state.attachments,
                    ),
                }
            )
        return state

    def _extract_request(self, text: str, attachments: List[Dict[str, Any]]) -> tuple[Dict[str, Any], str]:
        raw = text.lower()
        heuristic: Dict[str, Any] = {
            "material": self._extract_material(raw),
            "potential_family": self._extract_potential(raw),
            "task_type": self._extract_task(raw),
            "temperature": self._extract_temperature(raw),
            "steps": self._extract_steps(raw),
        }
        prompt = self._compose_prompt(text, attachments, suppress_native_pdf_extract=self._supports_native_pdf_input())
        llm_result = self.llm.generate(
            system_prompt=(
                "Extract molecular dynamics parameters from the user request. "
                "Return JSON only with keys: material, potential_family, task_type, temperature, steps. "
                "Use material symbols like Al/Cu/Ni. Use task_type values equilibration or heating. "
                "Use potential_family values eam or lj."
            ),
            user_prompt=prompt,
            response_schema={"type": "object"},
            attachments=attachments,
        )
        self._apply_llm_attachment_updates(attachments, llm_result.attachment_updates)
        llm_normalized = self._normalize_llm_payload(
            llm_result.content if isinstance(llm_result.content, dict) else {}
        )

        merged = dict(heuristic)
        merged.update({k: v for k, v in llm_normalized.items() if v is not None})
        parse_source = "hybrid" if llm_normalized else "heuristic"
        return merged, parse_source

    def _classify_intent(self, text: str, extracted: Dict[str, Any]) -> str:
        raw = text.lower()
        structured_count = sum(1 for value in extracted.values() if value is not None)
        has_run_parameters = any(
            extracted.get(key) is not None for key in ("potential_family", "task_type", "temperature", "steps")
        )
        if self._contains_any(raw, self.HELP_KEYWORDS) and not self._contains_any(raw, self.EXECUTION_KEYWORDS):
            return "general_help"
        if self._contains_any(raw, self.EXECUTION_KEYWORDS):
            return "simulation_request"
        if structured_count >= 3:
            return "simulation_request"
        if structured_count >= 2 and has_run_parameters:
            return "simulation_request"
        if self._contains_any(raw, self.GREETING_KEYWORDS):
            return "general_help"
        if self._contains_any(raw, self.MD_TOPIC_KEYWORDS):
            return "general_help"
        return "general_help"

    def _extract_material(self, raw: str) -> str | None:
        for key, value in self.MATERIAL_ALIASES.items():
            if key.isascii():
                matched = re.search(rf"\b{re.escape(key)}\b", raw)
            else:
                matched = key in raw
            if matched:
                return value
        return None

    def _extract_potential(self, raw: str) -> str | None:
        if "eam" in raw:
            return "eam"
        if "lj" in raw or "lennard-jones" in raw:
            return "lj"
        return None

    def _extract_task(self, raw: str) -> str | None:
        if "heat" in raw or "heating" in raw or "升温" in raw:
            return "heating"
        if "equilibr" in raw or "平衡" in raw or "nvt" in raw:
            return "equilibration"
        return None

    def _extract_temperature(self, raw: str) -> int | None:
        match = re.search(r"(?:temp(?:erature)?\s*[:=]?\s*|at\s*)(\d{2,5})\s*(k|kelvin)", raw)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d{2,5})\s*(k|kelvin)", raw)
        if match:
            return int(match.group(1))
        return None

    def _extract_steps(self, raw: str) -> int | None:
        match = re.search(r"(\d{3,7})\s*steps?", raw)
        if match:
            return int(match.group(1))
        match = re.search(r"步数\s*(\d{3,7})", raw)
        if match:
            return int(match.group(1))
        return None

    def _clarification_message(
        self,
        missing_fields: List[str],
        normalized: Dict[str, Any],
        validation: Dict[str, Any],
    ) -> str:
        readable = ", ".join(missing_fields) if missing_fields else "无"
        known_bits = ", ".join(f"{k}={v}" for k, v in normalized.items() if v is not None) or "暂无"
        errors = "；".join(validation["errors"]) if validation["errors"] else "无"
        warnings = "；".join(validation["warnings"]) if validation["warnings"] else "无"
        if missing_fields:
            return (
                f"还缺少以下必要参数：{readable}。当前已识别参数：{known_bits}。"
                f"合理性检查：错误={errors}，警告={warnings}。"
            )
        return f"参数已识别，但当前不建议直接运行。已识别参数：{known_bits}。错误：{errors}。警告：{warnings}。"

    def _ready_message(self, validation: Dict[str, Any], parse_source: str) -> str:
        warnings = "；".join(validation["warnings"]) if validation["warnings"] else "无"
        return f"参数已满足运行要求，解析来源={parse_source}，可以开始生成 LAMMPS 任务。警告：{warnings}。"

    def _build_general_reply(self, text: str, attachments: List[Dict[str, Any]]) -> str:
        canned_reply = self._canned_general_reply(text)
        if canned_reply and not attachments:
            return canned_reply

        prompt = self._compose_prompt(text, attachments, suppress_native_pdf_extract=self._supports_native_pdf_input())
        llm_result = self.llm.generate(
            system_prompt=(
                "You are a professional molecular dynamics assistant for an AgentsMD-style platform. "
                "Answer concise Chinese explanations about LAMMPS, molecular dynamics basics, "
                "or how to use the system. If the user is asking to run a simulation, ask them to "
                "provide material, potential_family, task_type, temperature, and steps."
            ),
            user_prompt=prompt,
            attachments=attachments,
        )
        self._apply_llm_attachment_updates(attachments, llm_result.attachment_updates)
        if isinstance(llm_result.content, str):
            cleaned = llm_result.content.strip()
            if cleaned and cleaned != text.strip() and cleaned != prompt.strip():
                return cleaned
        if canned_reply:
            return canned_reply

        return (
            "我可以回答 LAMMPS 和分子动力学基础问题，也可以把你的需求整理成可运行任务。"
            "如果你要直接启动模拟，请尽量给出 material、potential_family、task_type、temperature 和 steps。"
        )

    def _canned_general_reply(self, text: str) -> str:
        raw = text.lower()
        if self._contains_any(raw, self.GREETING_KEYWORDS):
            return (
                "我是一个面向 LAMMPS 的多智能体 MD 助手。"
                "我可以解释分子动力学概念、说明系统用法，也可以帮你整理并执行模拟请求。"
            )
        if "dump" in raw or "dump文件" in raw:
            return (
                "LAMMPS 的 dump 文件是轨迹输出，记录不同时间步的原子坐标等信息。"
                "你当前这个 agent 会把 `dump.atom` 作为后处理输入，再生成结构摘要、热力学图和可选的 OVITO 产物。"
            )
        if "in文件" in raw or "输入脚本" in raw or "input script" in raw or "lammps input" in raw:
            return (
                "LAMMPS 的 in 文件就是输入脚本，负责定义单位制、晶格、势函数、积分参数、dump 输出和运行步数。"
                "当前工程已经改成模板化生成这类脚本，便于后续扩展更多材料和任务。"
            )
        if "势函数" in raw or "eam" in raw or "lj" in raw:
            return (
                "势函数决定原子间相互作用。当前 demo 支持 `eam` 和 `lj` 两类，"
                "其中金属任务通常优先走 EAM；如果是 EAM 运行，还需要正确配置 `POTENTIALS_DIR`。"
            )
        if "nvt" in raw or "npt" in raw:
            return (
                "`NVT` 表示恒粒子数、恒体积、恒温，适合稳定控温；`NPT` 允许体积随压强调整，"
                "更适合先做平衡。当前 agent 默认用保守模板生成脚本，后续可按任务切换系综。"
            )
        if "怎么用" in raw or "help" in raw or "这个系统" in raw or "你能做什么" in raw:
            return (
                "这个系统分两步工作：先在 `/api/chat` 或前端里把自然语言整理成结构化参数，"
                "再在 `/api/run` 触发真实或 mock 的 LAMMPS 流程。当前支持的关键参数是 "
                "`material`、`potential_family`、`task_type`、`temperature`、`steps`。"
            )
        if "lammps" in raw or "分子动力学" in raw or "md" in raw:
            return (
                "LAMMPS 是常用的分子动力学模拟引擎，适合做材料和原子级系统的时间演化计算。"
                "在这个 agent 里，它负责执行输入脚本；上层多智能体逻辑负责参数解析、任务校验和结果整理。"
            )
        return ""

    def _normalize_llm_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload:
            return {}
        material_raw = str(payload.get("material", "")).strip()
        potential_raw = str(payload.get("potential_family", "")).strip().lower()
        task_raw = str(payload.get("task_type", "")).strip().lower()
        material = self.MATERIAL_ALIASES.get(material_raw.lower(), material_raw or None)
        if isinstance(material, str) and material.lower() in ("", "none", "null"):
            material = None
        if potential_raw not in {"eam", "lj"}:
            potential_raw = None
        if task_raw in {"equilibrate", "equilibrated", "equilibrium", "nvt"}:
            task_raw = "equilibration"
        if task_raw not in {"equilibration", "heating"}:
            task_raw = None
        return {
            "material": material,
            "potential_family": potential_raw,
            "task_type": task_raw,
            "temperature": self._coerce_int(payload.get("temperature")),
            "steps": self._coerce_int(payload.get("steps")),
        }

    def _conversation_validation(self) -> Dict[str, Any]:
        return {
            "is_complete": False,
            "is_reasonable": False,
            "missing_fields": [],
            "errors": [],
            "warnings": [],
            "conversation_only": True,
            "required_fields": list(REQUIRED_REQUEST_FIELDS),
        }

    def _coerce_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _contains_any(self, raw: str, candidates: tuple[str, ...]) -> bool:
        for candidate in candidates:
            if candidate.isascii():
                if re.search(rf"\b{re.escape(candidate)}\b", raw):
                    return True
            elif candidate in raw:
                return True
        return False

    def _prepare_attachments(self, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        prepared: List[Dict[str, Any]] = []
        native_pdf_enabled = self._supports_native_pdf_input()
        for attachment in attachments:
            item = dict(attachment)
            item["category"] = str(item.get("category") or item.get("kind") or "other")
            item["kind"] = item["category"]
            item["conversation_mode"] = str(item.get("conversation_mode") or "metadata-only")
            item["conversation_used"] = False
            item["fallback_reason"] = str(item.get("fallback_reason", "") or "")
            item["extracted_chars"] = 0
            if native_pdf_enabled and item["category"] == "pdf":
                extracted = extract_attachment_text(item)
                if extracted["used"] and extracted["text"]:
                    item["_extracted_text"] = extracted["text"]
                    item["extracted_chars"] = len(str(extracted["text"]))
                item["conversation_mode"] = "native-file"
                prepared.append(item)
                continue
            if item["conversation_mode"] == "multimodal":
                prepared.append(item)
                continue
            if item["conversation_mode"] == "extracted":
                extracted = extract_attachment_text(item)
                if extracted["used"] and extracted["text"]:
                    item["_extracted_text"] = extracted["text"]
                    item["conversation_mode"] = "extracted"
                    item["conversation_used"] = True
                    item["extracted_chars"] = len(str(extracted["text"]))
                else:
                    item["conversation_mode"] = "metadata-only"
                    item["conversation_used"] = True
                    item["fallback_reason"] = str(extracted["fallback_reason"] or "")
            else:
                item["conversation_mode"] = "metadata-only"
                item["conversation_used"] = True
            prepared.append(item)
        return prepared

    def _compose_prompt(
        self,
        text: str,
        attachments: List[Dict[str, Any]],
        suppress_native_pdf_extract: bool = False,
    ) -> str:
        attachment_context = self._build_attachment_context(
            attachments,
            suppress_native_pdf_extract=suppress_native_pdf_extract,
        )
        if not attachment_context:
            return text
        return f"用户消息：\n{text}\n\n附件上下文：\n{attachment_context}"

    def _build_attachment_context(
        self,
        attachments: List[Dict[str, Any]],
        suppress_native_pdf_extract: bool = False,
    ) -> str:
        if not attachments:
            return ""
        summary_lines: List[str] = []
        extracted_blocks: List[str] = []
        for attachment in attachments:
            parsed_state = "no"
            if attachment.get("conversation_mode") == "multimodal":
                parsed_state = "vision"
            elif attachment.get("conversation_mode") == "native-file":
                parsed_state = "native"
            elif str(attachment.get("_extracted_text", "") or "").strip():
                parsed_state = "yes"
            summary_lines.append(
                "- "
                + f"name={attachment.get('original_name') or attachment.get('stored_name')}, "
                + f"mime_type={attachment.get('mime_type') or 'unknown'}, "
                + f"category={attachment.get('category')}, "
                + f"size_bytes={attachment.get('size_bytes')}, "
                + f"parsed={parsed_state}, "
                + f"conversation_mode={attachment.get('conversation_mode')}, "
                + f"usage={attachment.get('usage') or 'none'}"
            )
            extracted_text = str(attachment.get("_extracted_text", "") or "").strip()
            if (
                suppress_native_pdf_extract
                and attachment.get("category") == "pdf"
                and attachment.get("conversation_mode") == "native-file"
            ):
                extracted_text = ""
            if extracted_text:
                extracted_blocks.append(
                    f"[{attachment.get('original_name') or attachment.get('stored_name')}]\n{extracted_text}"
                )
        parts = ["附件摘要：", "\n".join(summary_lines)]
        if extracted_blocks:
            parts.extend(["", "附件提取文本：", "\n\n".join(extracted_blocks)])
        return "\n".join(parts).strip()

    def _supports_native_pdf_input(self) -> bool:
        capability = getattr(self.llm, "supports_native_pdf_input", None)
        if not callable(capability):
            return False
        try:
            return bool(capability())
        except Exception:
            return False

    def _apply_llm_attachment_updates(
        self,
        attachments: List[Dict[str, Any]],
        updates: Dict[str, Dict[str, Any]],
    ) -> None:
        if not updates:
            return
        for attachment in attachments:
            key = str(
                attachment.get("upload_id")
                or attachment.get("stored_name")
                or attachment.get("original_name")
                or ""
            )
            if key in updates:
                attachment.update(updates[key])

    def _append_attachment_notice(self, content: str, attachments: List[Dict[str, Any]]) -> str:
        notices: List[str] = []
        for attachment in attachments:
            reason = str(attachment.get("fallback_reason", "") or "").strip()
            if not reason:
                continue
            notices.append(
                f"附件 `{attachment.get('original_name') or attachment.get('stored_name')}`：{reason}"
            )
        if not notices:
            return content
        return content + "\n\n" + "\n".join(notices)
