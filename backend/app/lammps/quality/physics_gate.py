from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.lammps.quality.log_scanner import scan_lammps_log
from app.lammps.quality.models import PhysicalQualityReport, RunMode, ThermoRow
from app.lammps.quality.profiles import resolve_quality_thresholds
from app.lammps.quality.thermo_parser import read_thermo_csv
from app.utils.path_utils import read_json_file_if_exists, write_json_file


def build_physical_quality_report(
    *,
    output_dir: Path,
    request: dict[str, Any],
    run_mode: RunMode,
    metrics: dict[str, Any] | None = None,
    execution_error: str = "",
) -> PhysicalQualityReport:
    thresholds = resolve_quality_thresholds(request)
    rows = read_thermo_csv(output_dir / "thermo.csv")
    synthetic_thermo = bool((metrics or {}).get("synthetic_thermo") or _synthetic_metadata(output_dir))
    issues: list[str] = []
    warnings: list[str] = []

    if run_mode == "mock":
        synthetic_thermo = True
        warnings.append("Mock mode used synthetic thermo rows; this is not a scientific result.")
    elif synthetic_thermo:
        issues.append("Real run is marked with synthetic thermo; refusing scientific success.")

    if not rows:
        issues.append("thermo_parse_failed: no thermo rows were available.")

    thermo_stats = _thermo_stats(rows, request)
    if rows and thermo_stats["has_nan_or_inf"]:
        issues.append("Thermo contains NaN or Inf values.")
    if rows and thermo_stats["thermo_rows"] < int(thresholds["min_thermo_rows"]):
        issues.append(f"Thermo row count below threshold: {thermo_stats['thermo_rows']}.")
    if rows and thermo_stats["step_coverage"] < float(thresholds["min_step_coverage"]):
        issues.append(
            f"Step coverage {thermo_stats['step_coverage']:.3f} below threshold {float(thresholds['min_step_coverage']):.3f}."
        )
    if rows and thermo_stats["temperature_deviation"] is not None:
        requested_temp = _float_or_none(request.get("temperature"))
        denominator = max(abs(requested_temp or 0.0), 1.0)
        relative_deviation = abs(thermo_stats["temperature_deviation"]) / denominator
        if relative_deviation > float(thresholds["max_temperature_relative_deviation"]):
            issues.append(
                "Final temperature deviates too far from request: "
                f"{relative_deviation:.3f} > {float(thresholds['max_temperature_relative_deviation']):.3f}."
            )
    if rows and thermo_stats["normalized_energy_drift"] is not None:
        if thermo_stats["normalized_energy_drift"] > float(thresholds["max_normalized_energy_drift"]):
            issues.append(
                "Normalized energy drift exceeds threshold: "
                f"{thermo_stats['normalized_energy_drift']:.3f} > {float(thresholds['max_normalized_energy_drift']):.3f}."
            )
    if rows and thermo_stats["max_pressure"] is not None:
        if abs(thermo_stats["max_pressure"]) > float(thresholds["max_pressure_abs"]):
            issues.append(f"Pressure magnitude exceeds threshold: {thermo_stats['max_pressure']:.3g}.")
    if thermo_stats["pressure_outlier_fraction"] > float(thresholds["max_pressure_outlier_fraction"]):
        issues.append(
            "Pressure outlier fraction exceeds threshold: "
            f"{thermo_stats['pressure_outlier_fraction']:.3f}."
        )

    log_errors = scan_lammps_log(output_dir / "run.log")
    fatal_log_errors = [item for item in log_errors if "mock fallback enabled" not in item.lower()]
    if run_mode == "real" and fatal_log_errors:
        issues.extend(f"Log anomaly: {item}" for item in fatal_log_errors)
    elif fatal_log_errors:
        warnings.extend(f"Log anomaly in non-real run: {item}" for item in fatal_log_errors)

    dump_info = _dump_info(output_dir, str(request.get("dump_file") or "dump.atom"))
    if run_mode == "real" and not dump_info["dump_exists"]:
        warnings.append("LAMMPS dump file is missing; thermo was still checked.")
    if dump_info["dump_exists"] and not dump_info["atom_count_valid"]:
        issues.append("Dump exists but atom count could not be validated.")

    if execution_error:
        warnings.append(f"Execution error context: {execution_error}")

    passed = not issues
    return PhysicalQualityReport(
        run_mode=run_mode,
        passed=passed,
        scientific_result_passed=passed and run_mode == "real" and not synthetic_thermo,
        synthetic_thermo=synthetic_thermo,
        thermo_rows=int(thermo_stats["thermo_rows"]),
        max_step=float(thermo_stats["max_step"]),
        requested_steps=int(_float_or_none(request.get("steps")) or 0),
        step_coverage=float(thermo_stats["step_coverage"]),
        final_temperature=thermo_stats["final_temperature"],
        average_temperature=thermo_stats["average_temperature"],
        temperature_deviation=thermo_stats["temperature_deviation"],
        final_total_energy=thermo_stats["final_total_energy"],
        normalized_energy_drift=thermo_stats["normalized_energy_drift"],
        max_pressure=thermo_stats["max_pressure"],
        pressure_outlier_fraction=float(thermo_stats["pressure_outlier_fraction"]),
        has_nan_or_inf=bool(thermo_stats["has_nan_or_inf"]),
        dump_exists=bool(dump_info["dump_exists"]),
        atom_count_valid=bool(dump_info["atom_count_valid"]),
        log_errors=log_errors,
        issues=issues,
        warnings=warnings,
        thresholds=thresholds,
        metadata={
            "material": request.get("material"),
            "task_type": request.get("task_type"),
            "dump_file": dump_info["dump_file"],
        },
    )


def write_physical_quality_report(path: Path, report: PhysicalQualityReport) -> Path:
    return write_json_file(path, report.model_dump(mode="json"))


def _thermo_stats(rows: list[ThermoRow], request: dict[str, Any]) -> dict[str, Any]:
    requested_steps = _float_or_none(request.get("steps")) or 0.0
    requested_temp = _float_or_none(request.get("temperature"))
    finite_steps = [row.step for row in rows if math.isfinite(row.step)]
    finite_temps = [row.temp for row in rows if math.isfinite(row.temp)]
    finite_energies = [row.etotal for row in rows if math.isfinite(row.etotal)]
    finite_pressures = [row.press for row in rows if math.isfinite(row.press)]
    has_nan_or_inf = any(
        not math.isfinite(value)
        for row in rows
        for value in (row.step, row.temp, row.pe, row.ke, row.etotal, row.press)
    )
    max_step = max(finite_steps) if finite_steps else 0.0
    step_coverage = min(max_step / requested_steps, 1.0) if requested_steps > 0 else (1.0 if rows else 0.0)
    final_temp = rows[-1].temp if rows else None
    final_energy = rows[-1].etotal if rows else None
    energy_drift = None
    if len(finite_energies) >= 2:
        scale = max(abs(finite_energies[0]), 1.0)
        energy_drift = abs(finite_energies[-1] - finite_energies[0]) / scale
    pressure_outlier_fraction = 0.0
    if finite_pressures:
        threshold = 1.0e6
        pressure_outlier_fraction = sum(1 for value in finite_pressures if abs(value) > threshold) / len(finite_pressures)
    return {
        "thermo_rows": len(rows),
        "max_step": max_step,
        "step_coverage": step_coverage,
        "final_temperature": final_temp if final_temp is None or math.isfinite(final_temp) else None,
        "average_temperature": sum(finite_temps) / len(finite_temps) if finite_temps else None,
        "temperature_deviation": (final_temp - requested_temp) if final_temp is not None and requested_temp is not None and math.isfinite(final_temp) else None,
        "final_total_energy": final_energy if final_energy is None or math.isfinite(final_energy) else None,
        "normalized_energy_drift": energy_drift,
        "max_pressure": max(finite_pressures, key=abs) if finite_pressures else None,
        "pressure_outlier_fraction": pressure_outlier_fraction,
        "has_nan_or_inf": has_nan_or_inf,
    }


def _dump_info(output_dir: Path, dump_file_name: str) -> dict[str, Any]:
    dump_path = output_dir / (dump_file_name or "dump.atom")
    exists = dump_path.exists()
    atom_count_valid = False
    if exists:
        lines = dump_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if line.strip() == "ITEM: NUMBER OF ATOMS" and index + 1 < len(lines):
                try:
                    atom_count_valid = int(lines[index + 1].strip()) > 0
                except ValueError:
                    atom_count_valid = False
                break
    return {"dump_file": dump_path.name, "dump_exists": exists, "atom_count_valid": atom_count_valid}


def _synthetic_metadata(output_dir: Path) -> bool:
    payload = read_json_file_if_exists(output_dir / "thermo_metadata.json") or {}
    return bool(payload.get("synthetic_thermo"))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
