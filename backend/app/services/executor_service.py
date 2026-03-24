from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.artifact_service import ArtifactService
from app.utils.file_utils import ensure_directory, read_text_file_if_exists, remove_file_if_exists, write_text_file

LAYOUT_MARKER = "phase-diagram-agent-layout"
ROOT_ID = "phase-diagram-agent-result"


@dataclass
class ExecutorResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    html_content: str | None = None
    html_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PythonExecutor(ABC):
    @abstractmethod
    def execute(self, run_id: str, code: str) -> ExecutorResult:
        raise NotImplementedError


class LocalPythonExecutor(PythonExecutor):
    def __init__(self, artifact_service: ArtifactService, python_executable: str) -> None:
        self.artifact_service = artifact_service
        self.python_executable = python_executable

    def _normalize_result_html(self, html_content: str) -> str:
        if LAYOUT_MARKER in html_content or ROOT_ID in html_content:
            return html_content

        injected_head = f"""
<meta name=\"{LAYOUT_MARKER}\" content=\"normalized-v1\">
<style>
  :root {{
    color-scheme: light;
    --page-bg: #f4f7fb;
    --panel-bg: #ffffff;
    --line: #d8e1ec;
    --text: #102033;
    --muted: #5b6b80;
    --accent: #2563eb;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: var(--text);
    background: linear-gradient(180deg, #f8fbff 0%, var(--page-bg) 100%);
  }}
  .normalized-page-shell {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 28px 20px 40px;
  }}
  .normalized-hero {{
    background: linear-gradient(135deg, #eff6ff 0%, #ffffff 100%);
    border: 1px solid #bfdbfe;
    border-radius: 18px;
    padding: 20px 24px;
    margin-bottom: 18px;
    box-shadow: 0 16px 48px rgba(15, 23, 42, 0.06);
  }}
  .normalized-hero p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
  .normalized-hero strong {{ display: block; margin-bottom: 6px; color: var(--accent); }}
  .normalized-panel {{
    background: var(--panel-bg);
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 12px 36px rgba(15, 23, 42, 0.05);
  }}
  .normalized-panel-header {{ margin-bottom: 14px; }}
  .normalized-panel-header h2 {{ margin: 0 0 6px; font-size: 20px; }}
  .normalized-panel-header p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
  .normalized-chart-host {{
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    background: #fff;
    overflow: hidden;
    padding: 6px;
  }}
  .normalized-chart-host .plotly-graph-div,
  .normalized-chart-host .js-plotly-plot,
  .normalized-chart-host .plot-container {{
    width: 100% !important;
  }}
  @media (max-width: 720px) {{
    .normalized-page-shell {{ padding: 20px 14px 28px; }}
    .normalized-hero {{ padding: 18px; }}
    .normalized-panel {{ padding: 14px; }}
  }}
</style>
""".strip()

        def wrap_body(body_inner: str) -> str:
            return f"""
<main id=\"{ROOT_ID}\" class=\"normalized-page-shell\">
  <section class=\"normalized-hero\">
    <strong>Normalized result page</strong>
    <p>The backend wrapped this HTML because the generated output did not provide the standard report-style layout. The interactive Plotly content is preserved below.</p>
  </section>
  <section class=\"normalized-panel\">
    <div class=\"normalized-panel-header\">
      <h2>Interactive diagram</h2>
      <p>This fallback shell keeps the result readable in the frontend iframe even when the generated page is structurally minimal.</p>
    </div>
    <div class=\"normalized-chart-host\">{body_inner}</div>
  </section>
</main>
""".strip()

        if re.search(r"<head[^>]*>", html_content, flags=re.IGNORECASE):
            normalized = re.sub(r"(<head[^>]*>)", r"\1\n" + injected_head, html_content, count=1, flags=re.IGNORECASE)
        else:
            normalized = f"<!DOCTYPE html><html><head>{injected_head}</head>{html_content}</html>"

        body_match = re.search(r"(<body[^>]*>)([\s\S]*?)(</body>)", normalized, flags=re.IGNORECASE)
        if body_match:
            body_inner = body_match.group(2).strip()
            wrapped_body = wrap_body(body_inner)
            normalized = f"{normalized[:body_match.start()]}{body_match.group(1)}\n{wrapped_body}\n{body_match.group(3)}{normalized[body_match.end():]}"
        else:
            normalized = f"<!DOCTYPE html><html><head>{injected_head}</head><body>{wrap_body(html_content)}</body></html>"

        return normalized

    def execute(self, run_id: str, code: str) -> ExecutorResult:
        run_dir = self.artifact_service.get_run_dir(run_id)
        code_path = self.artifact_service.get_code_path(run_id)
        result_path = self.artifact_service.get_result_path(run_id)

        ensure_directory(run_dir)
        remove_file_if_exists(result_path)
        write_text_file(code_path, code)

        completed = subprocess.run(
            [self.python_executable, str(code_path)],
            cwd=run_dir,
            capture_output=True,
            text=True,
        )

        html_content = read_text_file_if_exists(result_path)
        stderr = completed.stderr.strip()

        if html_content is not None:
            normalized_html = self._normalize_result_html(html_content)
            if normalized_html != html_content:
                write_text_file(result_path, normalized_html)
            html_content = normalized_html

        if completed.returncode == 0 and html_content is None:
            missing_file_message = f"Execution finished but {result_path.name} was not created."
            stderr = f"{stderr}\n{missing_file_message}".strip()

        success = completed.returncode == 0 and html_content is not None

        return ExecutorResult(
            success=success,
            stdout=completed.stdout.strip(),
            stderr=stderr,
            html_content=html_content,
            html_path=str(result_path) if html_content is not None else None,
        )


class DockerExecutor(PythonExecutor):
    def execute(self, code: str) -> ExecutorResult:
        _ = code
        raise NotImplementedError("DockerExecutor is reserved for a future version.")
