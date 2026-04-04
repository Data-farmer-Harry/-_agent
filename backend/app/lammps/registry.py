from __future__ import annotations

from copy import deepcopy
from typing import Any


LAMMPS_REGISTRY: dict[str, Any] = {
    "materials": {
        "Al": {"display_name": "Aluminum", "supported_potentials": ["eam", "lj"], "structure": "fcc"},
        "Cu": {"display_name": "Copper", "supported_potentials": ["eam", "lj"], "structure": "fcc"},
        "Ni": {"display_name": "Nickel", "supported_potentials": ["eam", "lj"], "structure": "fcc"},
    },
    "tasks": {
        "equilibration": "NVT equilibration for a single FCC metal",
        "heating": "linear heating ramp for a single FCC metal",
    },
    "potentials": {
        "eam": {"display_name": "Embedded Atom Method", "requires_potential_file": True},
        "lj": {"display_name": "Lennard-Jones demo potential", "requires_potential_file": False},
    },
    "uploads": {
        "custom_potential_supported": True,
        "custom_structure_supported": True,
        "future_auto_indexing": "reserved",
    },
}


def get_lammps_registry_payload() -> dict[str, Any]:
    return deepcopy(LAMMPS_REGISTRY)


def get_supported_materials() -> set[str]:
    return set(LAMMPS_REGISTRY["materials"].keys())


def get_supported_potentials() -> set[str]:
    return set(LAMMPS_REGISTRY["potentials"].keys())


def get_supported_tasks() -> set[str]:
    return set(LAMMPS_REGISTRY["tasks"].keys())
