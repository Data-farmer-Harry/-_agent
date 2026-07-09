from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import json
import re
from typing import Any


_MATERIAL_ALIASES = {
    "aluminium": "Al",
    "aluminum": "Al",
    "铝": "Al",
    "copper": "Cu",
    "铜": "Cu",
    "iron": "Fe",
    "铁": "Fe",
    "nickel": "Ni",
    "镍": "Ni",
    "titanium": "Ti",
    "钛": "Ti",
    "chromium": "Cr",
    "铬": "Cr",
    "magnesium": "Mg",
    "镁": "Mg",
    "zinc": "Zn",
    "锌": "Zn",
    "lead": "Pb",
    "铅": "Pb",
    "tin": "Sn",
    "锡": "Sn",
}


@dataclass(frozen=True)
class UnitRule:
    canonical_unit: str
    factor: float = 1.0
    offset: float = 0.0

    def convert(self, value: float) -> float:
        return value * self.factor + self.offset


_UNIT_ALIASES = {
    # Temperature: canonical Kelvin.
    "k": UnitRule("K"),
    "kelvin": UnitRule("K"),
    "kelvins": UnitRule("K"),
    "c": UnitRule("K", offset=273.15),
    "°c": UnitRule("K", offset=273.15),
    "℃": UnitRule("K", offset=273.15),
    "celsius": UnitRule("K", offset=273.15),
    "f": UnitRule("K", factor=5.0 / 9.0, offset=273.15 - 32.0 * 5.0 / 9.0),
    "°f": UnitRule("K", factor=5.0 / 9.0, offset=273.15 - 32.0 * 5.0 / 9.0),
    "fahrenheit": UnitRule("K", factor=5.0 / 9.0, offset=273.15 - 32.0 * 5.0 / 9.0),
    # Length: canonical Angstrom for atomistic inputs.
    "a": UnitRule("angstrom"),
    "å": UnitRule("angstrom"),
    "angstrom": UnitRule("angstrom"),
    "angstroms": UnitRule("angstrom"),
    "nm": UnitRule("angstrom", factor=10.0),
    "nanometer": UnitRule("angstrom", factor=10.0),
    "nanometers": UnitRule("angstrom", factor=10.0),
    # Time: canonical femtosecond.
    "fs": UnitRule("fs"),
    "femtosecond": UnitRule("fs"),
    "femtoseconds": UnitRule("fs"),
    "ps": UnitRule("fs", factor=1000.0),
    "picosecond": UnitRule("fs", factor=1000.0),
    "picoseconds": UnitRule("fs", factor=1000.0),
    "ns": UnitRule("fs", factor=1_000_000.0),
    "nanosecond": UnitRule("fs", factor=1_000_000.0),
    "nanoseconds": UnitRule("fs", factor=1_000_000.0),
    # Energy: canonical electronvolt.
    "ev": UnitRule("eV"),
    "electronvolt": UnitRule("eV"),
    "electronvolts": UnitRule("eV"),
    # Pressure: canonical Pa.
    "pa": UnitRule("Pa"),
    "kpa": UnitRule("Pa", factor=1_000.0),
    "mpa": UnitRule("Pa", factor=1_000_000.0),
    "gpa": UnitRule("Pa", factor=1_000_000_000.0),
    "bar": UnitRule("Pa", factor=100_000.0),
    "atm": UnitRule("Pa", factor=101_325.0),
}

_MEASUREMENT_RE = re.compile(
    r"^\s*(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<unit>[A-Za-z°℃Åå]+)?\s*$"
)


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def canonicalize_material_aliases(text: str) -> str:
    """Normalize common material names without changing unrelated words."""

    result = compact_whitespace(text)
    for alias, symbol in sorted(_MATERIAL_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(r"[\u4e00-\u9fff]", alias):
            result = result.replace(alias, symbol)
            continue
        result = re.sub(rf"\b{re.escape(alias)}\b", symbol, result, flags=re.IGNORECASE)
    return result


def canonicalize_key_text(text: str) -> str:
    return compact_whitespace(canonicalize_material_aliases(text)).lower()


def normalize_unit_token(unit: str) -> str:
    return compact_whitespace(unit).replace(" ", "").lower()


def canonicalize_scalar_measurement(value: Any, unit: str = "") -> tuple[str, str]:
    """Return a deterministic `(value, unit)` pair for hash/equality use.

    This intentionally only canonicalizes unambiguous scalar measurements. If a
    value is free text or a complex object, callers still receive a stable JSON
    representation without pretending that semantic equivalence was proven.
    """

    parsed_value: float | None = None
    parsed_unit = unit

    if isinstance(value, bool):
        return (str(value).lower(), compact_whitespace(unit))
    if isinstance(value, int | float):
        parsed_value = float(value)
    elif isinstance(value, str):
        match = _MEASUREMENT_RE.match(value)
        if match:
            parsed_value = float(match.group("value"))
            if not parsed_unit:
                parsed_unit = match.group("unit") or ""
        else:
            return (canonicalize_key_text(value), compact_whitespace(unit))
    else:
        return (json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True), compact_whitespace(unit))

    if parsed_value is None or not isfinite(parsed_value):
        return (str(value), compact_whitespace(unit))

    rule = _UNIT_ALIASES.get(normalize_unit_token(parsed_unit))
    if rule is None:
        return (_format_float(parsed_value), compact_whitespace(parsed_unit))
    return (_format_float(rule.convert(parsed_value)), rule.canonical_unit)


def canonicalize_memory_payload(*, subject: str, predicate: str, value: Any, unit: str) -> str:
    canonical_value, canonical_unit = canonicalize_scalar_measurement(value, unit)
    return "|".join(
        [
            canonicalize_key_text(subject),
            canonicalize_key_text(predicate),
            canonical_value,
            canonicalize_key_text(canonical_unit),
        ]
    )


def canonicalize_free_text(text: str) -> str:
    compacted = canonicalize_key_text(text)

    def replace_measurement(match: re.Match[str]) -> str:
        value, unit = canonicalize_scalar_measurement(match.group("value"), match.group("unit"))
        return f"{value} {unit}".strip().lower()

    return re.sub(
        r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<unit>°C|°F|℃|Å|å|kelvins?|celsius|fahrenheit|angstroms?|nanometers?|femtoseconds?|picoseconds?|nanoseconds?|electronvolts?|[A-Za-z]+)",
        replace_measurement,
        compacted,
        flags=re.IGNORECASE,
    )


def _format_float(value: float) -> str:
    rounded = round(value, 9)
    if abs(rounded) < 1e-12:
        rounded = 0.0
    text = f"{rounded:.9f}".rstrip("0").rstrip(".")
    return text or "0"
