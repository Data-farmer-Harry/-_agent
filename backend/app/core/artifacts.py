from __future__ import annotations

import json
from mimetypes import guess_type
import re
from pathlib import Path
import shutil
from uuid import uuid4

from app.config import settings
from app.state import AgentRunResponse, ArtifactRef, RunRecordSummary, RunTrace
from app.utils.path_utils import ensure_directory, read_json_file_if_exists, read_text_file_if_exists, write_json_file, write_text_file


class ArtifactService:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or settings.tmp_dir

    @property
    def runs_root(self) -> Path:
        return ensure_directory(self.root_dir / settings.runs_dir_name)

    @property
    def summary_file_name(self) -> str:
        return settings.summary_file_name

    def create_run_id(self) -> str:
        return uuid4().hex[:12]

    def get_run_dir(self, run_id: str) -> Path:
        return ensure_directory(self.runs_root / run_id)

    @staticmethod
    def _sanitize_artifact_filename(filename: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (filename or "").strip())
        return cleaned or settings.code_file_name

    def get_code_path(self, run_id: str, filename: str | None = None) -> Path:
        return self.get_run_dir(run_id) / self._sanitize_artifact_filename(filename or settings.code_file_name)

    def get_result_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / settings.result_file_name

    def get_trace_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / settings.trace_file_name

    def get_summary_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / self.summary_file_name

    def get_artifact_path(self, run_id: str, artifact_name: str) -> Path:
        return self.get_run_dir(run_id) / self._sanitize_artifact_filename(artifact_name)

    def build_artifact_url(self, run_id: str, artifact_name: str) -> str:
        safe_name = self._sanitize_artifact_filename(artifact_name)
        return f"/api/runs/{run_id}/artifacts/{safe_name}"

    def build_artifact_ref(
        self,
        kind: str,
        name: str,
        path: Path | str | None,
        content: str | None = None,
        url: str | None = None,
        metadata: dict | None = None,
    ) -> ArtifactRef:
        normalized_path = str(path) if path is not None else None
        return ArtifactRef(kind=kind, name=name, path=normalized_path, url=url, content=content, metadata=metadata or {})

    def write_trace(self, trace: RunTrace) -> Path:
        trace_path = self.get_trace_path(trace.run_id)
        return write_text_file(trace_path, trace.model_dump_json(indent=2))

    @staticmethod
    def _normalize_phase_diagram_html(html_content: str) -> str:
        root_marker = 'id="phase-diagram-agent-result"'
        patch_marker = "phase-diagram-runtime-patch"
        if root_marker not in html_content or patch_marker in html_content:
            return html_content

        injected = """
<style id="phase-diagram-runtime-patch">
  #phase-diagram-agent-result {
    width: 100%;
    height: 100vh;
    overflow: hidden;
    padding: 18px;
    display: grid;
    grid-template-rows: minmax(0, 1fr);
  }
  #phase-diagram-agent-result .topbar,
  #phase-diagram-agent-result .panel-header {
    display: none !important;
  }
  #phase-diagram-agent-result .workspace {
    height: 100% !important;
    min-height: 0 !important;
    align-items: stretch !important;
  }
  #phase-diagram-agent-result .figure-panel {
    height: 100% !important;
    min-height: 0 !important;
    min-width: 0 !important;
    display: flex !important;
    flex-direction: column !important;
  }
  #phase-diagram-agent-result .figure-stage {
    min-height: 0 !important;
    height: 100% !important;
    flex: 1 1 auto !important;
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    overflow: hidden !important;
    padding: 12px !important;
  }
  #phase-diagram-agent-result .figure-stage img {
    width: auto !important;
    height: auto !important;
    max-width: 100% !important;
    max-height: 100% !important;
    margin: 0 auto !important;
  }
  #phase-diagram-agent-result .sidebar {
    height: 100% !important;
    max-height: 100% !important;
    min-height: 0 !important;
    overflow-y: auto !important;
  }
  @media (max-width: 720px) {
    #phase-diagram-agent-result {
      height: auto !important;
      display: block !important;
      overflow: visible !important;
    }
    #phase-diagram-agent-result .workspace {
      height: auto !important;
    }
    #phase-diagram-agent-result .figure-stage {
      min-height: 320px !important;
    }
    #phase-diagram-agent-result .sidebar {
      height: auto !important;
      max-height: none !important;
      overflow: visible !important;
    }
  }
</style>
""".strip()

        if "</head>" in html_content:
            return html_content.replace("</head>", f"{injected}\n</head>", 1)
        return f"{injected}\n{html_content}"

    def load_run_html(self, run_id: str) -> str | None:
        html_content = read_text_file_if_exists(self.get_result_path(run_id))
        if html_content is None:
            return None
        return self._normalize_phase_diagram_html(html_content)

    def load_trace_dict(self, run_id: str) -> dict | None:
        raw = read_text_file_if_exists(self.get_trace_path(run_id))
        if raw is None:
            return None
        return json.loads(raw)

    def write_run_summary(self, response: AgentRunResponse) -> Path:
        summary = RunRecordSummary(
            run_id=response.run_id,
            conversation_id=response.conversation_id,
            status=response.run_status,
            route=response.route,
            final_message=response.final_message,
            summary=response.summary,
            artifacts=response.artifacts,
            trace=response.trace,
            metadata=response.metadata,
        )
        return write_json_file(self.get_summary_path(response.run_id), summary.model_dump(mode="json"))

    def load_run_summary(self, run_id: str) -> RunRecordSummary | None:
        payload = read_json_file_if_exists(self.get_summary_path(run_id))
        if payload is None:
            return None
        return RunRecordSummary.model_validate(payload)

    def list_run_summaries(self, *, limit: int = 50) -> list[RunRecordSummary]:
        summaries: list[tuple[float, RunRecordSummary]] = []
        for run_dir in self.runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            summary_path = run_dir / self.summary_file_name
            if not summary_path.exists():
                continue
            try:
                record = RunRecordSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            summaries.append((summary_path.stat().st_mtime, record))
        summaries.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in summaries[:limit]]

    def resolve_artifact_path(self, run_id: str, artifact_name: str) -> Path | None:
        safe_name = self._sanitize_artifact_filename(artifact_name)
        candidate = self.get_run_dir(run_id) / safe_name
        return candidate if candidate.exists() else None

    def delete_run(self, run_id: str) -> bool:
        run_dir = self.runs_root / run_id
        if not run_dir.exists() or not run_dir.is_dir():
            return False
        shutil.rmtree(run_dir)
        return True

    def delete_conversation(self, conversation_id: str) -> int:
        deleted = 0
        for record in self.list_run_summaries(limit=500):
            if record.conversation_id != conversation_id:
                continue
            if self.delete_run(record.run_id):
                deleted += 1
        return deleted

    @staticmethod
    def guess_media_type(path: Path) -> str:
        guessed, _ = guess_type(path.name)
        return guessed or "application/octet-stream"
