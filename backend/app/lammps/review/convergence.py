from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.lammps.review.models import stable_hash
from app.state import LammpsRequest


class RepairConvergenceReport(BaseModel):
    schema_version: str = "lammps-repair-convergence/v1"
    allow_repair: bool = True
    stage: str
    termination_reason: str = ""
    attempts_used: int = 0
    repair_budget: int = 0
    repair_budget_remaining: int = 0
    current_request_signature: str = ""
    candidate_request_signature: str = ""
    previous_request_signatures: list[str] = Field(default_factory=list)
    score_before_repair: float | None = None
    previous_repair_score: float | None = None
    score_delta: float | None = None
    min_score_improvement: float = 1.0
    budget_exhausted: bool = False
    stagnation_detected: bool = False
    oscillation_detected: bool = False
    policy_accepted: bool = True
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def request_signature(request_or_payload: LammpsRequest | dict[str, Any]) -> str:
    payload = (
        request_or_payload.model_dump(mode="json")
        if isinstance(request_or_payload, LammpsRequest)
        else dict(request_or_payload)
    )
    return stable_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def evaluate_repair_convergence(
    *,
    repair_history: list[dict[str, Any]],
    current_request: LammpsRequest,
    stage: str,
    repair_budget: int,
    current_score: float | None = None,
    candidate_request: LammpsRequest | None = None,
    policy_accepted: bool = True,
    policy_termination_reason: str = "",
    min_score_improvement: float = 1.0,
) -> RepairConvergenceReport:
    attempts_used = _repair_attempts_used(repair_history)
    budget = max(0, repair_budget)
    consumes_attempt = candidate_request is not None or not policy_accepted
    remaining = max(0, budget - attempts_used - (1 if consumes_attempt else 0))
    current_signature = request_signature(current_request)
    previous_signatures = _previous_request_signatures(repair_history)
    previous_score = _previous_review_repair_score(repair_history)
    score_delta = current_score - previous_score if current_score is not None and previous_score is not None else None

    base = {
        "stage": stage,
        "attempts_used": attempts_used,
        "repair_budget": budget,
        "repair_budget_remaining": remaining,
        "current_request_signature": current_signature,
        "previous_request_signatures": previous_signatures,
        "score_before_repair": current_score,
        "previous_repair_score": previous_score,
        "score_delta": score_delta,
        "min_score_improvement": min_score_improvement,
        "policy_accepted": policy_accepted,
    }
    if not consumes_attempt and attempts_used >= budget:
        return RepairConvergenceReport(
            **base,
            allow_repair=False,
            termination_reason="repair_budget_exhausted",
            budget_exhausted=True,
            message="Repair budget is exhausted before another LLM repair call.",
        )
    if not consumes_attempt and stage == "review" and score_delta is not None and score_delta < min_score_improvement:
        return RepairConvergenceReport(
            **base,
            allow_repair=False,
            termination_reason="repair_stagnation_detected",
            stagnation_detected=True,
            message="Previous review repair did not improve the Red review score enough to continue.",
        )
    if not policy_accepted:
        return RepairConvergenceReport(
            **base,
            allow_repair=False,
            termination_reason=policy_termination_reason or "patch_policy_rejected",
            message="Blue patch was rejected before convergence execution.",
        )
    if candidate_request is None:
        return RepairConvergenceReport(
            **base,
            allow_repair=True,
            message="Repair budget and convergence checks allow one more repair attempt.",
        )

    candidate_signature = request_signature(candidate_request)
    oscillation_detected = candidate_signature in set(previous_signatures)
    if oscillation_detected:
        return RepairConvergenceReport(
            **base,
            allow_repair=False,
            candidate_request_signature=candidate_signature,
            termination_reason="repair_oscillation_detected",
            oscillation_detected=True,
            message="Candidate repair returns to a previously seen request state.",
        )
    return RepairConvergenceReport(
        **base,
        allow_repair=True,
        candidate_request_signature=candidate_signature,
        message="Repair patch passed convergence checks.",
    )


def _repair_attempts_used(repair_history: list[dict[str, Any]]) -> int:
    return sum(1 for item in repair_history if "raw_payload" in item or "policy_report" in item)


def _previous_request_signatures(repair_history: list[dict[str, Any]]) -> list[str]:
    signatures: list[str] = []
    for item in repair_history:
        policy_report = item.get("policy_report")
        if not isinstance(policy_report, dict):
            continue
        for key in ("before_request", "after_request"):
            payload = policy_report.get(key)
            if isinstance(payload, dict) and payload:
                signature = request_signature(payload)
                if signature not in signatures:
                    signatures.append(signature)
    return signatures


def _previous_review_repair_score(repair_history: list[dict[str, Any]]) -> float | None:
    for item in reversed(repair_history):
        policy_report = item.get("policy_report")
        convergence_report = item.get("convergence_report")
        if not isinstance(policy_report, dict) or not isinstance(convergence_report, dict):
            continue
        if not policy_report.get("accepted") or not convergence_report.get("allow_repair"):
            continue
        score = convergence_report.get("score_before_repair")
        if score is None:
            continue
        try:
            return float(score)
        except (TypeError, ValueError):
            continue
    return None
