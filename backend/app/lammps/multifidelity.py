from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal


PilotAction = Literal["skip", "continue_full", "repair", "stop"]


@dataclass(frozen=True)
class MultiFidelityPlan:
    enabled: bool
    requires_pilot: bool
    pilot_steps: int
    full_steps: int
    estimated_cost_ratio: float
    initial_risk: float
    reasons: tuple[str, ...]

    def public_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "requires_pilot": self.requires_pilot,
            "pilot_steps": self.pilot_steps,
            "full_steps": self.full_steps,
            "estimated_cost_ratio": self.estimated_cost_ratio,
            "initial_risk": self.initial_risk,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class PilotDecision:
    action: PilotAction
    stability_score: float
    failure_probability: float
    information_gain: float
    value_of_information: float
    reasons: tuple[str, ...]

    def public_payload(self) -> dict[str, object]:
        return {
            "action": self.action,
            "stability_score": self.stability_score,
            "failure_probability": self.failure_probability,
            "information_gain": self.information_gain,
            "value_of_information": self.value_of_information,
            "reasons": list(self.reasons),
        }


def plan_multifidelity_run(request: dict[str, Any], *, enabled: bool) -> MultiFidelityPlan:
    steps = max(1, int(request.get("steps") or request.get("run_steps") or 0))
    temperature = float(request.get("temperature") or 0.0)
    timestep = float(request.get("time_step") or 0.001)
    task_type = str(request.get("task_type") or "equilibration")
    risk = 0.08
    reasons: list[str] = []
    if temperature > 1200:
        risk += 0.25
        reasons.append("high_temperature")
    if timestep > 0.003:
        risk += 0.3
        reasons.append("large_timestep")
    if task_type == "heating":
        risk += 0.12
        reasons.append("temperature_ramp")
    if steps >= 10_000:
        risk += 0.12
        reasons.append("long_horizon")
    pilot_steps = min(2000, max(200, int(steps * 0.05)))
    requires = enabled and steps >= 1000 and (risk >= 0.18 or steps >= 5000)
    if not enabled:
        reasons.append("multifidelity_disabled")
    elif not requires:
        reasons.append("direct_full_run_is_cheaper")
    return MultiFidelityPlan(
        enabled=enabled,
        requires_pilot=requires,
        pilot_steps=min(pilot_steps, steps),
        full_steps=steps,
        estimated_cost_ratio=round(min(pilot_steps / max(steps, 1), 1.0), 6),
        initial_risk=round(min(risk, 0.95), 6),
        reasons=tuple(reasons),
    )


def evaluate_pilot(
    plan: MultiFidelityPlan,
    *,
    execution_success: bool,
    quality_passed: bool,
    scientific_result_passed: bool,
    metrics: dict[str, Any] | None = None,
    fatal_anomalies: int = 0,
) -> PilotDecision:
    metrics = metrics or {}
    reasons: list[str] = []
    stability = 1.0
    if not execution_success:
        stability -= 0.65
        reasons.append("pilot_execution_failed")
    if not quality_passed:
        stability -= 0.5
        reasons.append("pilot_quality_gate_failed")
    if not scientific_result_passed:
        stability -= 0.2
        reasons.append("pilot_not_scientifically_valid")
    if fatal_anomalies:
        stability -= min(0.7, 0.25 * fatal_anomalies)
        reasons.append("fatal_anomaly_detected")
    temperature_drift = abs(float(metrics.get("temperature_drift", 0.0) or 0.0))
    energy_drift = abs(float(metrics.get("energy_drift", 0.0) or 0.0))
    if temperature_drift > 0.25:
        stability -= 0.2
        reasons.append("temperature_drift")
    if energy_drift > 0.2:
        stability -= 0.25
        reasons.append("energy_drift")
    stability = max(0.0, min(stability, 1.0))
    posterior_failure = max(0.0, min(1.0 - stability, 1.0))
    prior_entropy = _binary_entropy(plan.initial_risk)
    posterior_entropy = _binary_entropy(posterior_failure)
    information_gain = max(0.0, prior_entropy - posterior_entropy)
    value = information_gain / max(plan.estimated_cost_ratio, 1e-6)
    if not execution_success or fatal_anomalies:
        action: PilotAction = "stop"
    elif not quality_passed or stability < 0.55:
        action = "repair"
    else:
        action = "continue_full"
        reasons.append("pilot_stability_gate_passed")
    return PilotDecision(
        action=action,
        stability_score=round(stability, 6),
        failure_probability=round(posterior_failure, 6),
        information_gain=round(information_gain, 6),
        value_of_information=round(value, 6),
        reasons=tuple(reasons),
    )


def _binary_entropy(probability: float) -> float:
    p = max(1e-9, min(float(probability), 1.0 - 1e-9))
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))
