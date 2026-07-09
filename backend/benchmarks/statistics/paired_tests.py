from __future__ import annotations

import math
from typing import Any


def paired_risk_difference(old_values: list[bool | int], new_values: list[bool | int]) -> dict[str, Any]:
    old_binary = _as_binary(old_values)
    new_binary = _as_binary(new_values)
    if len(old_binary) != len(new_binary):
        raise ValueError("paired binary values must have the same length")
    n = len(old_binary)
    old_rate = sum(old_binary) / n if n else None
    new_rate = sum(new_binary) / n if n else None
    return {
        "n": n,
        "old_rate": old_rate,
        "new_rate": new_rate,
        "risk_difference": (new_rate - old_rate) if old_rate is not None and new_rate is not None else None,
        "improvements": sum(1 for old, new in zip(old_binary, new_binary, strict=True) if old == 0 and new == 1),
        "regressions": sum(1 for old, new in zip(old_binary, new_binary, strict=True) if old == 1 and new == 0),
    }


def mcnemar_exact(old_values: list[bool | int], new_values: list[bool | int]) -> dict[str, Any]:
    old_binary = _as_binary(old_values)
    new_binary = _as_binary(new_values)
    if len(old_binary) != len(new_binary):
        raise ValueError("paired binary values must have the same length")
    improvements = sum(1 for old, new in zip(old_binary, new_binary, strict=True) if old == 0 and new == 1)
    regressions = sum(1 for old, new in zip(old_binary, new_binary, strict=True) if old == 1 and new == 0)
    discordant = improvements + regressions
    p_value = 1.0 if discordant == 0 else min(1.0, 2.0 * _binomial_cdf(min(improvements, regressions), discordant, 0.5))
    return {
        "n": len(old_binary),
        "improvements": improvements,
        "regressions": regressions,
        "discordant": discordant,
        "exact_p_value": p_value,
        "method": "exact_binomial_mcnemar",
    }


def paired_binary_report(old_values: list[bool | int], new_values: list[bool | int]) -> dict[str, Any]:
    return {
        "risk_difference": paired_risk_difference(old_values, new_values),
        "mcnemar": mcnemar_exact(old_values, new_values),
    }


def _as_binary(values: list[bool | int]) -> list[int]:
    binary: list[int] = []
    for value in values:
        if value is True:
            binary.append(1)
        elif value is False:
            binary.append(0)
        elif value in {0, 1}:
            binary.append(int(value))
        else:
            raise ValueError(f"expected binary value, got {value!r}")
    return binary


def _binomial_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * (p**i) * ((1 - p) ** (n - i)) for i in range(k + 1))
