from __future__ import annotations

from typing import Any


GLOBAL_THRESHOLDS: dict[str, Any] = {
    "min_thermo_rows": 2,
    "min_step_coverage": 0.8,
    "max_temperature_relative_deviation": 0.5,
    "max_normalized_energy_drift": 25.0,
    "max_pressure_abs": 1.0e7,
    "max_pressure_outlier_fraction": 0.2,
}

TASK_THRESHOLDS: dict[str, dict[str, Any]] = {
    "heating": {
        "min_step_coverage": 0.8,
        "max_temperature_relative_deviation": 0.6,
    },
    "equilibration": {
        "min_step_coverage": 0.8,
        "max_temperature_relative_deviation": 0.35,
    },
}

MATERIAL_THRESHOLDS: dict[str, dict[str, Any]] = {
    "Al": {},
    "Cu": {},
    "Ni": {},
}


def resolve_quality_thresholds(request: dict[str, Any]) -> dict[str, Any]:
    thresholds = dict(GLOBAL_THRESHOLDS)
    task_type = str(request.get("task_type") or "").strip()
    material = str(request.get("material") or "").strip()
    thresholds.update(TASK_THRESHOLDS.get(task_type, {}))
    thresholds.update(MATERIAL_THRESHOLDS.get(material, {}))
    user_thresholds = request.get("quality_thresholds")
    if isinstance(user_thresholds, dict):
        thresholds.update(user_thresholds)
    thresholds["sources"] = {
        "global": sorted(GLOBAL_THRESHOLDS),
        "task": task_type if task_type in TASK_THRESHOLDS else "",
        "material": material if material in MATERIAL_THRESHOLDS else "",
        "user": sorted(user_thresholds) if isinstance(user_thresholds, dict) else [],
    }
    return thresholds
