from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from functools import lru_cache

from app.thermo.registry import ThermoDatabaseCard, get_thermo_database_card


def _configure_matplotlib_cache() -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/phase_diagram_agent_mpl_accuracy")


@dataclass(frozen=True)
class EndpointLiquidusEstimate:
    side: str
    endmember: str
    expected_temperature_K: float
    lower_bound_K: float
    upper_bound_K: float
    tolerance_K: float

    @property
    def midpoint_K(self) -> float:
        return (self.lower_bound_K + self.upper_bound_K) / 2.0

    @property
    def passes(self) -> bool:
        return abs(self.midpoint_K - self.expected_temperature_K) <= self.tolerance_K


@dataclass(frozen=True)
class ThermoAccuracyReport:
    system_name: str
    endpoint_estimates: tuple[EndpointLiquidusEstimate, ...]
    stable_phases_seen: tuple[str, ...]
    missing_required_phases: tuple[str, ...]

    @property
    def passes(self) -> bool:
        return all(item.passes for item in self.endpoint_estimates) and not self.missing_required_phases


def _load_pycalphad_symbols():
    _configure_matplotlib_cache()
    warnings.filterwarnings("ignore", module="pycalphad.io.tdb")
    warnings.filterwarnings("ignore", module="pycalphad.io.grammar")
    from pycalphad import Database, equilibrium, variables as v

    return Database, equilibrium, v


@lru_cache(maxsize=8)
def _load_database(path: str):
    Database, _, _ = _load_pycalphad_symbols()
    return Database(path)


def _component_list(card: ThermoDatabaseCard) -> list[str]:
    return [*card.components, "VA"]


def _chosen_phases(card: ThermoDatabaseCard) -> list[str]:
    return list(card.phases)


def _liquid_phase_names(card: ThermoDatabaseCard) -> tuple[str, ...]:
    reference = card.accuracy_reference
    explicit_names = reference.get("liquid_phase_names")
    if isinstance(explicit_names, (list, tuple)):
        names = tuple(str(item).strip() for item in explicit_names if str(item).strip())
        if names:
            return names
    explicit_name = str(reference.get("liquid_phase_name", "")).strip()
    if explicit_name:
        return (explicit_name,)
    return ("LIQUID",)


def _round_float(value: float, *, digits: int = 3) -> float:
    return round(float(value), digits)


@lru_cache(maxsize=2048)
def _stable_phase_names_cached(
    database_path: str,
    components: tuple[str, ...],
    phases: tuple[str, ...],
    x_component: str,
    composition: float,
    temperature: float,
    pressure: float,
) -> tuple[str, ...]:
    _, equilibrium, v = _load_pycalphad_symbols()
    database = _load_database(database_path)
    result = equilibrium(
        database,
        list(components),
        list(phases),
        {v.X(x_component): composition, v.T: temperature, v.P: pressure, v.N: 1.0},
    )
    stable = sorted({str(phase).strip() for phase in result.Phase.values.ravel() if str(phase).strip()})
    return tuple(stable)


def _stable_phase_names(card: ThermoDatabaseCard, *, composition: float, temperature: float, pressure: float = 101325.0) -> tuple[str, ...]:
    return _stable_phase_names_cached(
        str(card.database_path),
        tuple(_component_list(card)),
        tuple(_chosen_phases(card)),
        card.x_component,
        _round_float(composition, digits=6),
        _round_float(temperature, digits=3),
        _round_float(pressure, digits=3),
    )


def estimate_endpoint_liquidus(card: ThermoDatabaseCard, *, side: str, temperature_min: float, temperature_max: float, step_K: float = 5.0) -> EndpointLiquidusEstimate:
    reference = card.accuracy_reference
    if side not in {"left", "right"}:
        raise ValueError(f"Unsupported side: {side}")

    composition = 1e-3 if side == "left" else 0.999
    endmember = str(reference[f"{side}_endmember"])
    expected = float(reference[f"{side}_liquidus_K"])
    tolerance = float(reference.get("liquidus_tolerance_K", 25.0))
    liquid_phase_names = _liquid_phase_names(card)

    last_without_liquid = float(temperature_min)
    first_with_liquid = float(temperature_max)
    found = False
    temperature = float(temperature_min)
    while temperature <= float(temperature_max):
        phases = _stable_phase_names(card, composition=composition, temperature=temperature)
        if any(name in phases for name in liquid_phase_names):
            first_with_liquid = temperature
            found = True
            break
        last_without_liquid = temperature
        temperature += float(step_K)

    if not found:
        liquid_label = ", ".join(liquid_phase_names)
        raise RuntimeError(f"Could not detect {liquid_label} on the {side} endpoint for {card.system_name}.")

    return EndpointLiquidusEstimate(
        side=side,
        endmember=endmember,
        expected_temperature_K=expected,
        lower_bound_K=last_without_liquid,
        upper_bound_K=first_with_liquid,
        tolerance_K=tolerance,
    )


def scan_stable_phases(card: ThermoDatabaseCard, *, temperature_min: float, temperature_max: float) -> tuple[str, ...]:
    seen: set[str] = set()
    for composition in (0.001, 0.05, 0.2, 0.35, 0.5, 0.7, 0.95, 0.999):
        temperature = float(temperature_min)
        while temperature <= float(temperature_max):
            seen.update(_stable_phase_names(card, composition=composition, temperature=temperature))
            temperature += 100.0
    return tuple(sorted(seen))


@lru_cache(maxsize=64)
def _build_thermo_accuracy_report_cached(
    system_name: str,
    temperature_min: float,
    temperature_max: float,
) -> ThermoAccuracyReport:
    card = get_thermo_database_card(system_name)
    if card is None:
        raise KeyError(f"Unknown thermodynamic registry system for accuracy report: {system_name}")
    stable_phases = scan_stable_phases(card, temperature_min=temperature_min, temperature_max=temperature_max)
    required = tuple(str(item) for item in card.accuracy_reference.get("required_stable_phases", []))
    missing = tuple(phase for phase in required if phase not in stable_phases)
    estimates = (
        estimate_endpoint_liquidus(card, side="left", temperature_min=temperature_min, temperature_max=temperature_max),
        estimate_endpoint_liquidus(card, side="right", temperature_min=temperature_min, temperature_max=temperature_max),
    )
    return ThermoAccuracyReport(
        system_name=card.system_name,
        endpoint_estimates=estimates,
        stable_phases_seen=stable_phases,
        missing_required_phases=missing,
    )


def build_thermo_accuracy_report(card: ThermoDatabaseCard, *, temperature_min: float, temperature_max: float) -> ThermoAccuracyReport:
    return _build_thermo_accuracy_report_cached(
        card.system_name,
        _round_float(temperature_min, digits=3),
        _round_float(temperature_max, digits=3),
    )
