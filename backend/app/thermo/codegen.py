from __future__ import annotations

import re
from typing import Optional

from app.config import settings
from app.core.llm import LLMClient, LLMRequiredError
from app.thermo.prompts import PromptBuilder
from app.thermo.registry import get_calculated_binary_card
from app.state import DiagramRequest


class CodeGenerationService:
    def __init__(self, prompt_builder: PromptBuilder, llm_client: LLMClient | None = None) -> None:
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client or LLMClient()

    def build_prompt(self, request: DiagramRequest) -> str:
        return self.prompt_builder.build_generate_code_prompt(request)

    @staticmethod
    def _looks_like_python_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        starters = ("import ", "from ", "def ", "class ", "if ", "for ", "while ", "with ", "try:", "@")
        if stripped.startswith(starters):
            return True
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*=", stripped):
            return True
        if re.match(r"[A-Za-z_][A-Za-z0-9_\.]*\s*\(", stripped):
            return True
        return stripped in {"pass", "break", "continue", "return"}

    @classmethod
    def _extract_python_code(cls, content: str) -> str:
        stripped = content.strip()
        fenced_blocks = re.findall(r"```(?:python)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fenced_blocks:
            return fenced_blocks[0].strip()

        lines = stripped.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not cls._looks_like_python_line(lines[0]):
            lines.pop(0)
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_system_name(system_name: str) -> str:
        return re.sub(r"[^a-z]", "", system_name.lower())

    def sanitize_and_validate_code(self, request: DiagramRequest, code: str) -> tuple[str, list[str]]:
        sanitized_code = self._extract_python_code(code)
        return sanitized_code, self._collect_quality_issues(request, sanitized_code)

    def _collect_quality_issues(self, request: DiagramRequest, code: str) -> list[str]:
        issues: list[str] = []
        lowered = code.lower()
        normalized_request_system = self._normalize_system_name(request.system_name)
        calculated_card = get_calculated_binary_card(request.system_name)

        if lowered.startswith("```"):
            issues.append("Generated code still contains Markdown code fences; return only Python.")
        if request.diagram_type != "binary":
            issues.append("This simplified project currently supports binary phase diagrams only.")
        if "result.html" not in lowered:
            issues.append("Generated code must write result.html in the current working directory.")
        if "diagram_type=" in lowered:
            issues.append("Generated code must not pass diagram_type to the local phase-diagram helper because the helper does not accept that argument.")

        if normalized_request_system and normalized_request_system not in self._normalize_system_name(code):
            issues.append("Generated code must embed the requested material system name instead of drifting to another system.")

        forbidden_tokens = (
            "subprocess",
            "os.system",
            "pip install",
            "requests.",
            "urllib.request",
            "http://",
            "https://",
            "np.random",
            "random.",
        )
        if any(token in lowered for token in forbidden_tokens):
            issues.append("Generated code must stay local, deterministic, and offline; do not use shell commands, network calls, or randomness.")

        if calculated_card is None:
            issues.append("The requested system is not present in the thermodynamic database registry, so a true TDB calculation wrapper cannot be generated.")
            return issues

        if "build_calculated_phase_diagram_report" not in code:
            issues.append("Database-backed systems must call build_calculated_phase_diagram_report from the local pycalphad engine.")
        if "from app.thermo.engine import build_calculated_phase_diagram_report" not in code:
            issues.append("Database-backed systems must import build_calculated_phase_diagram_report from app.thermo.engine.")
        if "build_phase_diagram_report" in code:
            issues.append("Database-backed systems must not fall back to the legacy fake-RAG helper when a true pycalphad/TDB path is available.")

        manual_plot_tokens = (
            "go.figure(",
            "plotly.graph_objects",
            "plotly.express",
            "add_trace(",
            "fig.to_json()",
            "plotly.newplot",
            "plt.plot(",
            "ax.plot(",
        )
        if any(token in lowered for token in manual_plot_tokens):
            issues.append("Database-backed systems must use the local pycalphad helper instead of manually drawing Plotly or Matplotlib traces.")

        return issues

    @staticmethod
    def _build_quality_repair_stderr(issues: list[str]) -> str:
        if not issues:
            return "No quality issues were recorded."
        return "Pre-execution validation failed:\n- " + "\n- ".join(issues)

    @staticmethod
    def _blocking_quality_issues(issues: list[str]) -> list[str]:
        return list(issues)

    def _generate_code_with_llm(self, prompt: str) -> Optional[str]:
        content = self.llm_client.chat_text(
            system_prompt="Return runnable Python only. Use the project-provided local phase-diagram helper exactly when supported.",
            user_prompt=prompt,
            max_tokens=1800,
            temperature=0.1,
        )
        return self._extract_python_code(content)

    @staticmethod
    def _build_deterministic_wrapper(request: DiagramRequest) -> str:
        return f"""from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name={request.system_name!r},
    temperature_min={float(request.temperature_min)!r},
    temperature_max={float(request.temperature_max)!r},
    pressure={float(request.pressure)!r},
    step_size={float(request.step_size)!r},
    notes={request.notes!r},
    output_path="result.html",
)

print(f"system={{report['system_name']}}")
print(f"family={{report['family']}}")
print(f"method={{report['method']}}")
print(f"database={{report['database_name']}}")
print(f"output={{report['output_path']}}")
"""

    def generate_code(self, request: DiagramRequest) -> str:
        code, _ = self.generate_code_with_source(request)
        return code

    def generate_code_with_source(self, request: DiagramRequest) -> tuple[str, str]:
        if get_calculated_binary_card(request.system_name) is None:
            raise RuntimeError(f"No thermodynamic database is registered for {request.system_name}.")

        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="PhaseDiagramAgent", capability="Python wrapper 代码生成")
            deterministic_code = self._build_deterministic_wrapper(request)
            deterministic_code, issues = self.sanitize_and_validate_code(request, deterministic_code)
            if issues:
                raise RuntimeError(f"Deterministic wrapper fallback failed validation: {'; '.join(issues[:5])}")
            return deterministic_code, "deterministic_codegen_fallback"

        prompt = self.build_prompt(request)
        try:
            generated_code = self._generate_code_with_llm(prompt)
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"PhaseDiagramAgent 在代码生成阶段调用 LLM 失败：{exc}") from exc
            generated_code = None
        if not generated_code:
            if settings.require_llm_for_agents:
                raise LLMRequiredError("PhaseDiagramAgent 需要 LLM 生成 wrapper 代码，但本次没有返回有效 Python。")
            deterministic_code = self._build_deterministic_wrapper(request)
            deterministic_code, issues = self.sanitize_and_validate_code(request, deterministic_code)
            if issues:
                raise RuntimeError(f"Deterministic wrapper fallback failed validation: {'; '.join(issues[:5])}")
            return deterministic_code, "deterministic_codegen_fallback"

        source_label = "llm_codegen_calculated_wrapper"
        generated_code, issues = self.sanitize_and_validate_code(request, generated_code)
        if generated_code and not self._blocking_quality_issues(issues):
            return generated_code, source_label

        repaired_code = self.repair_code(request, generated_code, self._build_quality_repair_stderr(issues))
        if repaired_code:
            repaired_code, repaired_issues = self.sanitize_and_validate_code(request, repaired_code)
            if repaired_code and not self._blocking_quality_issues(repaired_issues):
                return repaired_code, f"{source_label}_repaired"

        if settings.require_llm_for_agents:
            joined = "; ".join(issues[:5]) if issues else "unknown validation failure"
            raise LLMRequiredError(f"PhaseDiagramAgent 生成的 LLM wrapper 未通过校验且修复失败：{joined}")

        deterministic_code = self._build_deterministic_wrapper(request)
        deterministic_code, deterministic_issues = self.sanitize_and_validate_code(request, deterministic_code)
        if deterministic_code and not self._blocking_quality_issues(deterministic_issues):
            return deterministic_code, "deterministic_codegen_fallback"

        joined = "; ".join(issues[:5]) if issues else "unknown validation failure"
        raise RuntimeError(f"LLM generated code failed pre-execution validation: {joined}")

    def repair_code(self, request: DiagramRequest, generated_code: str, stderr: str) -> Optional[str]:
        if not self.llm_client.is_configured():
            if settings.require_llm_for_agents:
                self.llm_client.require_configured(agent_name="PhaseDiagramAgent", capability="代码修复")
            return None
        prompt = self.prompt_builder.build_repair_code_prompt(request, generated_code, stderr)
        try:
            repaired = self._generate_code_with_llm(prompt)
        except RuntimeError as exc:
            if settings.require_llm_for_agents:
                raise LLMRequiredError(f"PhaseDiagramAgent 在代码修复阶段调用 LLM 失败：{exc}") from exc
            return None
        return repaired or None
