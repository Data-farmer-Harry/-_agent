import json
import json
import re
from typing import Optional

from app.config import settings
from app.schemas import DiagramRequest
from app.services.llm_client import LLMClient
from app.services.prompt_builder import PromptBuilder


class CodeGenerationService:
    def __init__(self, prompt_builder: PromptBuilder, llm_client: LLMClient | None = None) -> None:
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client or LLMClient()

    def build_prompt(self, request: DiagramRequest) -> str:
        return self.prompt_builder.build_generate_code_prompt(request)

    def build_placeholder_code(self, request: DiagramRequest) -> str:
        return self._build_placeholder_code(request)

    @staticmethod
    def _normalize_system_name(system_name: str) -> str:
        return re.sub(r"[^a-z]", "", system_name.lower())

    @staticmethod
    def _looks_like_python_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith(("#", "import ", "from ", "def ", "class ", "if ", "for ", "while ", "with ", "try:", "@")):
            return True
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*=", stripped):
            return True
        if re.match(r"[A-Za-z_][A-Za-z0-9_\.]*\s*\(", stripped):
            return True
        return stripped in {"pass", "break", "continue", "return"}

    @staticmethod
    def _slice_placeholder_branch(code: str, start_marker: str, end_markers: list[str]) -> Optional[str]:
        start = code.find(start_marker)
        if start == -1:
            return None

        end_candidates = [code.find(marker, start + len(start_marker)) for marker in end_markers]
        end_positions = [position for position in end_candidates if position != -1]
        end = min(end_positions) if end_positions else len(code)
        return code[start:end]

    def _select_quality_source(self, request: DiagramRequest, code: str) -> str:
        normalized_system = self._normalize_system_name(request.system_name)

        if request.diagram_type != "binary":
            return code

        alcu_marker = '    if normalized_system in {"al-cu", "cu-al", "alcu", "cual"}:'
        fecu_marker = '    elif normalized_system in {"fe-cu", "cu-fe", "fecu", "cufe"}:'
        generic_marker = '    else:\n        xaxis_title = "Composition fraction"'

        if alcu_marker not in code or fecu_marker not in code:
            return code

        if normalized_system in {"alcu", "cual"}:
            return self._slice_placeholder_branch(code, alcu_marker, [fecu_marker, generic_marker]) or code
        if normalized_system in {"fecu", "cufe"}:
            return self._slice_placeholder_branch(code, fecu_marker, [generic_marker]) or code

        return self._slice_placeholder_branch(code, generic_marker, ['\n    fig.update_layout(']) or code

    def sanitize_and_validate_code(self, request: DiagramRequest, code: str) -> tuple[str, list[str]]:
        sanitized_code = self._extract_python_code(code)
        return sanitized_code, self._collect_quality_issues(request, sanitized_code)

    def generate_code(self, request: DiagramRequest) -> str:
        generated_code, _ = self.generate_code_with_source(request)
        return generated_code

    def generate_code_with_source(self, request: DiagramRequest) -> tuple[str, str]:
        prompt = self.build_prompt(request)
        if settings.llm_api_base_url and settings.llm_api_key:
            try:
                generated_code = self._generate_code_with_llm(prompt)
            except RuntimeError:
                generated_code = None
            if generated_code:
                generated_code, quality_errors = self.sanitize_and_validate_code(request, generated_code)
                if quality_errors:
                    repaired_code = self.repair_code(request, generated_code, self._build_quality_repair_stderr(quality_errors))
                    if repaired_code:
                        repaired_code, repaired_quality_errors = self.sanitize_and_validate_code(request, repaired_code)
                        if not repaired_quality_errors:
                            return repaired_code, "llm_codegen_repaired"
                else:
                    return generated_code, "llm_codegen"
        return self.build_placeholder_code(request), "placeholder_fallback"

    def repair_code(self, request: DiagramRequest, generated_code: str, stderr: str) -> Optional[str]:
        if not (settings.llm_api_base_url and settings.llm_api_key):
            return None

        prompt = self.prompt_builder.build_repair_code_prompt(request, generated_code, stderr)
        try:
            return self._generate_code_with_llm(prompt)
        except RuntimeError:
            return None

    def _generate_code_with_llm(self, prompt: str) -> Optional[str]:
        content = self.llm_client.chat_text(
            system_prompt="You generate runnable Python only. Return only Python code and use valid Plotly APIs.",
            user_prompt=prompt,
            max_tokens=settings.llm_max_tokens,
            temperature=0.2,
        )
        return self._extract_python_code(content)

    @classmethod
    def _extract_python_code(cls, content: str) -> str:
        stripped = content.strip()
        fenced_blocks = re.findall(r"```(?:python)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fenced_blocks:
            return fenced_blocks[0].strip()

        lines = stripped.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and lines[0].strip().startswith("```"):
            lines.pop(0)
        while lines and not cls._looks_like_python_line(lines[0]):
            lines.pop(0)
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        return "\n".join(lines).strip()

    def _collect_quality_issues(self, request: DiagramRequest, code: str) -> list[str]:
        issues: list[str] = []
        lowered_code = self._select_quality_source(request, code).lower()
        normalized_system = self._normalize_system_name(request.system_name)

        if lowered_code.startswith("```"):
            issues.append("Generated code still contains Markdown code fences; strip them before execution.")

        if request.diagram_type == "binary":
            forbidden_plot_patterns = [
                "go.contour(",
                "go.heatmap(",
                "px.imshow(",
                "autorange='reversed'",
                'autorange="reversed"',
                'title="temperature (°c)"',
                "title='temperature (°c)'",
                'title="composition (at.% cu)"',
                "title='composition (at.% cu)'",
                'title="composition (wt%)"',
                "title='composition (wt%)'",
            ]
            if any(pattern in lowered_code for pattern in forbidden_plot_patterns):
                issues.append(
                    "Binary diagrams must not use contour/heatmap/raster-style rendering or a reversed temperature axis; use smooth boundaries and filled phase regions instead. Temperature must stay on the y-axis and composition on the x-axis."
                )
            if 'mode="markers"' in lowered_code or "mode='markers'" in lowered_code:
                if "phase" in lowered_code or "region" in lowered_code or "mask" in lowered_code:
                    issues.append(
                        "Binary diagrams must not use dense marker clouds or scatter-point rasters to fake phase-region fills; use boundary curves and filled regions instead."
                    )
            if "fig.write_html(" in lowered_code and "full_html=true" in lowered_code:
                issues.append(
                    "Generated result page regressed to a bare Plotly full_html page; keep the structured report-style HTML shell instead."
                )
            if 'temperature (°c)' in lowered_code:
                issues.append("Temperature input is in Kelvin; keep axis labels, hover text, and notes in Kelvin rather than °C.")

        if normalized_system in {"alcu", "cual"}:
            expected_terms = ["eutectic", "theta", "cual2"]
            if not any(term in lowered_code for term in expected_terms):
                issues.append(
                    "Al-Cu binary output should resemble a typical Al-Cu topology, including an eutectic-like feature and a theta/CuAl2-style intermetallic region."
                )
            if "binary binary phase diagram" in lowered_code:
                issues.append("The page title/subtitle is malformed; avoid duplicated words such as 'Binary Binary Phase Diagram'.")
            if any(pattern in lowered_code for pattern in ['xaxis_title="temperature', "xaxis_title='temperature", 'yaxis_title="composition', "yaxis_title='composition"]):
                issues.append("Al-Cu binary output swapped the axes; keep composition on x and temperature on y.")
            if "fill='toself'" in lowered_code or 'fill="toself"' in lowered_code:
                issues.append(
                    "Al-Cu binary output is using ad-hoc self-filled polygons that produce blocky or implausible regions; prefer smooth boundary curves with fills between curves."
                )

        if normalized_system in {"fecu", "cufe"}:
            forbidden_terms = ["a3", "acm", "carbide", "theta", "al2cu", "cual2", "alpha + gamma", "gamma + carbide"]
            if any(term in lowered_code for term in forbidden_terms):
                issues.append(
                    "Fe-Cu binary output is using terminology from steel or Al-Cu systems; use Fe-rich / Cu-rich terminal solids, limited solubility, and two-solid-region wording instead."
                )
            if any(term in lowered_code for term in ["intermediate intermetallic", "θ-like intermetallic", "eutectic-like point", "eutectic-like invariant"]):
                issues.append(
                    "Fe-Cu binary output is using an Al-Cu-style intermetallic/eutectic topology; replace it with Fe-Cu-like terminal solids and a limited-solubility two-solid-region topology."
                )
            if any(term in lowered_code for term in ["bcc start", "fcc start", "l+α", "fcc+bcc"]):
                issues.append(
                    "Fe-Cu binary output is still using a coarse BCC/FCC schematic that does not resemble the intended Fe-Cu style placeholder quality."
                )
            steel_drift_terms = [
                '"fe-c"',
                "carbon content",
                "wt% c",
                "cementite",
                "fe₃c",
                "fe3c",
                "ferrite",
                "austenite",
                "eutectoid",
                "gamma → alpha",
                "γ + fe",
            ]
            if any(term in lowered_code for term in steel_drift_terms):
                issues.append(
                    "Fe-Cu binary output drifted into an Fe-C / steel-style diagram; keep the system locked to Fe-Cu and avoid carbon, ferrite, austenite, Fe3C, or eutectoid terminology."
                )

        return issues

    @staticmethod
    def _build_quality_repair_stderr(issues: list[str]) -> str:
        joined = "\n".join(f"- {issue}" for issue in issues)
        return f"Quality validation failed before execution:\n{joined}"

    def _build_placeholder_code(self, request: DiagramRequest) -> str:
        system_name = json.dumps(request.system_name)
        diagram_type = json.dumps(request.diagram_type)
        notes = json.dumps(request.notes)

        template = '''import html
import numpy as np
import plotly.graph_objects as go

SYSTEM_NAME = __SYSTEM_NAME__
DIAGRAM_TYPE = __DIAGRAM_TYPE__
TEMPERATURE_MIN = __TEMPERATURE_MIN__
TEMPERATURE_MAX = __TEMPERATURE_MAX__
PRESSURE = __PRESSURE__
STEP_SIZE = max(float(__STEP_SIZE__), 1.0)
NOTES = __NOTES__
OUTPUT_FILE = "result.html"
LAYOUT_MARKER = "phase-diagram-agent-layout"
ROOT_ID = "phase-diagram-agent-result"


def format_number(value):
    if isinstance(value, (int, float)):
        return f"{value:.1f}"
    return str(value)


def build_result_page(title, subtitle, summary_cards, chart_html, notes_list, disclaimer):
    cards_html = "".join(
        f'<div class="summary-card"><span class="summary-label">{html.escape(label)}</span><strong class="summary-value">{html.escape(value)}</strong></div>'
        for label, value in summary_cards
    )
    notes_html = "".join(f"<li>{html.escape(item)}</li>" for item in notes_list)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="{LAYOUT_MARKER}" content="v1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --line: #d8e1ec;
      --text: #102033;
      --muted: #5b6b80;
      --accent: #2563eb;
      --warn-bg: #fff7ed;
      --warn-line: #fdba74;
      --warn-text: #9a3412;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .page-shell {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 40px;
    }}
    .hero {{
      background: linear-gradient(135deg, #eff6ff 0%, #ffffff 100%);
      border: 1px solid #bfdbfe;
      border-radius: 20px;
      padding: 24px 28px;
      margin-bottom: 20px;
      box-shadow: 0 16px 48px rgba(15, 23, 42, 0.06);
    }}
    .eyebrow {{
      margin: 0 0 8px;
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{ margin: 0; font-size: 30px; line-height: 1.15; }}
    .subtitle {{ margin: 10px 0 0; color: var(--muted); font-size: 15px; line-height: 1.6; }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .summary-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
      min-height: 88px;
    }}
    .summary-label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 8px;
    }}
    .summary-value {{
      display: block;
      font-size: 16px;
      line-height: 1.45;
      word-break: break-word;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 12px 36px rgba(15, 23, 42, 0.05);
      margin-bottom: 20px;
    }}
    .panel-header {{ margin-bottom: 16px; }}
    .panel-header h2 {{ margin: 0 0 6px; font-size: 20px; }}
    .panel-header p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
    .chart-frame {{
      border: 1px solid #e2e8f0;
      border-radius: 14px;
      overflow: hidden;
      background: #fff;
      padding: 6px;
    }}
    .chart-frame .plotly-graph-div,
    .chart-frame .js-plotly-plot,
    .chart-frame .plot-container {{
      width: 100% !important;
    }}
    .notes-list {{
      margin: 0;
      padding-left: 20px;
      color: var(--text);
      line-height: 1.7;
    }}
    .disclaimer {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      background: var(--warn-bg);
      border: 1px solid var(--warn-line);
      color: var(--warn-text);
    }}
    .disclaimer strong {{ min-width: fit-content; }}
    @media (max-width: 720px) {{
      .page-shell {{ padding: 20px 14px 28px; }}
      .hero {{ padding: 20px; }}
      h1 {{ font-size: 24px; }}
      .panel {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <main id="{ROOT_ID}" class="page-shell">
    <section class="hero">
      <p class="eyebrow">Illustrative phase-diagram result</p>
      <h1>{html.escape(title)}</h1>
      <p class="subtitle">{html.escape(subtitle)}</p>
    </section>

    <section class="summary-grid">{cards_html}</section>

    <section class="panel">
      <div class="panel-header">
        <h2>Interactive diagram</h2>
        <p>Plotly figure kept as the main artifact, with explanatory text moved outside the chart for better readability.</p>
      </div>
      <div class="chart-frame">{chart_html}</div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>Notes</h2>
        <p>Compact reading guidance for quick inspection.</p>
      </div>
      <ul class="notes-list">{notes_html}</ul>
    </section>

    <section class="panel disclaimer">
      <strong>Disclaimer.</strong>
      <span>{html.escape(disclaimer)}</span>
    </section>
  </main>
</body>
</html>"""


def add_region(fig, x_values, y_lower, y_upper, name, fillcolor):
    polygon_x = np.concatenate([x_values, x_values[::-1]])
    polygon_y = np.concatenate([y_upper, y_lower[::-1]])
    fig.add_trace(
        go.Scatter(
            x=polygon_x,
            y=polygon_y,
            fill="toself",
            mode="lines",
            line={"color": fillcolor, "width": 1},
            fillcolor=fillcolor,
            name=name,
            hoverinfo="skip",
            opacity=0.42,
        )
    )


def add_boundary(fig, x_values, y_values, name, color, dash="solid"):
    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=y_values,
            mode="lines",
            name=name,
            line={"color": color, "width": 2.5, "dash": dash},
            hovertemplate="x = %{x:.3f}<br>T = %{y:.1f} K<extra>" + name + "</extra>",
        )
    )


temperature_span = max(float(TEMPERATURE_MAX - TEMPERATURE_MIN), STEP_SIZE, 1.0)
notes_text = NOTES if NOTES else "(none)"
summary_cards = [
    ("System", SYSTEM_NAME),
    ("Diagram type", DIAGRAM_TYPE),
    ("Temperature range", f"{format_number(TEMPERATURE_MIN)} - {format_number(TEMPERATURE_MAX)} K"),
    ("Pressure", f"{PRESSURE / 1000:.1f} kPa"),
    ("Step size", f"{STEP_SIZE:g} K"),
    ("Mode", "illustrative placeholder"),
    ("Notes", notes_text),
]

disclaimer = "This result is an illustrative placeholder for workflow validation and is not derived from a thermodynamic database."

if DIAGRAM_TYPE == "binary":
    x = np.linspace(0.0, 1.0, 401)
    top = np.full_like(x, TEMPERATURE_MAX, dtype=float)
    bottom = np.full_like(x, TEMPERATURE_MIN, dtype=float)
    normalized_system = SYSTEM_NAME.lower().replace(" ", "")

    fig = go.Figure()

    if normalized_system in {"al-cu", "cu-al", "alcu", "cual"}:
        xaxis_title = "Cu composition fraction"
        eutectic_x = 0.33
        eutectic_t = TEMPERATURE_MIN + temperature_span * 0.43
        theta_center = 0.33
        theta_half_width = 0.055
        theta_peak_t = TEMPERATURE_MIN + temperature_span * 0.64
        cu_terminal_x = 0.92

        left_ratio = np.clip(x / max(eutectic_x, 1e-6), 0.0, 1.0)
        right_ratio = np.clip((x - eutectic_x) / max(1.0 - eutectic_x, 1e-6), 0.0, 1.0)

        liquidus_left = eutectic_t + (TEMPERATURE_MAX - eutectic_t) * np.power(1.0 - left_ratio, 0.58)
        liquidus_right = eutectic_t + (TEMPERATURE_MAX - eutectic_t) * np.power(right_ratio, 0.72)
        liquidus = np.where(x <= eutectic_x, liquidus_left, liquidus_right)

        solidus_left = eutectic_t + (liquidus_left - eutectic_t) * 0.40
        solidus_right = eutectic_t + (liquidus_right - eutectic_t) * 0.34
        solidus = np.where(x <= eutectic_x, solidus_left, solidus_right)
        solidus = np.maximum(solidus, eutectic_t + temperature_span * 0.015)

        alpha_terminal_x = 0.08
        theta_shape = np.clip(1.0 - np.abs(x - theta_center) / theta_half_width, 0.0, 1.0)
        theta_mask = theta_shape > 0.0
        theta_cap = eutectic_t + (theta_peak_t - eutectic_t) * np.power(theta_shape, 0.72)
        theta_floor = TEMPERATURE_MIN + temperature_span * (0.05 + 0.04 * np.power(theta_shape, 0.90))
        theta_cap = np.where(theta_mask, theta_cap, TEMPERATURE_MIN)
        theta_floor = np.where(theta_mask, theta_floor, TEMPERATURE_MIN)

        left_mask = x <= eutectic_x
        right_mask = x >= eutectic_x
        alpha_mask = x <= alpha_terminal_x
        low_temp_mask = (x >= alpha_terminal_x) & (x <= cu_terminal_x)
        cu_mask = x >= cu_terminal_x

        add_region(fig, x, liquidus, top, "Liquid", "rgba(239, 68, 68, 0.18)")
        add_region(fig, x[left_mask], solidus[left_mask], liquidus[left_mask], "Liquid + α-Al", "rgba(251, 191, 36, 0.26)")
        add_region(fig, x[right_mask], solidus[right_mask], liquidus[right_mask], "Liquid + θ / Cu-rich solid", "rgba(249, 168, 37, 0.24)")
        add_region(fig, x[alpha_mask], bottom[alpha_mask], solidus[alpha_mask], "α-Al terminal solid", "rgba(96, 165, 250, 0.22)")
        add_region(fig, x[low_temp_mask], bottom[low_temp_mask], np.minimum(solidus[low_temp_mask], eutectic_t), "α-Al + θ eutectic field", "rgba(148, 163, 184, 0.22)")
        add_region(fig, x[theta_mask], theta_floor[theta_mask], theta_cap[theta_mask], "θ (Al₂Cu)-like intermetallic", "rgba(168, 85, 247, 0.22)")
        add_region(fig, x[cu_mask], bottom[cu_mask], solidus[cu_mask], "Cu-rich terminal solid", "rgba(251, 113, 133, 0.18)")

        add_boundary(fig, x, liquidus, "Liquidus", "#dc2626")
        add_boundary(fig, x, solidus, "Solidus", "#f59e0b")
        add_boundary(fig, x[theta_mask], theta_cap[theta_mask], "θ cap", "#7c3aed")
        fig.add_trace(
            go.Scatter(
                x=[alpha_terminal_x, cu_terminal_x],
                y=[eutectic_t, eutectic_t],
                mode="lines",
                name="Eutectic-like invariant",
                line={"color": "#334155", "width": 1.5, "dash": "dash"},
                hovertemplate="T = %{y:.1f} K<extra>Eutectic-like invariant</extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[eutectic_x, theta_center],
                y=[eutectic_t, theta_peak_t],
                mode="markers+text",
                name="Key points",
                text=["Eutectic-like point", "θ-like intermetallic"],
                textposition="top center",
                marker={"size": 10, "color": "#111827", "symbol": "diamond"},
                hovertemplate="x = %{x:.3f}<br>T = %{y:.1f} K<extra>Key point</extra>",
            )
        )

        fig.add_annotation(text="Liquid", x=0.57, y=float(TEMPERATURE_MAX - temperature_span * 0.08), showarrow=False, font={"size": 15, "color": "#991b1b"})
        fig.add_annotation(text="α-Al", x=0.04, y=float((TEMPERATURE_MIN + solidus[int(np.searchsorted(x, 0.04))]) / 2.0), showarrow=False, font={"size": 13, "color": "#1d4ed8"})
        fig.add_annotation(text="α + θ", x=0.18, y=float(TEMPERATURE_MIN + temperature_span * 0.18), showarrow=False, font={"size": 13, "color": "#475569"})
        fig.add_annotation(text="θ (Al₂Cu)", x=theta_center, y=float((theta_floor[int(np.searchsorted(x, theta_center))] + theta_cap[int(np.searchsorted(x, theta_center))]) / 2.0), showarrow=False, font={"size": 13, "color": "#6d28d9"})
        fig.add_annotation(text="Cu-rich solid", x=0.965, y=float((TEMPERATURE_MIN + solidus[int(np.searchsorted(x, 0.965))]) / 2.0), showarrow=False, font={"size": 12, "color": "#be123c"})

        notes_list = [
            "This placeholder follows an Al-Cu-like topology with a eutectic-style invariant and a θ-like intermetallic field.",
            "The left terminal field represents α-Al-rich solid solution, while the center highlights an Al₂Cu-like phase pocket.",
            "Curves are illustrative only and are scaled to the user-requested temperature window rather than a real database.",
            f"User note: {notes_text}",
        ]
        subtitle = "Illustrative placeholder tuned to resemble a familiar Al-Cu binary phase-diagram layout."
    elif normalized_system in {"fe-cu", "cu-fe", "fecu", "cufe"}:
        xaxis_title = "Cu composition fraction"
        liquidus_base = TEMPERATURE_MAX - temperature_span * (0.10 + 0.18 * np.sin(np.pi * x) ** 1.3)
        liquidus = np.clip(liquidus_base, TEMPERATURE_MIN, TEMPERATURE_MAX)

        solidus_gap = temperature_span * (0.12 + 0.05 * np.cos(np.pi * (x - 0.5)) ** 2)
        solidus = np.clip(liquidus - solidus_gap, TEMPERATURE_MIN, TEMPERATURE_MAX)

        solvus_center = 0.50
        solvus_half_width = 0.36
        solvus_shape = np.clip(1.0 - np.abs(x - solvus_center) / solvus_half_width, 0.0, 1.0)
        solvus_cap = TEMPERATURE_MIN + temperature_span * (0.22 + 0.24 * np.power(solvus_shape, 0.85))
        solvus_cap = np.minimum(solvus_cap, solidus - temperature_span * 0.06)
        solvus_cap = np.clip(solvus_cap, TEMPERATURE_MIN + temperature_span * 0.05, TEMPERATURE_MAX)

        fe_terminal_x = 0.12
        cu_terminal_x = 0.88
        fe_mask = x <= fe_terminal_x
        cu_mask = x >= cu_terminal_x
        two_solid_mask = (x >= fe_terminal_x) & (x <= cu_terminal_x)
        liquid_fe_mask = x <= 0.50
        liquid_cu_mask = x >= 0.50

        add_region(fig, x, liquidus, top, "Liquid", "rgba(239, 68, 68, 0.18)")
        add_region(fig, x[liquid_fe_mask], solidus[liquid_fe_mask], liquidus[liquid_fe_mask], "Liquid + Fe-rich solid", "rgba(251, 191, 36, 0.24)")
        add_region(fig, x[liquid_cu_mask], solidus[liquid_cu_mask], liquidus[liquid_cu_mask], "Liquid + Cu-rich solid", "rgba(245, 158, 11, 0.22)")
        add_region(fig, x[fe_mask], bottom[fe_mask], solvus_cap[fe_mask], "Fe-rich terminal solid", "rgba(96, 165, 250, 0.22)")
        add_region(fig, x[cu_mask], bottom[cu_mask], solvus_cap[cu_mask], "Cu-rich terminal solid", "rgba(251, 113, 133, 0.18)")
        add_region(fig, x[two_solid_mask], bottom[two_solid_mask], solvus_cap[two_solid_mask], "Two-solid region", "rgba(148, 163, 184, 0.24)")
        add_region(fig, x, solvus_cap, solidus, "High-temperature single solid solution", "rgba(59, 130, 246, 0.16)")

        add_boundary(fig, x, liquidus, "Liquidus", "#dc2626")
        add_boundary(fig, x, solidus, "Solidus", "#f59e0b")
        add_boundary(fig, x, solvus_cap, "Solvus / miscibility boundary", "#475569", dash="dash")

        fe_index = int(np.searchsorted(x, 0.06))
        cu_index = int(np.searchsorted(x, 0.94))
        two_solid_index = int(np.searchsorted(x, 0.50))
        single_solid_index = int(np.searchsorted(x, 0.32))

        fig.add_annotation(text="Liquid", x=0.58, y=float(TEMPERATURE_MAX - temperature_span * 0.08), showarrow=False, font={"size": 15, "color": "#991b1b"})
        fig.add_annotation(text="Fe-rich solid", x=0.06, y=float((TEMPERATURE_MIN + solvus_cap[fe_index]) / 2.0), showarrow=False, font={"size": 13, "color": "#1d4ed8"})
        fig.add_annotation(text="Cu-rich solid", x=0.94, y=float((TEMPERATURE_MIN + solvus_cap[cu_index]) / 2.0), showarrow=False, font={"size": 13, "color": "#be123c"})
        fig.add_annotation(text="Two-solid region", x=0.50, y=float((TEMPERATURE_MIN + solvus_cap[two_solid_index]) / 2.0), showarrow=False, font={"size": 13, "color": "#334155"})
        fig.add_annotation(text="Single solid solution", x=0.32, y=float((solvus_cap[single_solid_index] + solidus[single_solid_index]) / 2.0), showarrow=False, font={"size": 13, "color": "#2563eb"})

        notes_list = [
            "This placeholder uses a Fe-Cu-like topology with Fe-rich and Cu-rich terminal solids plus a broad two-solid region to reflect limited mutual solubility.",
            "The central low-temperature field is intentionally shown as a two-solid region rather than an intermetallic pocket.",
            "Curves are illustrative only and scaled to the requested temperature window rather than a thermodynamic database.",
            f"User note: {notes_text}",
        ]
        subtitle = "Illustrative placeholder tuned to avoid Al-Cu and steel-style semantics for a Fe-Cu binary diagram."
    else:
        xaxis_title = "Composition fraction"
        eutectic_x = 0.42
        eutectic_t = TEMPERATURE_MIN + temperature_span * 0.58
        intermediate_center = 0.72
        intermediate_half_width = 0.07
        dome_peak_t = TEMPERATURE_MIN + temperature_span * 0.78
        right_terminal_x = 0.92

        left_ratio = np.clip(x / max(eutectic_x, 1e-6), 0.0, 1.0)
        right_ratio = np.clip((x - eutectic_x) / max(1.0 - eutectic_x, 1e-6), 0.0, 1.0)
        liquidus_left = TEMPERATURE_MAX - temperature_span * (0.18 + 0.20 * np.sqrt(left_ratio) + 0.05 * np.sin(np.pi * left_ratio))
        liquidus_right = eutectic_t + (TEMPERATURE_MAX - eutectic_t) * np.power(right_ratio, 0.62)
        liquidus = np.clip(np.where(x <= eutectic_x, liquidus_left, liquidus_right), TEMPERATURE_MIN, TEMPERATURE_MAX)

        solidus_gap_left = temperature_span * (0.08 + 0.05 * np.sin(np.pi * left_ratio / 2.0) ** 2)
        solidus_gap_right = temperature_span * (0.06 + 0.05 * np.power(right_ratio, 0.85))
        solidus = np.clip(np.where(x <= eutectic_x, liquidus - solidus_gap_left, liquidus - solidus_gap_right), TEMPERATURE_MIN, TEMPERATURE_MAX)
        solidus = np.maximum(solidus, eutectic_t + temperature_span * 0.01)

        intermediate_shape = np.clip(1.0 - np.abs(x - intermediate_center) / intermediate_half_width, 0.0, 1.0)
        intermediate_mask = intermediate_shape > 0.0
        intermediate_cap = eutectic_t + (dome_peak_t - eutectic_t) * np.power(intermediate_shape, 0.72)
        intermediate_floor = TEMPERATURE_MIN + temperature_span * (0.06 + 0.04 * np.power(intermediate_shape, 0.90))
        intermediate_cap = np.where(intermediate_mask, intermediate_cap, TEMPERATURE_MIN)
        intermediate_floor = np.where(intermediate_mask, intermediate_floor, TEMPERATURE_MIN)

        left_terminal_x = 0.10
        left_mask = x <= eutectic_x
        right_mask = x >= eutectic_x
        left_terminal_mask = x <= left_terminal_x
        right_terminal_mask = x >= right_terminal_x
        low_temp_mask = (x >= left_terminal_x) & (x <= right_terminal_x)

        add_region(fig, x, liquidus, top, "Liquid", "rgba(239, 68, 68, 0.18)")
        add_region(fig, x, solidus, liquidus, "Liquid + solid", "rgba(251, 191, 36, 0.26)")
        add_region(fig, x[left_terminal_mask], bottom[left_terminal_mask], solidus[left_terminal_mask], "A-rich terminal solid", "rgba(96, 165, 250, 0.22)")
        add_region(fig, x[low_temp_mask], bottom[low_temp_mask], np.minimum(solidus[low_temp_mask], eutectic_t), "Low-temperature two-phase field", "rgba(148, 163, 184, 0.22)")
        add_region(fig, x[intermediate_mask], intermediate_floor[intermediate_mask], intermediate_cap[intermediate_mask], "Intermediate intermetallic-like phase", "rgba(168, 85, 247, 0.22)")
        add_region(fig, x[right_terminal_mask], bottom[right_terminal_mask], solidus[right_terminal_mask], "B-rich terminal solid", "rgba(251, 113, 133, 0.18)")

        add_boundary(fig, x, liquidus, "Liquidus", "#dc2626")
        add_boundary(fig, x, solidus, "Solidus", "#f59e0b")
        add_boundary(fig, x[intermediate_mask], intermediate_cap[intermediate_mask], "Intermediate phase cap", "#7c3aed")
        fig.add_trace(
            go.Scatter(
                x=[left_terminal_x, right_terminal_x],
                y=[eutectic_t, eutectic_t],
                mode="lines",
                name="Invariant line",
                line={"color": "#334155", "width": 1.5, "dash": "dash"},
                hovertemplate="T = %{y:.1f} K<extra>Invariant line</extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[eutectic_x, intermediate_center],
                y=[eutectic_t, dome_peak_t],
                mode="markers+text",
                name="Key points",
                text=["Eutectic-like point", "Intermediate-phase point"],
                textposition="top center",
                marker={"size": 10, "color": "#111827", "symbol": "diamond"},
                hovertemplate="x = %{x:.3f}<br>T = %{y:.1f} K<extra>Key point</extra>",
            )
        )

        fig.add_annotation(text="Liquid", x=0.16, y=float(TEMPERATURE_MAX - temperature_span * 0.06), showarrow=False, font={"size": 15, "color": "#991b1b"})
        fig.add_annotation(text="A-rich solid", x=0.05, y=float((TEMPERATURE_MIN + solidus[int(np.searchsorted(x, 0.05))]) / 2.0), showarrow=False, font={"size": 13, "color": "#1d4ed8"})
        fig.add_annotation(text="Two-phase field", x=0.32, y=float(TEMPERATURE_MIN + temperature_span * 0.18), showarrow=False, font={"size": 13, "color": "#475569"})
        fig.add_annotation(text="Intermediate phase", x=intermediate_center, y=float((intermediate_floor[int(np.searchsorted(x, intermediate_center))] + intermediate_cap[int(np.searchsorted(x, intermediate_center))]) / 2.0), showarrow=False, font={"size": 13, "color": "#6d28d9"})
        fig.add_annotation(text="B-rich solid", x=0.965, y=float((TEMPERATURE_MIN + solidus[int(np.searchsorted(x, 0.965))]) / 2.0), showarrow=False, font={"size": 12, "color": "#be123c"})

        notes_list = [
            "Colored fields are schematic proxies for major binary phase regions rather than database-backed equilibria.",
            "The placeholder uses smooth liquidus/solidus boundaries plus a narrow intermediate-phase pocket to mimic a typical binary diagram.",
            "Most descriptive text is kept outside the chart to reduce overlap in the iframe viewer.",
            f"User note: {notes_text}",
        ]
        subtitle = "Illustrative placeholder output for validating the generation workflow and result viewer."

    fig.update_layout(
        title=None,
        template="plotly_white",
        xaxis_title=xaxis_title,
        yaxis_title="Temperature (K)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0.0},
        margin={"l": 64, "r": 36, "t": 84, "b": 56},
        hovermode="closest",
    )
    fig.update_xaxes(range=[0.0, 1.0], tickformat=".2f")
    fig.update_yaxes(range=[TEMPERATURE_MIN, TEMPERATURE_MAX])

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", config={"responsive": True, "displaylogo": False})
    result_html = build_result_page(
        title=SYSTEM_NAME + " Binary Phase Diagram",
        subtitle=subtitle,
        summary_cards=summary_cards,
        chart_html=chart_html,
        notes_list=notes_list,
        disclaimer=disclaimer,
    )
else:
    a_values = []
    b_values = []
    c_values = []
    stability_values = []
    phase_labels = []

    for a in np.linspace(0.04, 0.92, 34):
        for b in np.linspace(0.04, 0.92, 34):
            c = 1.0 - a - b
            if c <= 0.04:
                continue
            stability = float(0.55 * np.sin(a * np.pi * 3.2) + 0.35 * np.cos(b * np.pi * 4.5) + 0.25 * c)
            if stability > 0.55:
                phase_label = "Field A"
            elif stability > 0.15:
                phase_label = "Field B"
            elif stability > -0.2:
                phase_label = "Field C"
            else:
                phase_label = "Field D"
            a_values.append(float(a))
            b_values.append(float(b))
            c_values.append(float(c))
            stability_values.append(stability)
            phase_labels.append(phase_label)

    fig = go.Figure(
        data=go.Scatterternary(
            a=a_values,
            b=b_values,
            c=c_values,
            mode="markers",
            marker={
                "size": 10,
                "color": stability_values,
                "colorscale": "Plasma",
                "showscale": True,
                "colorbar": {"title": "Stability proxy"},
                "line": {"color": "rgba(15, 23, 42, 0.35)", "width": 0.5},
            },
            text=phase_labels,
            hovertemplate=(
                "A = %{a:.3f}<br>"
                + "B = %{b:.3f}<br>"
                + "C = %{c:.3f}<br>"
                + "Field = %{text}<br>"
                + "Stability = %{marker.color:.3f}<extra></extra>"
            ),
            name="Ternary sampling",
        )
    )
    fig.update_layout(
        title=SYSTEM_NAME + " Ternary Phase Diagram",
        template="plotly_white",
        ternary={
            "sum": 1,
            "aaxis": {"title": {"text": "Component A"}, "min": 0},
            "baxis": {"title": {"text": "Component B"}, "min": 0},
            "caxis": {"title": {"text": "Component C"}, "min": 0},
        },
        margin={"l": 48, "r": 48, "t": 88, "b": 48},
    )
    fig.add_annotation(text="Illustrative ternary stability map", x=0.5, y=1.05, xref="paper", yref="paper", showarrow=False, font={"size": 13, "color": "#7c2d12"})

    notes_list = [
        "Ternary fields are sampled on a regular simplex grid.",
        "Marker colors represent a synthetic stability proxy rather than equilibrium output.",
        "The page shell is shared with binary mode for consistent rendering in the frontend.",
        f"User note: {notes_text}",
    ]
    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", config={"responsive": True, "displaylogo": False})
    result_html = build_result_page(
        title=SYSTEM_NAME + " Ternary Phase Diagram",
        subtitle="Illustrative ternary placeholder output for validating the workflow, page layout, and iframe rendering.",
        summary_cards=summary_cards,
        chart_html=chart_html,
        notes_list=notes_list,
        disclaimer=disclaimer,
    )

with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    file.write(result_html)

print(f"Generated illustrative {SYSTEM_NAME} {DIAGRAM_TYPE} phase diagram.")
print(f"Mode: illustrative placeholder. Output saved to {OUTPUT_FILE}.")
print(f"Temperature range: {format_number(TEMPERATURE_MIN)}-{format_number(TEMPERATURE_MAX)} K; pressure: {PRESSURE / 1000:.1f} kPa.")
'''

        return (
            template.replace("__SYSTEM_NAME__", system_name)
            .replace("__DIAGRAM_TYPE__", diagram_type)
            .replace("__TEMPERATURE_MIN__", str(request.temperature_min))
            .replace("__TEMPERATURE_MAX__", str(request.temperature_max))
            .replace("__PRESSURE__", str(request.pressure))
            .replace("__STEP_SIZE__", str(request.step_size))
            .replace("__NOTES__", notes)
        )
