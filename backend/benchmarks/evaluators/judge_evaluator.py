from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.orchestration.fingerprint import stable_content_hash
from benchmarks.evaluators.judge_provider import (
    JudgeChatClient,
    JudgeProviderConfig,
    build_live_judge_client,
    judge_provider_config_from_env,
    sanitized_provider_metadata,
)
from benchmarks.evaluators.rule_evaluator import evaluate_rule_layer
from benchmarks.materials_agent_bench import MaterialsAgentBenchCase, MaterialsAgentBenchResult


JudgeDimension = Literal[
    "factuality",
    "logical_consistency",
    "citation_quality",
    "physical_validity",
    "actionable_clarity",
]
JUDGE_DIMENSIONS: tuple[JudgeDimension, ...] = (
    "factuality",
    "logical_consistency",
    "citation_quality",
    "physical_validity",
    "actionable_clarity",
)


class JudgeScores(BaseModel):
    factuality: int = 3
    logical_consistency: int = 3
    citation_quality: int = 3
    physical_validity: int = 3
    actionable_clarity: int = 3

    @field_validator(*JUDGE_DIMENSIONS)
    @classmethod
    def _score_range(cls, value: int) -> int:
        value = int(value)
        if value < 1 or value > 5:
            raise ValueError("judge dimension scores must be integers from 1 to 5")
        return value

    def average(self) -> float:
        return round(sum(getattr(self, dimension) for dimension in JUDGE_DIMENSIONS) / len(JUDGE_DIMENSIONS), 4)


class JudgeReport(BaseModel):
    schema_version: str = "materials-judge-report/v1"
    judge_version: str = "offline-contract-judge/v1"
    scores: JudgeScores
    overall_score: float
    passed: bool
    hard_gate_passed: bool
    blind_input_hash: str
    cache_key: str
    parse_mode: str = "deterministic_fallback"
    issues: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JudgeCalibrationResult(BaseModel):
    schema_version: str = "materials-judge-calibration/v1"
    case_id: str
    report: JudgeReport
    human_scores: JudgeScores
    exact_dimension_matches: int
    within_one_matches: int
    dimension_count: int = len(JUDGE_DIMENSIONS)
    agreement: float
    within_one_agreement: float
    hard_gate_override: bool = False
    parse_recovered: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class JudgeDriftReport(BaseModel):
    schema_version: str = "materials-judge-drift/v1"
    case_count: int
    dimension_count: int
    exact_agreement_rate: float
    within_one_agreement_rate: float
    mean_absolute_error: float
    per_dimension_mae: dict[str, float]
    parse_recovery_rate: float
    hard_gate_override_rate: float
    drift_detected: bool
    issues: list[str] = Field(default_factory=list)
    calibration_signature: str
    thresholds: dict[str, float]


class JudgeBackendCapability(BaseModel):
    provider: str
    backend: str
    configured: bool
    requires_api_key: bool
    api_key_env: str | None = None
    supports_chat_judge: bool = False
    supports_embedding: bool = False
    supports_reranker: bool = False
    supports_blind_input: bool = True
    supports_parse_fallback: bool = False
    hard_gate_non_override: bool = True
    allowed_in_quick_ci: bool = False
    notes: list[str] = Field(default_factory=list)


class JudgeBackendMatrix(BaseModel):
    schema_version: str = "materials-judge-backend-matrix/v1"
    backends: list[JudgeBackendCapability]
    configured_live_backend_count: int
    quick_ci_backend: str = "offline_contract"
    live_backend_names: list[str]
    missing_required_env: list[str]


def build_blind_judge_input(case: MaterialsAgentBenchCase, observation: dict[str, Any], rule_result: MaterialsAgentBenchResult) -> dict[str, Any]:
    """Build judge input without source ids, split, dataset name or human labels."""

    return {
        "schema_version": "materials-judge-blind-input/v1",
        "domain": case.domain,
        "difficulty": case.difficulty,
        "prompt": case.prompt,
        "expected_route": case.expected_route,
        "expected_compute_domain": case.expected_compute_domain,
        "locked_constraints": case.locked_constraints,
        "required_tool_chain": case.required_tool_chain,
        "required_artifacts": case.required_artifacts,
        "required_evidence": case.required_evidence,
        "forbidden_claims": case.forbidden_claims,
        "observation": {
            "route_name": observation.get("route_name"),
            "compute_domain": observation.get("compute_domain"),
            "locked_constraints": observation.get("locked_constraints") or observation.get("request") or {},
            "completed_tools": observation.get("completed_tools") or observation.get("tool_chain") or [],
            "artifacts": observation.get("artifacts") or [],
            "provenance": observation.get("provenance") or {},
            "physical_gate": observation.get("physical_gate") or {},
            "execution": observation.get("execution") or {},
            "final_conclusion": observation.get("final_conclusion"),
            "final_response": observation.get("final_response") or "",
            "claims": observation.get("claims") or [],
            "citations": observation.get("citations") or [],
            "required_hops": observation.get("required_hops") or [],
        },
        "hard_gate_summary": {
            "hard_gate_passed": rule_result.hard_gate_passed,
            "critical_failures": rule_result.critical_failures,
            "metric_failures": [
                name
                for name, metric in rule_result.metrics.items()
                if metric.passed is False
            ],
        },
    }


def deterministic_judge_report(case: MaterialsAgentBenchCase, observation: dict[str, Any]) -> JudgeReport:
    rule_result = evaluate_rule_layer(case, observation)
    blind_input = build_blind_judge_input(case, observation, rule_result)
    blind_input_hash = stable_content_hash(blind_input)
    scores = _scores_from_rule_result(rule_result, observation)
    issues = list(rule_result.critical_failures)
    for name, metric in rule_result.metrics.items():
        if metric.passed is False:
            issues.append(f"metric_failed:{name}")
    overall = scores.average()
    hard_gate_passed = rule_result.hard_gate_passed
    return JudgeReport(
        scores=scores,
        overall_score=overall,
        passed=hard_gate_passed and overall >= 4.0,
        hard_gate_passed=hard_gate_passed,
        blind_input_hash=blind_input_hash,
        cache_key=stable_content_hash({"judge_version": "offline-contract-judge/v1", "blind_input_hash": blind_input_hash}),
        issues=issues,
        metadata={
            "backend": "deterministic_contract_fallback",
            "rule_result": rule_result.to_dict(),
            "blind_input": blind_input,
        },
    )


def parse_judge_payload(raw_payload: str, *, fallback_report: JudgeReport) -> JudgeReport:
    payload = _load_json_like(raw_payload)
    if payload is None:
        return fallback_report.model_copy(update={"parse_mode": "deterministic_fallback"})
    try:
        scores = JudgeScores.model_validate(payload.get("scores") or payload)
        overall = float(payload.get("overall_score") or scores.average())
        provider_hard_gate_passed = bool(payload.get("hard_gate_passed", fallback_report.hard_gate_passed))
        hard_gate_passed = fallback_report.hard_gate_passed and provider_hard_gate_passed
        passed = bool(payload.get("passed", hard_gate_passed and overall >= 4.0)) and hard_gate_passed
        issues = [str(item) for item in payload.get("issues", fallback_report.issues)]
        if not fallback_report.hard_gate_passed and provider_hard_gate_passed:
            issues.append("provider_attempted_hard_gate_override")
        return fallback_report.model_copy(
            update={
                "scores": scores,
                "overall_score": round(overall, 4),
                "passed": passed,
                "hard_gate_passed": hard_gate_passed,
                "parse_mode": "strict" if raw_payload.strip().startswith("{") else "normalized",
                "issues": issues,
                "metadata": {
                    **fallback_report.metadata,
                    "raw_payload_hash": stable_content_hash(raw_payload),
                    "provider_hard_gate_passed": provider_hard_gate_passed,
                    "deterministic_hard_gate_passed": fallback_report.hard_gate_passed,
                },
            }
        )
    except Exception:  # noqa: BLE001 - malformed judge output must fall back safely.
        return fallback_report.model_copy(update={"parse_mode": "deterministic_fallback"})


def build_judge_prompts(blind_input: dict[str, Any]) -> tuple[str, str]:
    system_prompt = (
        "You are a blinded evaluator for a materials-science agent. "
        "Return JSON only. Score exactly five dimensions from 1 to 5: "
        "factuality, logical_consistency, citation_quality, physical_validity, actionable_clarity. "
        "Do not infer hidden labels or source dataset IDs. The hard_gate_summary is deterministic: "
        "you may not turn a failed hard gate into a pass."
    )
    user_prompt = (
        "Evaluate this blinded MaterialsAgentBench observation. Return JSON with keys: "
        "scores, overall_score, passed, hard_gate_passed, issues.\n\n"
        f"{json.dumps(blind_input, ensure_ascii=False, sort_keys=True)}"
    )
    return system_prompt, user_prompt


def evaluate_judge_with_provider(
    case: MaterialsAgentBenchCase,
    observation: dict[str, Any],
    *,
    provider_config: JudgeProviderConfig | None = None,
    client: JudgeChatClient | None = None,
    require_live: bool = False,
) -> JudgeReport:
    rule_result = evaluate_rule_layer(case, observation)
    blind_input = build_blind_judge_input(case, observation, rule_result)
    fallback_report = deterministic_judge_report(case, observation)
    config = provider_config or judge_provider_config_from_env()
    metadata = {
        **fallback_report.metadata,
        "provider": sanitized_provider_metadata(config),
        "blind_input": blind_input,
    }
    if config.provider in {"offline_contract", "mock", "local"}:
        return fallback_report.model_copy(update={"metadata": metadata})

    live_client = client or build_live_judge_client(config)
    if live_client is None:
        if require_live:
            raise RuntimeError(f"Judge provider is not configured or enabled: {config.provider}")
        return fallback_report.model_copy(
            update={
                "metadata": {
                    **metadata,
                    "provider_status": "not_configured_or_disabled",
                }
            }
        )

    system_prompt, user_prompt = build_judge_prompts(blind_input)
    raw_payload = live_client.chat_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
    )
    parsed = parse_judge_payload(raw_payload, fallback_report=fallback_report)
    return parsed.model_copy(
        update={
            "judge_version": f"{config.provider}:{config.model}",
            "cache_key": stable_content_hash(
                {
                    "provider": config.provider,
                    "model": config.model,
                    "blind_input_hash": fallback_report.blind_input_hash,
                    "raw_payload_hash": stable_content_hash(raw_payload),
                }
            ),
            "metadata": {
                **parsed.metadata,
                **metadata,
                "provider_status": "called",
                "raw_payload_hash": stable_content_hash(raw_payload),
            },
        }
    )


def evaluate_judge_calibration_case(
    case: MaterialsAgentBenchCase,
    row: dict[str, Any],
    *,
    report: JudgeReport | None = None,
) -> JudgeCalibrationResult:
    observation = dict(row.get("observation") or {})
    if report is None:
        fallback_report = deterministic_judge_report(case, observation)
        raw_payload = str(row.get("raw_judge_payload") or "")
        report = parse_judge_payload(raw_payload, fallback_report=fallback_report) if raw_payload else fallback_report
    human_scores = JudgeScores.model_validate(row.get("human_scores") or {})
    exact = 0
    within_one = 0
    for dimension in JUDGE_DIMENSIONS:
        judge_value = getattr(report.scores, dimension)
        human_value = getattr(human_scores, dimension)
        if judge_value == human_value:
            exact += 1
        if abs(judge_value - human_value) <= 1:
            within_one += 1
    hard_gate_override = report.passed and not report.hard_gate_passed
    return JudgeCalibrationResult(
        case_id=case.source_case_id,
        report=report,
        human_scores=human_scores,
        exact_dimension_matches=exact,
        within_one_matches=within_one,
        agreement=round(exact / len(JUDGE_DIMENSIONS), 4),
        within_one_agreement=round(within_one / len(JUDGE_DIMENSIONS), 4),
        hard_gate_override=hard_gate_override,
        parse_recovered=report.parse_mode in {"strict", "normalized", "deterministic_fallback"},
        metadata={"judge_dimensions": list(JUDGE_DIMENSIONS)},
    )


def build_judge_drift_report(
    calibrations: list[JudgeCalibrationResult],
    *,
    thresholds: dict[str, float] | None = None,
) -> JudgeDriftReport:
    resolved_thresholds = {
        "min_within_one_agreement_rate": 0.80,
        "max_mean_absolute_error": 0.80,
        "min_parse_recovery_rate": 1.00,
        "max_hard_gate_override_rate": 0.00,
        **(thresholds or {}),
    }
    if not calibrations:
        return JudgeDriftReport(
            case_count=0,
            dimension_count=len(JUDGE_DIMENSIONS),
            exact_agreement_rate=0.0,
            within_one_agreement_rate=0.0,
            mean_absolute_error=5.0,
            per_dimension_mae={dimension: 5.0 for dimension in JUDGE_DIMENSIONS},
            parse_recovery_rate=0.0,
            hard_gate_override_rate=1.0,
            drift_detected=True,
            issues=["no_calibration_cases"],
            calibration_signature=stable_content_hash({"calibrations": []}),
            thresholds=resolved_thresholds,
        )

    absolute_errors: list[int] = []
    per_dimension_errors: dict[str, list[int]] = {dimension: [] for dimension in JUDGE_DIMENSIONS}
    exact_matches = within_one_matches = total_dimensions = 0
    parse_recovered = hard_gate_overrides = 0
    signature_rows: list[dict[str, Any]] = []
    for calibration in calibrations:
        total_dimensions += calibration.dimension_count
        exact_matches += calibration.exact_dimension_matches
        within_one_matches += calibration.within_one_matches
        if calibration.parse_recovered:
            parse_recovered += 1
        if calibration.hard_gate_override:
            hard_gate_overrides += 1
        row_signature = {"case_id": calibration.case_id, "judge": {}, "human": {}}
        for dimension in JUDGE_DIMENSIONS:
            judge_value = getattr(calibration.report.scores, dimension)
            human_value = getattr(calibration.human_scores, dimension)
            error = abs(judge_value - human_value)
            absolute_errors.append(error)
            per_dimension_errors[dimension].append(error)
            row_signature["judge"][dimension] = judge_value
            row_signature["human"][dimension] = human_value
        signature_rows.append(row_signature)

    case_count = len(calibrations)
    exact_rate = round(exact_matches / total_dimensions, 4) if total_dimensions else 0.0
    within_one_rate = round(within_one_matches / total_dimensions, 4) if total_dimensions else 0.0
    mae = round(sum(absolute_errors) / len(absolute_errors), 4) if absolute_errors else 5.0
    per_dimension_mae = {
        dimension: round(sum(errors) / len(errors), 4) if errors else 5.0
        for dimension, errors in per_dimension_errors.items()
    }
    parse_rate = round(parse_recovered / case_count, 4)
    hard_gate_override_rate = round(hard_gate_overrides / case_count, 4)
    issues: list[str] = []
    if within_one_rate < resolved_thresholds["min_within_one_agreement_rate"]:
        issues.append("within_one_agreement_below_threshold")
    if mae > resolved_thresholds["max_mean_absolute_error"]:
        issues.append("mean_absolute_error_above_threshold")
    if parse_rate < resolved_thresholds["min_parse_recovery_rate"]:
        issues.append("parse_recovery_below_threshold")
    if hard_gate_override_rate > resolved_thresholds["max_hard_gate_override_rate"]:
        issues.append("hard_gate_override_detected")
    return JudgeDriftReport(
        case_count=case_count,
        dimension_count=len(JUDGE_DIMENSIONS),
        exact_agreement_rate=exact_rate,
        within_one_agreement_rate=within_one_rate,
        mean_absolute_error=mae,
        per_dimension_mae=per_dimension_mae,
        parse_recovery_rate=parse_rate,
        hard_gate_override_rate=hard_gate_override_rate,
        drift_detected=bool(issues),
        issues=issues,
        calibration_signature=stable_content_hash({"judge_drift_calibrations": signature_rows}),
        thresholds=resolved_thresholds,
    )


def build_judge_backend_matrix(env: Mapping[str, str] | None = None) -> JudgeBackendMatrix:
    env = env or {}

    def has_key(name: str) -> bool:
        return bool(str(env.get(name) or "").strip())

    backends = [
        JudgeBackendCapability(
            provider="local",
            backend="offline_contract",
            configured=True,
            requires_api_key=False,
            supports_chat_judge=True,
            supports_blind_input=True,
            supports_parse_fallback=True,
            hard_gate_non_override=True,
            allowed_in_quick_ci=True,
            notes=["deterministic fallback used for quick CI and schema safety"],
        ),
        JudgeBackendCapability(
            provider="local",
            backend="mock",
            configured=True,
            requires_api_key=False,
            supports_chat_judge=True,
            supports_embedding=True,
            supports_reranker=False,
            supports_blind_input=True,
            supports_parse_fallback=True,
            hard_gate_non_override=True,
            allowed_in_quick_ci=True,
            notes=["test-only backend; never treated as scientific evidence"],
        ),
        JudgeBackendCapability(
            provider="openrouter",
            backend="openrouter",
            configured=has_key("OPENROUTER_API_KEY"),
            requires_api_key=True,
            api_key_env="OPENROUTER_API_KEY",
            supports_chat_judge=True,
            supports_embedding=True,
            supports_reranker=False,
            supports_blind_input=True,
            supports_parse_fallback=True,
            hard_gate_non_override=True,
            allowed_in_quick_ci=False,
            notes=["manual live gate; key presence is reported without exposing the key"],
        ),
        JudgeBackendCapability(
            provider="dashscope",
            backend="dashscope",
            configured=has_key("DASHSCOPE_API_KEY"),
            requires_api_key=True,
            api_key_env="DASHSCOPE_API_KEY",
            supports_chat_judge=True,
            supports_embedding=True,
            supports_reranker=True,
            supports_blind_input=True,
            supports_parse_fallback=True,
            hard_gate_non_override=True,
            allowed_in_quick_ci=False,
            notes=["manual live gate for Qwen/DashScope embeddings, rerankers and judge models"],
        ),
    ]
    live_backend_names = [backend.backend for backend in backends if backend.requires_api_key]
    missing_required_env = [
        str(backend.api_key_env)
        for backend in backends
        if backend.requires_api_key and not backend.configured and backend.api_key_env
    ]
    return JudgeBackendMatrix(
        backends=backends,
        configured_live_backend_count=sum(1 for backend in backends if backend.requires_api_key and backend.configured),
        live_backend_names=live_backend_names,
        missing_required_env=missing_required_env,
    )


def _scores_from_rule_result(rule_result: MaterialsAgentBenchResult, observation: dict[str, Any]) -> JudgeScores:
    metrics = rule_result.metrics

    def metric_value(name: str, default: float = 1.0) -> float:
        metric = metrics.get(name)
        if metric is None or metric.value is None:
            return default
        return float(metric.value)

    critical = bool(rule_result.critical_failures)
    factuality = _rate_to_score(metric_value("factual_accuracy"), hard_fail=critical)
    citation_quality = min(_rate_to_score(metric_value("citation_coverage")), _rate_to_score(metric_value("citation_precision")))
    logic_values = [
        metric_value("locked_constraint_accuracy"),
        metric_value("tool_chain_completion"),
        metric_value("artifact_completeness"),
    ]
    logical_consistency = _rate_to_score(sum(logic_values) / len(logic_values), hard_fail=critical)
    provenance_ok = metric_value("real_mock_provenance_accuracy")
    execution = observation.get("execution") if isinstance(observation.get("execution"), dict) else {}
    physical_gate = observation.get("physical_gate") if isinstance(observation.get("physical_gate"), dict) else {}
    physical_rate = provenance_ok
    if execution.get("success") is False or physical_gate.get("passed") is False:
        physical_rate = min(physical_rate, 0.4)
    physical_validity = _rate_to_score(physical_rate, hard_fail=critical)
    clarity = 4
    final_response = str(observation.get("final_response") or "")
    if len(final_response.strip()) >= 40 and not critical:
        clarity = 5
    elif critical:
        clarity = 3
    return JudgeScores(
        factuality=factuality,
        logical_consistency=logical_consistency,
        citation_quality=citation_quality,
        physical_validity=physical_validity,
        actionable_clarity=clarity,
    )


def _rate_to_score(value: float, *, hard_fail: bool = False) -> int:
    if hard_fail:
        return 2 if value >= 0.8 else 1
    if value >= 0.98:
        return 5
    if value >= 0.8:
        return 4
    if value >= 0.6:
        return 3
    if value >= 0.3:
        return 2
    return 1


def _load_json_like(raw_payload: str) -> dict[str, Any] | None:
    raw = (raw_payload or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1))
    balanced = _first_balanced_object(raw)
    if balanced:
        candidates.append(balanced)
    for candidate in candidates:
        normalized = re.sub(r",\s*([}\]])", r"\1", candidate.strip())
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_balanced_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""
