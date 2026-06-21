from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
import sys
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.state import AgentRunResponse


PROVENANCE_SCHEMA_VERSION = "provenance/v1"


class ArtifactDigest(BaseModel):
    name: str
    kind: str
    path: str = ""
    sha256: str = ""
    size_bytes: int | None = None
    missing: bool = False


class ReproducibilityRecord(BaseModel):
    schema_version: str = PROVENANCE_SCHEMA_VERSION
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_id: str
    conversation_id: str = "default"
    route_name: str = ""
    compute_domain: str = "none"
    selected_tool: str = ""
    decision_source: str = ""
    decision_confidence: float | None = None
    request_message: str = ""
    runtime: dict[str, Any] = Field(default_factory=dict)
    llm_config: dict[str, Any] = Field(default_factory=dict)
    rag_config: dict[str, Any] = Field(default_factory=dict)
    runtime_profile: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactDigest] = Field(default_factory=list)
    trace_tools: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _artifact_digest(artifact: Any) -> ArtifactDigest:
    raw_path = getattr(artifact, "path", None)
    if not raw_path:
        return ArtifactDigest(
            name=str(getattr(artifact, "name", "")),
            kind=str(getattr(artifact, "kind", "")),
            path="",
            missing=True,
        )
    path = Path(str(raw_path))
    if not path.exists() or not path.is_file():
        return ArtifactDigest(
            name=str(getattr(artifact, "name", "")),
            kind=str(getattr(artifact, "kind", "")),
            path=str(path),
            missing=True,
        )
    sha256, size = _sha256_file(path)
    return ArtifactDigest(
        name=str(getattr(artifact, "name", "")),
        kind=str(getattr(artifact, "kind", "")),
        path=str(path),
        sha256=sha256,
        size_bytes=size,
    )


def build_reproducibility_record(response: AgentRunResponse) -> ReproducibilityRecord:
    summary = response.summary if isinstance(response.summary, dict) else {}
    request_message = str(summary.get("request_message") or "")
    warnings: list[str] = []
    artifact_digests = []
    for artifact in response.artifacts:
        try:
            artifact_digests.append(_artifact_digest(artifact))
        except OSError as exc:
            warnings.append(f"artifact_digest_failed:{getattr(artifact, 'name', '')}:{exc}")

    return ReproducibilityRecord(
        run_id=response.run_id,
        conversation_id=response.conversation_id,
        route_name=response.route.name,
        compute_domain=response.route.compute_domain,
        selected_tool=response.route.selected_tool or "",
        decision_source=response.route.decision_source,
        decision_confidence=response.route.decision_confidence,
        request_message=request_message,
        runtime={
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "app_version": settings.app_version,
        },
        llm_config={
            "llm_enabled": settings.llm_enabled,
            "llm_model": settings.llm_model,
            "llm_enable_thinking": settings.llm_enable_thinking,
            "llm_api_base_url": settings.llm_api_base_url,
            "llm_max_tokens": settings.llm_max_tokens,
        },
        rag_config={
            "thermo_rag_enabled": settings.thermo_rag_enabled,
            "thermo_embedding_backend": settings.thermo_rag_embedding_backend,
            "thermo_embedding_model": settings.thermo_rag_embedding_model,
            "thermo_bm25_weight": settings.thermo_rag_bm25_weight,
            "materials_rag_enabled": settings.materials_rag_enabled,
            "materials_embedding_backend": settings.materials_rag_embedding_backend,
            "materials_embedding_model": settings.materials_rag_embedding_model,
            "materials_bm25_weight": settings.materials_rag_bm25_weight,
        },
        runtime_profile=response.metadata.get("runtime_profile", {}) if isinstance(response.metadata, dict) else {},
        artifacts=artifact_digests,
        trace_tools=[observation.tool_name for observation in response.trace],
        warnings=warnings,
    )
