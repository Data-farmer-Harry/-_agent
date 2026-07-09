from __future__ import annotations

import re
from typing import Any

from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


MELTING_HINTS_K = {
    "al": 933.47,
    "cu": 1357.77,
    "ni": 1728.0,
    "fe": 1811.0,
    "pb": 600.61,
    "sn": 505.08,
}

PRESSURE_TO_PA = {
    "pa": 1.0,
    "kpa": 1e3,
    "mpa": 1e6,
    "gpa": 1e9,
    "bar": 1e5,
    "atm": 101325.0,
}

TIME_TO_PS = {
    "fs": 0.001,
    "ps": 1.0,
    "ns": 1000.0,
}


def _first_number(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    for value in match.groups():
        if value is not None:
            return float(value)
    return None


def _extract_material(text: str) -> str:
    match = re.search(r"\b(Al|Cu|Ni|Fe|Pb|Sn)\b|铝|铜|镍|铁|铅|锡", text, flags=re.IGNORECASE)
    if not match:
        return ""
    raw = match.group(0).lower()
    mapping = {"铝": "al", "铜": "cu", "镍": "ni", "铁": "fe", "铅": "pb", "锡": "sn"}
    return mapping.get(raw, raw)


def _extract_timestep_ps(text: str, arguments: dict[str, Any]) -> float | None:
    if arguments.get("timestep") is not None:
        value = float(arguments["timestep"])
        unit = str(arguments.get("timestep_unit") or "ps").lower()
        return value * TIME_TO_PS.get(unit, 1.0)
    match = re.search(r"(?:timestep|time\s*step|时间步长)\s*[:=]?\s*([0-9.]+)\s*(fs|ps|ns)?", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "ps").lower()
    return value * TIME_TO_PS.get(unit, 1.0)


def _extract_pressure_pa(text: str, arguments: dict[str, Any]) -> float | None:
    if arguments.get("pressure") is not None:
        value = float(arguments["pressure"])
        unit = str(arguments.get("pressure_unit") or "atm").lower()
        return value * PRESSURE_TO_PA.get(unit, 1.0)
    match = re.search(r"(?:pressure|压力)\s*[:=]?\s*([0-9.+\-Ee]+)\s*(pa|kpa|mpa|gpa|bar|atm)?", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "atm").lower()
    return value * PRESSURE_TO_PA.get(unit, 1.0)


def _physics_check(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    text = str(arguments.get("text") or context.request_message or "")
    material = str(arguments.get("material") or "").lower().strip() or _extract_material(text)
    temperature = arguments.get("temperature")
    if temperature is None:
        temperature = _first_number(r"(\d+(?:\.\d+)?)\s*(?:k|kelvin|K)\b|温度\s*[:=]?\s*(\d+(?:\.\d+)?)", text)
    temperature_k = float(temperature) if temperature is not None else None
    steps = arguments.get("steps")
    if steps is None:
        steps = _first_number(r"(\d{2,9})\s*(?:steps?|步)\b|步数\s*[:=]?\s*(\d{2,9})", text)
    steps_int = int(steps) if steps is not None else None
    timestep_ps = _extract_timestep_ps(text, arguments)
    pressure_pa = _extract_pressure_pa(text, arguments)
    units = str(arguments.get("units") or ("metal" if "metal" in text.lower() else "metal")).lower()
    ensemble = str(arguments.get("ensemble") or ("npt" if "npt" in text.lower() else "nvt" if "nvt" in text.lower() else "")).upper()

    warnings: list[str] = []
    recommendations: list[str] = []
    conversions: dict[str, Any] = {}

    if timestep_ps is not None:
        conversions["timestep_ps"] = round(timestep_ps, 8)
        conversions["timestep_fs"] = round(timestep_ps / TIME_TO_PS["fs"], 4)
        if units == "metal" and timestep_ps > 0.01:
            warnings.append("LAMMPS metal units 下 timestep 大于 0.01 ps，很多金属 MD 任务会偏大，建议确认。")
        elif units == "metal" and timestep_ps <= 0:
            warnings.append("timestep 必须为正。")
    else:
        recommendations.append("如果是 LAMMPS 任务，建议显式给出 timestep，便于计算总模拟时间和稳定性。")

    if steps_int is not None:
        conversions["steps"] = steps_int
        if steps_int < 1000:
            warnings.append("步数低于 1000，通常只能做 smoke test，不足以代表平衡或统计性质。")
        if timestep_ps is not None:
            total_ps = steps_int * timestep_ps
            conversions["total_simulation_time_ps"] = round(total_ps, 6)
            conversions["total_simulation_time_ns"] = round(total_ps / 1000.0, 6)
            if total_ps < 1.0:
                warnings.append("总模拟时间低于 1 ps，通常过短，只适合快速连通性测试。")

    if temperature_k is not None:
        conversions["temperature_K"] = round(temperature_k, 4)
        conversions["temperature_C"] = round(temperature_k - 273.15, 4)
        if temperature_k <= 0:
            warnings.append("温度必须大于 0 K。")
        if material in MELTING_HINTS_K:
            tm = MELTING_HINTS_K[material]
            ratio = temperature_k / tm
            conversions["melting_point_hint_K"] = tm
            conversions["temperature_over_melting_hint"] = round(ratio, 4)
            if ratio > 1.5:
                warnings.append(f"{material.title()} 的目标温度显著高于常见熔点参考值，若不是熔化/高温任务，需要确认。")
            elif ratio > 1.0:
                recommendations.append(f"目标温度高于 {material.title()} 熔点参考值，适合熔化/液相任务；固相性质需谨慎解释。")

    if pressure_pa is not None:
        conversions["pressure_Pa"] = round(pressure_pa, 6)
        conversions["pressure_bar"] = round(pressure_pa / PRESSURE_TO_PA["bar"], 6)
        conversions["pressure_atm"] = round(pressure_pa / PRESSURE_TO_PA["atm"], 6)
        if ensemble == "NPT" and abs(pressure_pa) > 1e10:
            warnings.append("NPT 压力绝对值超过 10 GPa，除非是高压任务，否则建议检查单位。")

    if ensemble == "NPT" and pressure_pa is None:
        recommendations.append("NPT 任务建议明确压力单位；LAMMPS metal units 中压力单位是 bar。")
    if not warnings:
        recommendations.append("未发现明显物理/单位红旗；仍建议结合势函数适用范围和体系尺寸复核。")

    passed = not warnings
    return ToolResult(
        tool_name="physics.check",
        success=True,
        summary="物理参数校验完成，未发现明显红旗。" if passed else f"物理参数校验发现 {len(warnings)} 个需要确认的问题。",
        output={
            "passed": passed,
            "material": material,
            "units": units,
            "ensemble": ensemble,
            "conversions": conversions,
            "warnings": warnings,
            "recommendations": recommendations,
        },
    )


def register_physics_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="physics.check",
            description="Check common materials-simulation units and physical parameters, especially LAMMPS timestep, temperature, pressure, steps, total simulated time, and simple material melting-point sanity hints.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "material": {"type": "string"},
                    "temperature": {"type": "number"},
                    "steps": {"type": "integer"},
                    "timestep": {"type": "number"},
                    "timestep_unit": {"type": "string", "enum": ["fs", "ps", "ns"]},
                    "pressure": {"type": "number"},
                    "pressure_unit": {"type": "string", "enum": ["Pa", "kPa", "MPa", "GPa", "bar", "atm"]},
                    "units": {"type": "string", "enum": ["metal", "real", "si", "lj"]},
                    "ensemble": {"type": "string"},
                },
            },
            risk=ToolRisk.SAFE,
            read_only=True,
        ),
        _physics_check,
    )
