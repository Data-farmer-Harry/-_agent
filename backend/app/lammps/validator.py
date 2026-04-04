from __future__ import annotations

from typing import Any

from app.lammps.registry import get_supported_materials, get_supported_potentials, get_supported_tasks
from app.lammps.template import EAM_FILES

REQUIRED_REQUEST_FIELDS = [
    "material",
    "potential_family",
    "task_type",
    "temperature",
    "steps",
]
HARD_MAX_STEPS = 100000
WARN_STEPS = 20000


def validate_request(normalized: dict[str, Any]) -> dict[str, Any]:
    missing_fields = [field for field in REQUIRED_REQUEST_FIELDS if not normalized.get(field)]
    errors: list[str] = []
    warnings: list[str] = []

    material = normalized.get("material")
    potential = normalized.get("potential_family")
    task_type = normalized.get("task_type")
    temperature = _to_int(normalized.get("temperature"))
    steps = _to_int(normalized.get("steps"))

    supported_materials = get_supported_materials()
    supported_potentials = get_supported_potentials()
    supported_tasks = get_supported_tasks()

    if material and material not in supported_materials:
        errors.append(f"当前 demo 只支持 {', '.join(sorted(supported_materials))}，收到 {material}。")

    if potential and potential not in supported_potentials:
        errors.append(f"当前只支持势函数类型 {', '.join(sorted(supported_potentials))}。")

    if task_type and task_type not in supported_tasks:
        errors.append(f"当前只支持任务类型 {', '.join(sorted(supported_tasks))}。")

    if temperature is not None:
        if temperature < 50:
            errors.append("温度过低，当前 demo 要求 temperature >= 50 K。")
        elif temperature > 2000:
            errors.append("温度过高，当前 demo 要求 temperature <= 2000 K。")
        elif temperature > 1200:
            warnings.append("温度较高，结果可能不稳定，建议先用 300-1000 K 做测试。")

    if steps is not None:
        if steps < 100:
            errors.append("步数过少，当前 demo 要求 steps >= 100。")
        elif steps > HARD_MAX_STEPS:
            errors.append(f"步数 {steps} 超出交互式 demo 上限 {HARD_MAX_STEPS}，请先用更短任务验证。")
        elif steps > WARN_STEPS:
            warnings.append(f"步数 {steps} 较大，运行可能较慢，建议先用 <= {WARN_STEPS}。")

    if potential == "eam" and material and material not in EAM_FILES and not normalized.get("custom_potential_path"):
        errors.append(f"没有为 {material} 配置 EAM 势文件映射。")

    if task_type == "heating" and temperature is not None and temperature <= 300:
        warnings.append("heating 任务的目标温度不高于 300 K，升温意义可能不明显。")
    if material in {"Al", "Cu", "Ni"} and task_type == "heating":
        if temperature is not None and temperature < 600:
            warnings.append(f"{material} heating 任务温度偏低，扩散轨迹图可能不明显。")
        if steps is not None and steps < 3000:
            warnings.append(f"{material} heating 任务步数偏少，扩散轨迹图可能较短。")

    is_complete = not missing_fields
    is_reasonable = is_complete and not errors
    return {
        "is_complete": is_complete,
        "is_reasonable": is_reasonable,
        "missing_fields": missing_fields,
        "errors": errors,
        "warnings": warnings,
    }


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
