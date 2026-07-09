from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RunMode = Literal["real", "mock"]


class ThermoRow(BaseModel):
    step: float
    temp: float
    pe: float
    ke: float
    etotal: float
    press: float


class PhysicalQualityReport(BaseModel):
    schema_version: str = "lammps-physical-quality/v1"
    run_mode: RunMode
    passed: bool
    scientific_result_passed: bool
    synthetic_thermo: bool = False
    thermo_rows: int = 0
    max_step: float = 0.0
    requested_steps: int = 0
    step_coverage: float = 0.0
    final_temperature: float | None = None
    average_temperature: float | None = None
    temperature_deviation: float | None = None
    final_total_energy: float | None = None
    normalized_energy_drift: float | None = None
    max_pressure: float | None = None
    pressure_outlier_fraction: float = 0.0
    has_nan_or_inf: bool = False
    dump_exists: bool = False
    atom_count_valid: bool = False
    log_errors: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
