from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field


RUNTIME_PROFILE_VERSION = "runtime-profile/v1"


class RuntimeExecutionProfile(BaseModel):
    schema_version: str = RUNTIME_PROFILE_VERSION
    runtime_name: str
    run_id: str
    status: str
    termination_reason: str = ""
    started_at: str = ""
    finished_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_seconds: float = 0.0
    step_count: int = 0
    failed_step_count: int = 0
    artifact_count: int = 0
    tool_chain: list[str] = Field(default_factory=list)
    capability_tags: list[str] = Field(default_factory=list)
    review_passed: bool | None = None
    trust_level: str = "unknown"
    run_mode: str = ""
    warnings: list[str] = Field(default_factory=list)


def initialize_runtime_state(state: dict[str, Any], *, runtime_name: str, capability_tags: list[str]) -> None:
    state["runtime_telemetry"] = {
        "runtime_name": runtime_name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_perf": perf_counter(),
        "capability_tags": capability_tags,
    }


def build_runtime_execution_profile(
    state: dict[str, Any],
    *,
    success: bool,
    termination_reason: str,
    result_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    telemetry = state.get("runtime_telemetry", {}) or {}
    started_perf = telemetry.get("started_perf")
    duration = 0.0
    if isinstance(started_perf, (int, float)):
        duration = max(0.0, perf_counter() - float(started_perf))

    trace = state.get("trace", []) or []
    artifacts = state.get("artifacts", []) or []
    review = state.get("review", {}) or {}
    profile = result_profile or {}
    warning_items: list[str] = []
    for candidate in (
        profile.get("warnings", []),
        review.get("issues", []),
        review.get("advisory_issues", []),
    ):
        if not isinstance(candidate, list):
            continue
        warning_items.extend(str(item) for item in candidate if str(item).strip())

    runtime_profile = RuntimeExecutionProfile(
        runtime_name=str(telemetry.get("runtime_name") or "unknown"),
        run_id=str(state.get("run_id") or ""),
        status="completed" if success else "failed",
        termination_reason=termination_reason,
        started_at=str(telemetry.get("started_at") or ""),
        duration_seconds=round(duration, 3),
        step_count=len(trace),
        failed_step_count=sum(1 for item in trace if not getattr(item, "success", False)),
        artifact_count=len(artifacts),
        tool_chain=[str(getattr(item, "tool_name", "")) for item in trace],
        capability_tags=list(telemetry.get("capability_tags") or []),
        review_passed=review.get("passed") if isinstance(review.get("passed"), bool) else None,
        trust_level=str(profile.get("trust_level") or "unknown"),
        run_mode=str(state.get("run_mode") or state.get("generation_source") or ""),
        warnings=list(dict.fromkeys(warning_items))[:12],
    )
    return runtime_profile.model_dump(mode="json")
