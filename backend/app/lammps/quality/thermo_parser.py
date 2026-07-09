from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from app.lammps.quality.models import ThermoRow


THERMO_FIELDNAMES = ["step", "temp", "pe", "ke", "etotal", "press"]


class ThermoParseError(RuntimeError):
    """Raised when a real LAMMPS run finishes but no usable thermo rows are found."""


def parse_lammps_thermo_stdout(stdout: str) -> list[ThermoRow]:
    rows: list[ThermoRow] = []
    for line in stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 6 or not _looks_like_step(parts[0]):
            continue
        try:
            rows.append(
                ThermoRow(
                    step=float(parts[0]),
                    temp=float(parts[1]),
                    pe=float(parts[2]),
                    ke=float(parts[3]),
                    etotal=float(parts[4]),
                    press=float(parts[5]),
                )
            )
        except ValueError:
            continue
    return rows


def parse_real_thermo_to_csv(stdout: str, thermo_path: Path) -> dict[str, Any]:
    rows = parse_lammps_thermo_stdout(stdout)
    if not rows:
        raise ThermoParseError("thermo_parse_failed: no numeric thermo rows found in real LAMMPS stdout.")
    write_thermo_csv(rows, thermo_path)
    return summarize_thermo_rows(rows, synthetic=False)


def seed_thermo_rows(temperature: int, steps: int) -> list[ThermoRow]:
    interval = max(steps // 10, 100)
    rows = []
    for idx, step in enumerate(range(0, steps + interval, interval)):
        temp = min(temperature, 300 + idx * (temperature - 300) / 10)
        pe = -3.36 + idx * 0.01
        ke = 0.025 * temp
        etotal = pe + ke
        press = 100 + idx * 4
        rows.append(
            ThermoRow(
                step=float(step),
                temp=float(temp),
                pe=float(pe),
                ke=float(ke),
                etotal=float(etotal),
                press=float(press),
            )
        )
    return rows


def write_thermo_csv(rows: list[ThermoRow], thermo_path: Path) -> None:
    with thermo_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=THERMO_FIELDNAMES)
        writer.writeheader()
        writer.writerows(row.model_dump(mode="json") for row in rows)


def read_thermo_csv(thermo_path: Path) -> list[ThermoRow]:
    if not thermo_path.exists():
        return []
    rows: list[ThermoRow] = []
    with thermo_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                rows.append(
                    ThermoRow(
                        step=float(row["step"]),
                        temp=float(row["temp"]),
                        pe=float(row["pe"]),
                        ke=float(row["ke"]),
                        etotal=float(row["etotal"]),
                        press=float(row["press"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def summarize_thermo_rows(rows: list[ThermoRow], *, synthetic: bool) -> dict[str, Any]:
    if not rows:
        return {
            "final_temp": None,
            "final_pe": None,
            "final_etotal": None,
            "max_press": None,
            "thermo_rows": 0,
            "max_step": 0.0,
            "synthetic_thermo": synthetic,
        }
    last = rows[-1]
    finite_press = [row.press for row in rows if math.isfinite(row.press)]
    return {
        "final_temp": _round_or_none(last.temp),
        "final_pe": _round_or_none(last.pe),
        "final_etotal": _round_or_none(last.etotal),
        "max_press": _round_or_none(max(finite_press)) if finite_press else None,
        "thermo_rows": len(rows),
        "max_step": _round_or_none(max(row.step for row in rows)),
        "synthetic_thermo": synthetic,
    }


def _looks_like_step(value: str) -> bool:
    try:
        number = float(value)
    except ValueError:
        return False
    return number >= 0 and number.is_integer()


def _round_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return value
    return round(value, 3)
