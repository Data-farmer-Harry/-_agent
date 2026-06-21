from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.state import AgentChatRequest, LastRunContext


MINI_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(
    bytes.fromhex(
        "89504E470D0A1A0A"
        "0000000D49484452000000010000000108060000001F15C489"
        "0000000D49444154789C6360606060000000040001F6173855"
        "0000000049454E44AE426082"
    )
).decode("ascii")


def build_request(
    message: str,
    *,
    conversation_id: str = "test-conversation",
    system_name: str = "",
    temperature_min: float = 300.0,
    temperature_max: float = 1800.0,
) -> AgentChatRequest:
    return AgentChatRequest(
        conversation_id=conversation_id,
        message=message,
        system_name=system_name,
        diagram_type="binary",
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure=101325.0,
        step_size=50.0,
        notes="unit test",
        uploaded_assets=[],
        conversation_history=[],
        last_run_context=LastRunContext(),
    )


class ScriptedLLMClient:
    SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")
    RANGE_PATTERN = re.compile(
        r"(?P<low>\d+(?:\.\d+)?)\s*(?:-|~|到|至|to)\s*(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>k|K|℃|°C|°c|c|C)?"
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def is_configured() -> bool:
        return True

    def require_configured(self, *, agent_name: str, capability: str) -> None:
        _ = agent_name, capability

    @classmethod
    def _extract_user_message(cls, prompt: str) -> str:
        match = re.search(r"User message:\n(.*?)(?:\n\n|$)", prompt, flags=re.DOTALL)
        return match.group(1).strip() if match else prompt.strip()

    @classmethod
    def _extract_system_name(cls, text: str) -> str:
        match = cls.SYSTEM_PATTERN.search(text)
        return match.group(1).replace("/", "-").replace(" ", "") if match else "Al-Zn"

    @classmethod
    def _extract_temperature_range(cls, text: str) -> tuple[float, float]:
        match = cls.RANGE_PATTERN.search(text)
        if not match:
            return 300.0, 1800.0
        low = float(match.group("low"))
        high = float(match.group("high"))
        if (match.group("unit") or "").lower() in {"℃", "°c", "c"}:
            low += 273.15
            high += 273.15
        return low, high

    def chat_text(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1) -> str:
        self.calls.append(
            {
                "method": "chat_text",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        user_message = self._extract_user_message(user_prompt)
        grounding_match = re.search(r"Contextual grounding draft:\n(.*?)(?:\n\nConversation history:|$)", user_prompt, flags=re.DOTALL)
        grounding = grounding_match.group(1).strip() if grounding_match else ""
        last_system_match = re.search(r'"system_name":"([^"]+)"', user_prompt)
        last_system = last_system_match.group(1) if last_system_match else ""
        if "Return runnable Python only." in system_prompt:
            system_name = self._extract_system_name(user_prompt)
            low, high = self._extract_temperature_range(user_prompt)
            return (
                "from app.thermo.engine import build_calculated_phase_diagram_report\n\n"
                "report = build_calculated_phase_diagram_report(\n"
                f"    system_name={system_name!r},\n"
                f"    temperature_min={low!r},\n"
                f"    temperature_max={high!r},\n"
                "    pressure=101325.0,\n"
                "    step_size=50.0,\n"
                "    notes='stub llm codegen',\n"
                "    output_path='result.html',\n"
                ")\n\n"
                "print(f\"system={report['system_name']}\")\n"
                "print(f\"database={report['database_name']}\")\n"
                "print(f\"output={report['output_path']}\")\n"
            )
        if "You suggest the next user prompt for a materials research agent." in system_prompt:
            if "lammps.generate" in user_prompt:
                return "请解释刚刚这轮 LAMMPS 结果里温度、能量和轨迹变化最值得关注的地方。"
            if "phase_diagram.generate" in user_prompt or "build_calculated_phase_diagram_report" in user_prompt:
                return "请解释刚刚这张相图里最重要的相区变化，并指出结果的可信边界。"
            if "RecognitionAgent" in user_prompt or "识别" in user_prompt:
                return "请基于识别结果，说明这张图最可能的体系、坐标轴和关键相区。"
            return "请基于当前会话上下文，给我一个最值得继续追问的科研问题。"
        if grounding:
            if "你刚刚生成了什么代码" in user_message or "代码" in user_message:
                system_text = f"{last_system} " if last_system else ""
                return f"上一轮 {system_text}生成的是一段薄 wrapper，核心调用是 build_calculated_phase_diagram_report，并通过 pycalphad + TDB 在本地执行。"
            if "准确" in user_message or "对不对" in user_message:
                return "这轮结果不是手画示意图，而是走了真实 TDB 计算链路，并保留了 review 摘要与 accuracy gate 结果。"
            return grounding
        if "共析" in user_message:
            return "共析点是某一固定成分下，一个固相在固定温度分解成两个固相的点。"
        if "包晶" in user_message:
            return "包晶反应指液相与一种固相在固定温度反应生成另一种固相。"
        return "当前处于对话模式。"

    def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1) -> dict[str, Any] | None:
        self.calls.append(
            {
                "method": "chat_json",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        user_message = self._extract_user_message(user_prompt)
        lowered = user_message.lower()
        if "Choose exactly one route_name" in system_prompt:
            has_image = "Uploaded assets" in user_prompt and "\"image/" in user_prompt
            last_run_present = '"run_id":"' in user_prompt and '"run_id":""' not in user_prompt
            recognition_present = "Recognition result" in user_prompt and any(
                token in user_prompt for token in ('"system":"', '"raw_summary":"', '"labels":[')
            )
            follow_up_ref = any(token in user_message for token in ("刚刚", "刚才", "上一轮", "上一个结果", "这张图", "那张图"))
            follow_up_hint = any(token in user_message for token in ("代码", "准确", "对不对", "讲解", "解释", "流程", "为什么"))
            html_follow_up = last_run_present and any(
                token in lowered or token in user_message
                for token in ("交互式html", "交互式 html", "交互式页面", "interactive html", "result.html", "html文件")
            )
            image_html_request = has_image and any(
                token in lowered or token in user_message
                for token in ("交互式html", "交互式 html", "交互式页面", "interactive html", "result.html", "html文件")
            )
            wants_generate = any(token in user_message for token in ("生成", "绘制", "画", "重画", "重新生成")) or "generate" in lowered
            wants_recognition = any(token in user_message for token in ("识别", "截图", "解析")) or "recognize" in lowered
            wants_image_analysis = any(token in user_message for token in ("相区", "关键点", "坐标轴", "共晶", "phase field", "axis", "label"))
            wants_generate_from_recognition = recognition_present and wants_generate and any(
                token in user_message for token in ("识别结果", "对应体系", "这张图", "刚才", "上一轮")
            )
            follow_up = not has_image and follow_up_hint and (follow_up_ref or last_run_present or recognition_present) and not wants_generate_from_recognition
            if has_image and wants_image_analysis and not wants_generate:
                wants_recognition = True
            wants_lammps = any(token in lowered for token in ("lammps", "md", "molecular dynamics", "模拟", "分子动力学", "nvt", "npt", "eam", "lj", "升温"))
            lammps_run_request = any(token in user_message for token in ("运行", "执行", "跑", "做一个", "做一轮", "返回", "给我", "模拟一下", "再跑"))
            lammps_explain_request = wants_lammps and any(
                token in lowered or token in user_message
                for token in ("怎么用", "是什么", "区别", "解释", "说明", "报错", "怎么办", "适合", "选择", "推荐", "error", "explain")
            )
            if image_html_request:
                return {
                    "route_name": "recognition.analyze",
                    "next_step": "recognition",
                    "compute_domain": "none",
                    "intent": "recognize_image_to_interactive_simulator",
                    "reason": "image to interactive simulator request",
                    "confidence": 0.93,
                }
            if html_follow_up:
                return {
                    "route_name": "conversation.answer",
                    "next_step": "chat",
                    "compute_domain": "none",
                    "intent": "rehydrate_previous_phase_html",
                    "reason": "reopen previous phase html",
                    "confidence": 0.95,
                }
            if follow_up:
                return {
                    "route_name": "conversation.answer",
                    "next_step": "chat",
                    "compute_domain": "none",
                    "intent": "follow_up_about_previous_run",
                    "reason": "follow-up request",
                    "confidence": 0.94,
                }
            if has_image and wants_generate and wants_recognition:
                return {
                    "route_name": "mixed.request",
                    "next_step": "recognition",
                    "compute_domain": "phase_diagram",
                    "intent": "recognize_then_generate",
                    "reason": "image plus generation request",
                    "confidence": 0.92,
                }
            if wants_generate_from_recognition:
                return {
                    "route_name": "phase_diagram.generate",
                    "next_step": "compute",
                    "compute_domain": "phase_diagram",
                    "intent": "generate_phase_diagram",
                    "reason": "generate from previous recognition result",
                    "confidence": 0.91,
                }
            if wants_recognition:
                return {
                    "route_name": "recognition.analyze",
                    "next_step": "recognition",
                    "compute_domain": "none",
                    "intent": "recognize_phase_diagram",
                    "reason": "recognition request",
                    "confidence": 0.91,
                }
            if lammps_explain_request and not lammps_run_request:
                return {
                    "route_name": "conversation.answer",
                    "next_step": "chat",
                    "compute_domain": "none",
                    "intent": "explain_lammps_or_materials_concept",
                    "reason": "lammps explanation request",
                    "confidence": 0.92,
                }
            if wants_lammps:
                return {
                    "route_name": "lammps.generate",
                    "next_step": "compute",
                    "compute_domain": "lammps",
                    "intent": "run_lammps_simulation",
                    "reason": "lammps request",
                    "confidence": 0.92,
                }
            if wants_generate:
                return {
                    "route_name": "phase_diagram.generate",
                    "next_step": "compute",
                    "compute_domain": "phase_diagram",
                    "intent": "generate_phase_diagram",
                    "reason": "generate request",
                    "confidence": 0.93,
                }
            return {
                "route_name": "conversation.answer",
                "next_step": "chat",
                "compute_domain": "none",
                "intent": "answer_question",
                "reason": "plain chat",
                "confidence": 0.9,
            }

        if "Return conservative JSON for a binary phase diagram request" in system_prompt:
            system_name = self._extract_system_name(user_message)
            low, high = self._extract_temperature_range(user_message)
            return {
                "system_name": system_name,
                "diagram_type": "binary",
                "temperature_min": low,
                "temperature_max": high,
                "pressure": 101325.0,
                "step_size": 50.0,
                "notes": user_message,
                "confidence": 0.9,
            }

        if "LammpsRuntime request interpreter" in system_prompt:
            if any(token in lowered for token in ("cu", "copper", "铜")):
                material = "Cu"
            elif any(token in lowered for token in ("ni", "nickel", "镍")):
                material = "Ni"
            else:
                material = "Al"
            task_type = "heating" if any(token in lowered for token in ("heat", "heating", "升温")) else "equilibration"
            potential_family = "lj" if "lj" in lowered else "eam"
            steps_match = re.search(r"(\d{3,7})\s*steps?", lowered)
            temperature_match = re.search(r"(\d{2,5})\s*(k|kelvin)", lowered)
            return {
                "material": material,
                "potential_family": potential_family,
                "task_type": task_type,
                "temperature": int(temperature_match.group(1)) if temperature_match else 900,
                "steps": int(steps_match.group(1)) if steps_match else 5000,
                "ensemble": "NVT",
                "box_size": 4,
                "initial_temp": 300,
                "time_step": 0.001,
                "dump_file": "dump.atom",
                "notes": user_message,
                "confidence": 0.88,
            }

        if "repair a structured LAMMPS request" in system_prompt:
            return {
                "material": "Cu",
                "potential_family": "lj",
                "task_type": "equilibration",
                "temperature": 700,
                "steps": 3000,
                "ensemble": "NVT",
                "box_size": 4,
                "initial_temp": 300,
                "time_step": 0.001,
                "dump_file": "dump.atom",
                "notes": "repaired request",
            }

        if "reviewing an agent run" in system_prompt:
            return {
                "summary": "LLM reviewer confirmed that the wrapper uses the local pycalphad + TDB helper and the artifact contract is valid.",
                "confidence": 0.88,
                "passed": True,
                "blocking_issues": [],
                "advisory_issues": [],
            }

        if "reviewing a LAMMPS runtime agent run" in system_prompt:
            return {
                "summary": "LLM reviewer confirmed that the LAMMPS request, artifact bundle, and result summary are internally consistent.",
                "confidence": 0.84,
                "passed": True,
                "blocking_issues": [],
                "advisory_issues": [],
            }

        content = self.chat_text(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens, temperature=temperature)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def chat_multimodal_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> dict[str, Any] | None:
        _ = system_prompt, image_data_url, max_tokens, temperature
        self.calls.append({"method": "chat_multimodal_json", "user_prompt": user_prompt})
        user_message = self._extract_user_message(user_prompt)
        system_name = self._extract_system_name(user_message)
        return {
            "system": system_name,
            "diagram_type": "binary",
            "x_axis": {"label": "composition", "minimum": 0, "maximum": 100, "unit": "at.%"},
            "y_axis": {"label": "temperature", "minimum": 300, "maximum": 1000, "unit": "K"},
            "plot_region": {"left": 0.14, "top": 0.10, "right": 0.88, "bottom": 0.84, "confidence": 0.86},
            "phases": ["Liquid", "FCC_A1", "HCP_A3"],
            "critical_points": [
                {
                    "label": "point-1",
                    "composition": 50,
                    "temperature": 700,
                    "x_norm": 0.51,
                    "y_norm": 0.42,
                    "confidence": 0.82,
                    "notes": "stub",
                }
            ],
            "labels": ["phase-diagram.png"],
            "confidence": 0.84,
            "raw_summary": f"识别到 {system_name} 二元相图截图。",
        }
