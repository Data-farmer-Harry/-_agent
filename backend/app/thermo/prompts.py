from __future__ import annotations

from app.state import DiagramRequest
from app.thermo.registry import (
    build_calculated_prompt_hint,
    list_calculated_binary_systems,
)


class PromptBuilder:
    @staticmethod
    def _build_calculated_system_block() -> str:
        return ", ".join(list_calculated_binary_systems())

    def build_generate_code_prompt(self, request: DiagramRequest) -> str:
        calculated_hint = build_calculated_prompt_hint(request.system_name)
        calculated_systems = self._build_calculated_system_block()
        return f"""You are the code-writing agent inside a materials phase-diagram project.

Your job is to return only runnable Python code.

Project architecture rules:
1. You MUST call the local helper `build_calculated_phase_diagram_report` from `app.thermo.engine`.
2. Do NOT call any legacy fake-RAG helper.
3. Do NOT manually draw Plotly or Matplotlib traces in generated code.
4. Do NOT read pre-saved phase diagrams, screenshots, or HTML templates.
5. Do NOT use subprocess, network requests, os.system, pip install, or shell commands.
6. The code must write `result.html` in the current working directory.
7. Keep stdout short: print 4-5 lines summarizing system, family, method, database, and output path.
8. Use the request values exactly as given for system name, temperature range, pressure, step size, and notes.
9. Do NOT pass unsupported keyword arguments such as `diagram_type` into `build_calculated_phase_diagram_report`.

Use this exact helper pattern:
```python
from app.thermo.engine import build_calculated_phase_diagram_report

report = build_calculated_phase_diagram_report(
    system_name="...",
    temperature_min=...,
    temperature_max=...,
    pressure=...,
    step_size=...,
    notes="...",
    output_path="result.html",
)

print(f"system={{report['system_name']}}")
print(f"family={{report['family']}}")
print(f"method={{report['method']}}")
print(f"database={{report['database_name']}}")
print(f"output={{report['output_path']}}")
```

Registered thermodynamic systems:
{calculated_systems}

Calculated system context:
{calculated_hint or "This request is only valid if a registry-backed TDB file has already been matched for the requested system."}

Current request:
- system_name: {request.system_name}
- diagram_type: {request.diagram_type}
- temperature_min: {request.temperature_min}
- temperature_max: {request.temperature_max}
- pressure: {request.pressure}
- step_size: {request.step_size}
- notes: {request.notes or "(none)"}
"""

    def build_repair_code_prompt(self, request: DiagramRequest, generated_code: str, stderr: str) -> str:
        calculated_hint = build_calculated_prompt_hint(request.system_name)
        return f"""Repair the Python code below.

Hard rules:
1. Return only full runnable Python code.
2. Keep using `build_calculated_phase_diagram_report` from `app.thermo.engine`.
3. Do not switch to any legacy fake-RAG helper.
4. Do not manually draw Matplotlib or Plotly traces.
5. Keep the output file as `result.html`.
6. Keep stdout short and deterministic.
7. Do NOT pass unsupported keyword arguments such as `diagram_type` into `build_calculated_phase_diagram_report`.

Calculated system context:
{calculated_hint or "Use the registry-backed TDB card that was already matched for this run."}

Request:
- system_name: {request.system_name}
- diagram_type: {request.diagram_type}
- temperature_min: {request.temperature_min}
- temperature_max: {request.temperature_max}
- pressure: {request.pressure}
- step_size: {request.step_size}
- notes: {request.notes or "(none)"}

stderr / review context:
{stderr}

Original code:
{generated_code}
"""
