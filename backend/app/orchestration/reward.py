from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.orchestration.dag import DAGNode, DAGPlan, topological_sort
from app.orchestration.executor import DAGExecutionResult


@dataclass(frozen=True)
class ProcessRewardWeights:
    critical_coverage: float = 2.4
    retry_safety: float = 0.7
    evidence_outputs: float = 0.5
    dependency_validity: float = 1.4
    latency_cost: float = 0.45
    complexity_cost: float = 0.15
    failure_penalty: float = 2.5
    timeout_penalty: float = 1.8
    fallback_penalty: float = 0.25
    completion_reward: float = 1.2


@dataclass(frozen=True)
class StepReward:
    node_id: str
    reward: float
    components: dict[str, float] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class PlanScore:
    plan_id: str
    score: float
    step_rewards: tuple[StepReward, ...]
    estimated_latency_seconds: float
    critical_nodes: int
    retryable_nodes: int

    def public_payload(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "score": self.score,
            "estimated_latency_seconds": self.estimated_latency_seconds,
            "critical_nodes": self.critical_nodes,
            "retryable_nodes": self.retryable_nodes,
            "step_rewards": [
                {"node_id": item.node_id, "reward": item.reward, "components": item.components, "reason": item.reason}
                for item in self.step_rewards
            ],
        }


@dataclass(frozen=True)
class PlanSearchResult:
    selected_plan: DAGPlan
    candidate_scores: tuple[PlanScore, ...]
    search_strategy: str = "best_of_n_process_reward"

    def public_payload(self) -> dict[str, object]:
        return {
            "strategy": self.search_strategy,
            "selected_plan_id": self.selected_plan.plan_id,
            "candidate_count": len(self.candidate_scores),
            "candidate_scores": [score.public_payload() for score in self.candidate_scores],
        }


class ProcessRewardModel:
    """Deterministic PRM for scientific DAG plans and execution traces.

    The interface intentionally mirrors a learned value model: a future model
    can replace the scoring function without changing orchestration contracts.
    Current rewards are verifiable from plan structure and runtime outcomes.
    """

    def __init__(self, weights: ProcessRewardWeights | None = None) -> None:
        self.weights = weights or ProcessRewardWeights()

    def score_plan(self, plan: DAGPlan) -> PlanScore:
        node_map = plan.node_map()
        step_rewards: list[StepReward] = []
        critical_nodes = 0
        retryable_nodes = 0
        estimated_latency = 0.0
        for node_id in topological_sort(plan):
            node = node_map[node_id]
            critical_nodes += int(node.critical)
            retryable_nodes += int(node.retryable)
            dependency_valid = all(dependency in node_map for dependency in node.dependencies)
            retry_safe = node.retryable and node.max_attempts > 1
            evidence_outputs = sum("evidence" in key or "report" in key for key in node.output_keys)
            latency = node.timeout_seconds * (1.0 if node.critical else 0.35)
            estimated_latency += latency
            components = {
                "critical_coverage": self.weights.critical_coverage if node.critical else 0.15,
                "dependency_validity": self.weights.dependency_validity if dependency_valid else -self.weights.failure_penalty,
                "retry_safety": self.weights.retry_safety if retry_safe else 0.0,
                "evidence_outputs": min(evidence_outputs, 2) * self.weights.evidence_outputs,
                "latency_cost": -self.weights.latency_cost * min(latency / 120.0, 1.0),
                "complexity_cost": -self.weights.complexity_cost,
            }
            reward = sum(components.values())
            step_rewards.append(
                StepReward(
                    node_id=node_id,
                    reward=round(reward, 6),
                    components={key: round(value, 6) for key, value in components.items()},
                    reason="static_plan_contract",
                )
            )
        total = sum(item.reward for item in step_rewards)
        return PlanScore(
            plan_id=plan.plan_id,
            score=round(total, 6),
            step_rewards=tuple(step_rewards),
            estimated_latency_seconds=round(estimated_latency, 3),
            critical_nodes=critical_nodes,
            retryable_nodes=retryable_nodes,
        )

    def score_execution(self, plan: DAGPlan, result: DAGExecutionResult) -> dict[str, object]:
        rewards: list[StepReward] = []
        for node_id in plan.topological_order():
            node_result = result.results.get(node_id)
            if node_result is None:
                rewards.append(StepReward(node_id=node_id, reward=-self.weights.failure_penalty, reason="missing_result"))
                continue
            components: dict[str, float] = {}
            if node_result.status == "completed":
                components["completion"] = self.weights.completion_reward
            elif node_result.status == "completed_with_fallback":
                components["completion"] = self.weights.completion_reward - self.weights.fallback_penalty
            elif node_result.status == "timed_out":
                components["timeout"] = -self.weights.timeout_penalty
            elif node_result.status in {"failed", "skipped"}:
                components["failure"] = -self.weights.failure_penalty
            if node_result.evidence_refs:
                components["traceability"] = self.weights.evidence_outputs
            reward = sum(components.values())
            rewards.append(
                StepReward(
                    node_id=node_id,
                    reward=round(reward, 6),
                    components={key: round(value, 6) for key, value in components.items()},
                    reason=f"runtime_status:{node_result.status}",
                )
            )
        total = sum(item.reward for item in rewards)
        completed = sum(item.reason in {"runtime_status:completed", "runtime_status:completed_with_fallback"} for item in rewards)
        return {
            "schema_version": "process-reward-trace/v1",
            "total_reward": round(total, 6),
            "normalized_reward": round(total / max(len(rewards), 1), 6),
            "progress_rate": round(completed / max(len(rewards), 1), 6),
            "step_rewards": [
                {"node_id": item.node_id, "reward": item.reward, "components": item.components, "reason": item.reason}
                for item in rewards
            ],
        }


def build_plan_variants(plan: DAGPlan) -> tuple[DAGPlan, ...]:
    """Create semantically equivalent reliability/efficiency candidates."""

    robust_nodes: list[DAGNode] = []
    efficient_nodes: list[DAGNode] = []
    for node in plan.nodes:
        robust_nodes.append(
            node.model_copy(
                update={
                    "timeout_seconds": min(node.timeout_seconds * 1.2, node.timeout_seconds + 60.0),
                    "max_attempts": min(node.max_attempts + 1, 3) if node.retryable else node.max_attempts,
                }
            )
        )
        efficient_nodes.append(
            node.model_copy(
                update={
                    "timeout_seconds": max(5.0, node.timeout_seconds * (0.9 if node.critical else 0.7)),
                }
            )
        )
    robust = plan.model_copy(
        update={
            "plan_id": f"{plan.plan_id}/robust",
            "nodes": robust_nodes,
            "metadata": {**plan.metadata, "candidate_policy": "robust"},
        }
    )
    efficient = plan.model_copy(
        update={
            "plan_id": f"{plan.plan_id}/efficient",
            "nodes": efficient_nodes,
            "global_timeout_seconds": max(30.0, plan.global_timeout_seconds * 0.85),
            "metadata": {**plan.metadata, "candidate_policy": "efficient"},
        }
    )
    baseline = plan.model_copy(update={"metadata": {**plan.metadata, "candidate_policy": "baseline"}})
    return baseline, robust, efficient


def search_plans(
    candidates: Iterable[DAGPlan],
    *,
    reward_model: ProcessRewardModel | None = None,
    latency_budget_seconds: float | None = None,
    risk_level: float = 0.5,
) -> PlanSearchResult:
    model = reward_model or ProcessRewardModel()
    plans = list(candidates)
    if not plans:
        raise ValueError("At least one DAG plan candidate is required")
    scored = [(plan, model.score_plan(plan)) for plan in plans]
    normalized_risk = max(0.0, min(float(risk_level), 1.0))

    def objective(item: tuple[DAGPlan, PlanScore]) -> tuple[float, float]:
        plan, score = item
        budget_penalty = 0.0
        if latency_budget_seconds is not None and score.estimated_latency_seconds > latency_budget_seconds:
            budget_penalty = (score.estimated_latency_seconds - latency_budget_seconds) / max(latency_budget_seconds, 1.0)
        policy = str(plan.metadata.get("candidate_policy") or "baseline")
        policy_bonus = {
            "robust": 1.5 * normalized_risk,
            "efficient": 1.5 * (1.0 - normalized_risk),
            "baseline": 0.3,
        }.get(policy, 0.0)
        return score.score + policy_bonus - budget_penalty, -score.estimated_latency_seconds

    selected, _ = max(scored, key=objective)
    return PlanSearchResult(selected_plan=selected, candidate_scores=tuple(score for _, score in scored))
