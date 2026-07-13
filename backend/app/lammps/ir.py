from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.lammps.registry import get_supported_materials, get_supported_potentials, get_supported_tasks
from app.lammps.template import EAM_FILES


class LammpsIRValidationError(ValueError):
    pass


class PhysicalQuantity(BaseModel):
    value: float
    unit: str


class PotentialSpec(BaseModel):
    family: Literal["eam", "lj"]
    file_path: str = ""
    pair_style: str = ""


class StructureSpec(BaseModel):
    material: str
    crystal: str = "fcc"
    repetitions: int = Field(default=4, ge=1, le=30)
    source_path: str = ""
    source_format: str = ""


class EnsembleSpec(BaseModel):
    kind: Literal["NVT", "NPT"] = "NVT"
    initial_temperature: PhysicalQuantity
    target_temperature: PhysicalQuantity
    thermostat_damping: PhysicalQuantity = Field(default_factory=lambda: PhysicalQuantity(value=0.1, unit="ps"))
    pressure: PhysicalQuantity | None = None
    barostat_damping: PhysicalQuantity | None = None


class SamplingSpec(BaseModel):
    steps: int = Field(ge=100, le=100_000)
    timestep: PhysicalQuantity
    dump_interval: int = Field(default=100, ge=1)
    dump_file: str = "dump.atom"
    thermo_interval: int = Field(default=100, ge=1)


class LammpsSimulationIR(BaseModel):
    schema_version: str = "matterlab-lammps-ir/v1"
    task_type: Literal["equilibration", "heating"]
    units: Literal["metal"] = "metal"
    dimension: Literal[3] = 3
    boundary: tuple[str, str, str] = ("p", "p", "p")
    atom_style: Literal["atomic"] = "atomic"
    structure: StructureSpec
    potential: PotentialSpec
    ensemble: EnsembleSpec
    sampling: SamplingSpec
    locked_fields: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class IRValidationReport(BaseModel):
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)


def request_to_ir(request: dict[str, Any]) -> LammpsSimulationIR:
    material = str(request.get("material") or "")
    task_type = str(request.get("task_type") or "equilibration").lower()
    target = float(request.get("temperature") or request.get("target_temp") or 900)
    initial_default = 300.0 if task_type == "heating" else target
    initial = float(request.get("initial_temp") if request.get("initial_temp") is not None else initial_default)
    steps = int(request.get("steps") or request.get("run_steps") or 5000)
    timestep = float(request.get("time_step") or 0.001)
    potential_family = str(request.get("potential_family") or "eam").lower()
    ensemble = str(request.get("ensemble") or "NVT").upper()
    dump_interval = max(1, int(request.get("dump_interval") or _default_dump_interval(task_type, steps)))
    return LammpsSimulationIR(
        task_type=task_type,  # type: ignore[arg-type]
        structure=StructureSpec(
            material=material,
            repetitions=int(request.get("box_size") or 4),
            source_path=str(request.get("custom_structure_path") or ""),
            source_format=str(request.get("custom_structure_format") or ""),
        ),
        potential=PotentialSpec(
            family=potential_family,  # type: ignore[arg-type]
            file_path=str(request.get("custom_potential_path") or ""),
        ),
        ensemble=EnsembleSpec(
            kind=ensemble,  # type: ignore[arg-type]
            initial_temperature=PhysicalQuantity(value=initial, unit="K"),
            target_temperature=PhysicalQuantity(value=target, unit="K"),
            pressure=PhysicalQuantity(value=0.0, unit="bar") if ensemble == "NPT" else None,
            barostat_damping=PhysicalQuantity(value=1.0, unit="ps") if ensemble == "NPT" else None,
        ),
        sampling=SamplingSpec(
            steps=steps,
            timestep=PhysicalQuantity(value=timestep, unit="ps"),
            dump_interval=dump_interval,
            dump_file=str(request.get("dump_file") or "dump.atom"),
        ),
        locked_fields=[
            "structure.material",
            "potential.family",
            "task_type",
            "ensemble.kind",
            "ensemble.target_temperature",
            "sampling.steps",
        ],
        provenance={"source": "validated_lammps_request", "compiler": "matterlab-neurosymbolic/v1"},
    )


def validate_ir(ir: LammpsSimulationIR) -> IRValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    material = ir.structure.material
    target = ir.ensemble.target_temperature
    initial = ir.ensemble.initial_temperature
    timestep = ir.sampling.timestep
    checks = {
        "material_registered": material in get_supported_materials(),
        "potential_registered": ir.potential.family in get_supported_potentials(),
        "task_registered": ir.task_type in get_supported_tasks(),
        "temperature_units": target.unit == "K" and initial.unit == "K",
        "timestep_units": timestep.unit == "ps",
        "ensemble_thermostat_compatible": ir.ensemble.kind in {"NVT", "NPT"},
        "npt_pressure_complete": ir.ensemble.kind != "NPT" or (
            ir.ensemble.pressure is not None and ir.ensemble.barostat_damping is not None
        ),
        "eam_mapping_available": ir.potential.family != "eam" or bool(ir.potential.file_path) or material in EAM_FILES,
        "periodic_bulk_boundary": ir.boundary == ("p", "p", "p"),
    }
    for name, passed in checks.items():
        if not passed:
            errors.append(f"IR constraint failed: {name}")
    if not 50 <= target.value <= 2000:
        errors.append("Target temperature must be within 50-2000 K for the interactive runtime.")
    if not 0 < timestep.value <= 0.01:
        errors.append("Metal-unit timestep must be within (0, 0.01] ps.")
    elif timestep.value > 0.003:
        warnings.append("Timestep is high for a metal-unit atomistic simulation; run a pilot stability check first.")
    if ir.task_type == "heating" and target.value <= initial.value:
        errors.append("Heating requires target temperature greater than initial temperature.")
    thermostat = ir.ensemble.thermostat_damping.value
    if thermostat < 10 * timestep.value:
        errors.append("Thermostat damping must be at least 10 timesteps.")
    if ir.ensemble.kind == "NPT" and ir.ensemble.barostat_damping is not None:
        if ir.ensemble.barostat_damping.value <= thermostat:
            warnings.append("Barostat damping is usually expected to exceed thermostat damping.")
    if ir.sampling.dump_interval > ir.sampling.steps:
        warnings.append("Dump interval exceeds total steps; trajectory may contain only the initial frame.")
    return IRValidationReport(passed=not errors, errors=errors, warnings=warnings, checks=checks)


def compile_ir(ir: LammpsSimulationIR) -> dict[str, Any]:
    report = validate_ir(ir)
    if not report.passed:
        raise LammpsIRValidationError("; ".join(report.errors))
    return {
        "material": ir.structure.material,
        "potential_family": ir.potential.family,
        "task_type": ir.task_type,
        "temperature": int(ir.ensemble.target_temperature.value),
        "steps": ir.sampling.steps,
        "ensemble": ir.ensemble.kind,
        "box_size": ir.structure.repetitions,
        "initial_temp": int(ir.ensemble.initial_temperature.value),
        "time_step": ir.sampling.timestep.value,
        "dump_file": ir.sampling.dump_file,
        "custom_potential_path": ir.potential.file_path,
        "custom_structure_path": ir.structure.source_path,
        "custom_structure_format": ir.structure.source_format,
        "ir_validation": report.model_dump(mode="json"),
    }


def _default_dump_interval(task_type: str, steps: int) -> int:
    if task_type == "heating":
        return max(50, min(250, steps // 100))
    return 100
