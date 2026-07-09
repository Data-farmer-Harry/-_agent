from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from benchmarks.statistics.effect_size import cohens_dz
from benchmarks.statistics.paired_tests import mcnemar_exact, paired_risk_difference


DEFAULT_BOOTSTRAP_RESAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 20260706
DEFAULT_MIN_DOMAIN_CI_CASES = 30


@dataclass(frozen=True)
class PairedBootstrapResult:
    n: int
    old_mean: float | None
    new_mean: float | None
    delta: float | None
    relative_delta: float | None
    ci_low: float | None
    ci_high: float | None
    confidence: float
    n_resamples: int
    seed: int
    status: str = "ok"
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "old_mean": self.old_mean,
            "new_mean": self.new_mean,
            "delta": self.delta,
            "relative_delta": self.relative_delta,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "confidence": self.confidence,
            "n_resamples": self.n_resamples,
            "seed": self.seed,
            "status": self.status,
            "reason": self.reason,
        }


def paired_bootstrap_ci(
    old_values: list[float | int],
    new_values: list[float | int],
    *,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence: float = 0.95,
) -> PairedBootstrapResult:
    _validate_paired_values(old_values, new_values)
    n = len(old_values)
    if n == 0:
        return PairedBootstrapResult(
            n=0,
            old_mean=None,
            new_mean=None,
            delta=None,
            relative_delta=None,
            ci_low=None,
            ci_high=None,
            confidence=confidence,
            n_resamples=n_resamples,
            seed=seed,
            status="not_applicable",
            reason="no paired cases",
        )
    old_float = [float(value) for value in old_values]
    new_float = [float(value) for value in new_values]
    old_mean = _mean(old_float)
    new_mean = _mean(new_float)
    delta = new_mean - old_mean
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(n_resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        sampled_old = _mean(old_float[index] for index in indices)
        sampled_new = _mean(new_float[index] for index in indices)
        deltas.append(sampled_new - sampled_old)
    alpha = 1 - confidence
    sorted_deltas = sorted(deltas)
    return PairedBootstrapResult(
        n=n,
        old_mean=old_mean,
        new_mean=new_mean,
        delta=delta,
        relative_delta=(delta / old_mean) if old_mean else None,
        ci_low=_percentile(sorted_deltas, alpha / 2 * 100),
        ci_high=_percentile(sorted_deltas, (1 - alpha / 2) * 100),
        confidence=confidence,
        n_resamples=n_resamples,
        seed=seed,
    )


def paired_statistics_report(
    old_by_case: dict[str, float | int | bool],
    new_by_case: dict[str, float | int | bool],
    *,
    metric_name: str,
    data_type: str,
    domain_by_case: dict[str, str] | None = None,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    min_domain_ci_cases: int = DEFAULT_MIN_DOMAIN_CI_CASES,
) -> dict[str, Any]:
    paired_case_ids = sorted(set(old_by_case) & set(new_by_case))
    old_values = [_numeric_value(old_by_case[case_id]) for case_id in paired_case_ids]
    new_values = [_numeric_value(new_by_case[case_id]) for case_id in paired_case_ids]
    report: dict[str, Any] = {
        "statistics_version": "materials-statistics/v1",
        "metric_name": metric_name,
        "data_type": data_type,
        "paired_case_count": len(paired_case_ids),
        "missing_old_case_ids": sorted(set(new_by_case) - set(old_by_case)),
        "missing_new_case_ids": sorted(set(old_by_case) - set(new_by_case)),
        "bootstrap": paired_bootstrap_ci(old_values, new_values, n_resamples=n_resamples, seed=seed).to_dict(),
        "seed": seed,
        "n_resamples": n_resamples,
    }
    if data_type == "continuous":
        report["effect_size"] = cohens_dz(old_values, new_values, data_type="continuous")
    elif data_type == "binary":
        report["paired_binary"] = {
            "risk_difference": paired_risk_difference([int(value) for value in old_values], [int(value) for value in new_values]),
            "mcnemar": mcnemar_exact([int(value) for value in old_values], [int(value) for value in new_values]),
        }
        report["effect_size"] = {
            "status": "not_applicable",
            "reason": "Cohen's d is not valid for binary pass/fail metrics",
        }
    elif data_type in {"rate", "proportion", "hit@k", "mrr", "ndcg", "latency", "cost", "token"}:
        report["effect_size"] = {
            "status": "not_applicable",
            "reason": f"no default effect size for data_type={data_type}",
        }
    else:
        raise ValueError(f"unsupported data_type: {data_type}")
    if domain_by_case:
        report["domains"] = _domain_reports(
            paired_case_ids,
            old_values,
            new_values,
            domain_by_case,
            n_resamples=n_resamples,
            seed=seed,
            min_domain_ci_cases=min_domain_ci_cases,
        )
    return report


def _domain_reports(
    paired_case_ids: list[str],
    old_values: list[float],
    new_values: list[float],
    domain_by_case: dict[str, str],
    *,
    n_resamples: int,
    seed: int,
    min_domain_ci_cases: int,
) -> dict[str, Any]:
    by_domain: dict[str, list[int]] = {}
    for index, case_id in enumerate(paired_case_ids):
        domain = domain_by_case.get(case_id, "unknown")
        by_domain.setdefault(domain, []).append(index)
    reports: dict[str, Any] = {}
    for offset, (domain, indices) in enumerate(sorted(by_domain.items())):
        domain_old = [old_values[index] for index in indices]
        domain_new = [new_values[index] for index in indices]
        if len(indices) < min_domain_ci_cases:
            old_mean = _mean(domain_old) if domain_old else None
            new_mean = _mean(domain_new) if domain_new else None
            delta = (new_mean - old_mean) if old_mean is not None and new_mean is not None else None
            reports[domain] = {
                "case_count": len(indices),
                "bootstrap": PairedBootstrapResult(
                    n=len(indices),
                    old_mean=old_mean,
                    new_mean=new_mean,
                    delta=delta,
                    relative_delta=(delta / old_mean) if delta is not None and old_mean else None,
                    ci_low=None,
                    ci_high=None,
                    confidence=0.95,
                    n_resamples=n_resamples,
                    seed=seed + offset + 1,
                    status="not_applicable",
                    reason=f"domain has fewer than {min_domain_ci_cases} paired cases",
                ).to_dict(),
            }
            continue
        reports[domain] = {
            "case_count": len(indices),
            "bootstrap": paired_bootstrap_ci(domain_old, domain_new, n_resamples=n_resamples, seed=seed + offset + 1).to_dict(),
        }
    return reports


def _validate_paired_values(old_values: list[float | int], new_values: list[float | int]) -> None:
    if len(old_values) != len(new_values):
        raise ValueError("paired bootstrap values must have the same length")
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in [*old_values, *new_values]):
        raise TypeError("paired bootstrap values must be numeric and not bool")


def _numeric_value(value: float | int | bool) -> float:
    if value is True:
        return 1.0
    if value is False:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"expected numeric or bool value, got {value!r}")


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _mean(values: Any) -> float:
    sequence = list(values)
    if not sequence:
        raise ValueError("mean requires at least one value")
    return sum(float(value) for value in sequence) / len(sequence)
