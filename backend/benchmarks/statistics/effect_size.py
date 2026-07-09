from __future__ import annotations

import math
from typing import Any


def cohens_dz(old_values: list[float | int], new_values: list[float | int], *, data_type: str = "continuous") -> dict[str, Any]:
    _validate_paired_numeric(old_values, new_values)
    if data_type != "continuous":
        raise ValueError("Cohen's dz is only valid for continuous paired metrics")
    if _looks_binary(old_values) and _looks_binary(new_values):
        raise ValueError("Cohen's dz must not be used for binary pass/fail metrics; use paired risk difference or McNemar")
    deltas = [float(new) - float(old) for old, new in zip(old_values, new_values, strict=True)]
    if not deltas:
        return {"effect_size": None, "mean_delta": None, "std_delta": None, "n": 0, "status": "not_applicable"}
    mean_delta = _mean(deltas)
    std_delta = _sample_std(deltas)
    if std_delta == 0:
        effect_size = 0.0 if mean_delta == 0 else math.copysign(float("inf"), mean_delta)
    else:
        effect_size = mean_delta / std_delta
    return {
        "effect_size": effect_size,
        "mean_delta": mean_delta,
        "std_delta": std_delta,
        "n": len(deltas),
        "status": "ok",
    }


def summarize_distribution(values: list[float | int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p90": None, "p95": None, "min": None, "max": None}
    sorted_values = sorted(float(value) for value in values)
    return {
        "n": len(sorted_values),
        "mean": _mean(sorted_values),
        "median": _percentile(sorted_values, 50),
        "p90": _percentile(sorted_values, 90),
        "p95": _percentile(sorted_values, 95),
        "min": sorted_values[0],
        "max": sorted_values[-1],
    }


def _validate_paired_numeric(old_values: list[float | int], new_values: list[float | int]) -> None:
    if len(old_values) != len(new_values):
        raise ValueError("paired values must have the same length")
    for value in [*old_values, *new_values]:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("paired values must be numeric and not bool")


def _looks_binary(values: list[float | int]) -> bool:
    return bool(values) and all(value in {0, 1, 0.0, 1.0} for value in values)


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile / 100
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _mean(values: list[float]) -> float:
    if not values:
        raise ValueError("mean requires at least one value")
    return sum(float(value) for value in values) / len(values)
