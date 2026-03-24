from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import ArtifactRef, RunTrace
from app.utils.file_utils import ensure_directory, read_text_file_if_exists, write_text_file


class ArtifactService:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or settings.tmp_dir

    @property
    def runs_root(self) -> Path:
        return ensure_directory(self.root_dir / settings.runs_dir_name)

    @property
    def latest_result_path(self) -> Path:
        return self.root_dir / settings.latest_result_file_name

    def create_run_id(self) -> str:
        return uuid4().hex[:12]

    def get_run_dir(self, run_id: str) -> Path:
        return ensure_directory(self.runs_root / run_id)

    def get_code_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / settings.code_file_name

    def get_result_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / settings.result_file_name

    def get_trace_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / settings.trace_file_name

    def build_artifact_ref(self, kind: str, name: str, path: Path, content: str | None = None) -> ArtifactRef:
        return ArtifactRef(kind=kind, name=name, path=str(path), content=content)

    def write_trace(self, trace: RunTrace) -> Path:
        trace_path = self.get_trace_path(trace.run_id)
        return write_text_file(trace_path, trace.model_dump_json(indent=2))

    def load_latest_html(self) -> str | None:
        return read_text_file_if_exists(self.latest_result_path)

    def load_run_html(self, run_id: str) -> str | None:
        return read_text_file_if_exists(self.get_result_path(run_id))

    def write_latest_html(self, html_content: str) -> Path:
        return write_text_file(self.latest_result_path, html_content)

    def load_trace_dict(self, run_id: str) -> dict | None:
        raw = read_text_file_if_exists(self.get_trace_path(run_id))
        if raw is None:
            return None
        return json.loads(raw)
