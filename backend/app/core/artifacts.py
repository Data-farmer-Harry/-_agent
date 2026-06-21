from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from mimetypes import guess_type
import re
from pathlib import Path
import shutil
from uuid import uuid4

from app.config import settings
from app.core.observability import log_event
from app.core.provenance import build_reproducibility_record
from app.state import AgentRunResponse, ArtifactRef, RunRecordSummary, RunTrace
from app.utils.path_utils import ensure_directory, read_json_file_if_exists, read_text_file_if_exists, write_json_file, write_text_file


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


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

    def get_manifest_path(self, run_id: str) -> Path:
        return self.get_run_dir(run_id) / settings.artifact_manifest_file_name

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

    def _artifact_manifest_entry(
        self,
        *,
        name: str,
        kind: str,
        path: Path,
        essential: bool,
        source: str,
        url: str = "",
    ) -> dict[str, object]:
        entry: dict[str, object] = {
            "name": name,
            "kind": kind,
            "path": str(path),
            "url": url,
            "essential": essential,
            "source": source,
            "exists": path.exists() and path.is_file(),
            "sha256": "",
            "size_bytes": None,
            "modified_at": "",
        }
        if not path.exists() or not path.is_file():
            return entry
        try:
            sha256, size = _sha256_file(path)
            entry["sha256"] = sha256
            entry["size_bytes"] = size
            entry["modified_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError as exc:
            entry["warning"] = str(exc)
        return entry

    def build_artifact_manifest(
        self,
        response: AgentRunResponse,
        *,
        summary_path: Path,
        provenance_path: Path,
    ) -> dict[str, object]:
        run_dir = self.get_run_dir(response.run_id)
        seen: set[str] = set()
        artifacts: list[dict[str, object]] = []

        def add_entry(*, name: str, kind: str, path: Path, essential: bool, source: str, url: str = "") -> None:
            key = str(path)
            if key in seen:
                return
            seen.add(key)
            artifacts.append(
                self._artifact_manifest_entry(
                    name=name,
                    kind=kind,
                    path=path,
                    essential=essential,
                    source=source,
                    url=url,
                )
            )

        add_entry(name=settings.summary_file_name, kind="json", path=summary_path, essential=True, source="run_summary")
        add_entry(name=settings.trace_file_name, kind="json", path=self.get_trace_path(response.run_id), essential=True, source="trace")
        add_entry(name="provenance.json", kind="json", path=provenance_path, essential=True, source="provenance")
        result_path = self.get_result_path(response.run_id)
        if result_path.exists():
            add_entry(name=settings.result_file_name, kind="html", path=result_path, essential=True, source="result_html")
        for artifact in response.artifacts:
            raw_path = artifact.path or ""
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = run_dir / path
            add_entry(
                name=artifact.name,
                kind=artifact.kind,
                path=path,
                essential=artifact.kind in {"html", "image", "video", "markdown", "json"},
                source="response_artifact",
                url=artifact.url or "",
            )
        total_size = sum(int(item.get("size_bytes") or 0) for item in artifacts)
        return {
            "schema_version": "artifact-manifest/v1",
            "generated_at": _now_iso(),
            "run_id": response.run_id,
            "conversation_id": response.conversation_id,
            "status": response.run_status,
            "route_name": response.route.name,
            "compute_domain": response.route.compute_domain,
            "run_dir": str(run_dir),
            "artifact_count": len(artifacts),
            "total_size_bytes": total_size,
            "artifacts": artifacts,
        }

    def write_run_summary(self, response: AgentRunResponse) -> Path:
        provenance = build_reproducibility_record(response)
        provenance_path = self.get_artifact_path(response.run_id, "provenance.json")
        manifest_path = self.get_manifest_path(response.run_id)
        write_json_file(provenance_path, provenance.model_dump(mode="json"))
        metadata = {
            **response.metadata,
            "provenance": provenance.model_dump(mode="json"),
            "provenance_path": str(provenance_path),
            "artifact_manifest_path": str(manifest_path),
        }
        summary = RunRecordSummary(
            run_id=response.run_id,
            conversation_id=response.conversation_id,
            status=response.run_status,
            route=response.route,
            final_message=response.final_message,
            summary=response.summary,
            artifacts=response.artifacts,
            trace=response.trace,
            metadata=metadata,
        )
        summary_path = write_json_file(self.get_summary_path(response.run_id), summary.model_dump(mode="json"))
        manifest = self.build_artifact_manifest(response, summary_path=summary_path, provenance_path=provenance_path)
        write_json_file(manifest_path, manifest)
        request_id = str(response.metadata.get("request_id") or response.summary.get("request_id") or "")
        log_event(
            "artifact.manifest_written",
            request_id=request_id,
            run_id=response.run_id,
            conversation_id=response.conversation_id,
            message="Artifact manifest written.",
            artifact_count=manifest["artifact_count"],
            total_size_bytes=manifest["total_size_bytes"],
            manifest_path=str(manifest_path),
        )
        return summary_path

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

    def artifact_inventory(self, *, limit: int = 500) -> dict[str, object]:
        runs: list[dict[str, object]] = []
        total_size = 0
        for run_dir in sorted((item for item in self.runs_root.iterdir() if item.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True):
            if len(runs) >= limit:
                break
            summary = self.load_run_summary(run_dir.name)
            manifest = read_json_file_if_exists(run_dir / settings.artifact_manifest_file_name)
            size = int(manifest.get("total_size_bytes", 0)) if isinstance(manifest, dict) else _directory_size(run_dir)
            total_size += size
            runs.append(
                {
                    "run_id": run_dir.name,
                    "conversation_id": summary.conversation_id if summary else "",
                    "status": summary.status if summary else "unknown",
                    "route_name": summary.route.name if summary else "",
                    "updated_at": summary.updated_at if summary else datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "size_bytes": size,
                    "artifact_count": int(manifest.get("artifact_count", 0)) if isinstance(manifest, dict) else len([item for item in run_dir.iterdir() if item.is_file()]),
                    "has_manifest": isinstance(manifest, dict),
                }
            )
        return {
            "generated_at": _now_iso(),
            "runs_root": str(self.runs_root),
            "run_count": len(runs),
            "total_size_bytes": total_size,
            "retention_policy": {
                "keep_latest": settings.artifact_retention_keep_latest,
                "max_age_days": settings.artifact_retention_max_age_days,
            },
            "runs": runs,
        }

    def cleanup_runs(
        self,
        *,
        keep_latest: int | None = None,
        max_age_days: int | None = None,
        dry_run: bool = True,
    ) -> dict[str, object]:
        keep_latest = settings.artifact_retention_keep_latest if keep_latest is None else max(0, keep_latest)
        max_age_days = settings.artifact_retention_max_age_days if max_age_days is None else max(0, max_age_days)
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 24 * 60 * 60 if max_age_days > 0 else None
        run_dirs = sorted((item for item in self.runs_root.iterdir() if item.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
        candidates: list[dict[str, object]] = []
        deleted = 0
        reclaimed = 0
        for index, run_dir in enumerate(run_dirs):
            reasons: list[str] = []
            if keep_latest >= 0 and index >= keep_latest:
                reasons.append(f"beyond_keep_latest_{keep_latest}")
            if cutoff is not None and run_dir.stat().st_mtime < cutoff:
                reasons.append(f"older_than_{max_age_days}_days")
            if not reasons:
                continue
            size = _directory_size(run_dir)
            candidates.append({"run_id": run_dir.name, "path": str(run_dir), "size_bytes": size, "reasons": reasons})
            if not dry_run:
                shutil.rmtree(run_dir)
                deleted += 1
                reclaimed += size
        return {
            "generated_at": _now_iso(),
            "dry_run": dry_run,
            "runs_root": str(self.runs_root),
            "policy": {"keep_latest": keep_latest, "max_age_days": max_age_days},
            "total_runs_seen": len(run_dirs),
            "candidate_count": len(candidates),
            "deleted_count": deleted,
            "reclaimed_bytes": reclaimed,
            "candidates": candidates,
        }

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
