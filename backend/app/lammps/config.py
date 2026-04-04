from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any


def _default_lammps_command() -> str:
    for candidate in (
        os.getenv("LAMMPS_CMD", ""),
        "/opt/homebrew/bin/lmp",
        "/opt/homebrew/bin/lmp_serial",
        "/opt/homebrew/bin/lmp_mpi",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    resolved = shutil.which("lmp") or shutil.which("lammps")
    return resolved or ""


def _default_potentials_dir() -> str:
    for candidate in (
        os.getenv("POTENTIALS_DIR", ""),
        "/opt/homebrew/share/lammps/potentials",
        "/opt/homebrew/opt/lammps/share/lammps/potentials",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def detect_ovito_backend() -> dict[str, str | bool]:
    override = str(_RUNTIME_OVERRIDES.get("ovito_location") or "").strip()
    if override:
        candidate_path = Path(override)
        if candidate_path.exists():
            return {
                "ovito_available": True,
                "ovito_backend": "custom executable" if candidate_path.is_file() else "custom path",
                "ovito_location": str(candidate_path),
            }

    for candidate in ("ovitos", "ovito"):
        resolved = shutil.which(candidate)
        if resolved:
            return {
                "ovito_available": True,
                "ovito_backend": candidate,
                "ovito_location": resolved,
            }

    try:
        import importlib.util

        spec = importlib.util.find_spec("ovito")
        if spec:
            locations = list(spec.submodule_search_locations or [])
            resolved = locations[0] if locations else (spec.origin or "python module")
            return {
                "ovito_available": True,
                "ovito_backend": "python module",
                "ovito_location": resolved,
            }
    except Exception:
        pass

    return {
        "ovito_available": False,
        "ovito_backend": "not found",
        "ovito_location": "",
    }


@dataclass
class LammpsConfig:
    allow_mock_fallback: bool = os.getenv("ALLOW_MOCK_FALLBACK", "true").lower() == "true"
    force_mock: bool = os.getenv("USE_MOCK", "false").lower() == "true"
    lammps_command: str = _default_lammps_command()
    potentials_dir: str = _default_potentials_dir()
    ovito_location: str = os.getenv("OVITO_LOCATION", "").strip()
    max_retries: int = int(os.getenv("MAX_RUN_RETRIES", "1"))


_RUNTIME_OVERRIDES: dict[str, Any] = {}
_LOCK = Lock()


def load_lammps_config() -> LammpsConfig:
    base = asdict(LammpsConfig())
    with _LOCK:
        merged = {**base, **_RUNTIME_OVERRIDES}
    return LammpsConfig(**merged)


def update_runtime_lammps_config(payload: dict[str, Any]) -> LammpsConfig:
    allowed = {"allow_mock_fallback", "force_mock", "lammps_command", "potentials_dir", "ovito_location", "max_retries"}
    with _LOCK:
        for key, value in payload.items():
            if key not in allowed or value is None:
                continue
            if key in {"allow_mock_fallback", "force_mock"}:
                _RUNTIME_OVERRIDES[key] = bool(value)
            elif key == "max_retries":
                _RUNTIME_OVERRIDES[key] = int(value)
            else:
                _RUNTIME_OVERRIDES[key] = str(value).strip()
    return load_lammps_config()


def lammps_config_public_payload() -> dict[str, Any]:
    config = load_lammps_config()
    ovito_status = detect_ovito_backend()
    return {
        "lammps_command": config.lammps_command,
        "potentials_dir": config.potentials_dir,
        "ovito_location": config.ovito_location or str(ovito_status["ovito_location"]),
        "allow_mock_fallback": config.allow_mock_fallback,
        "force_mock": config.force_mock,
        "max_retries": config.max_retries,
        "lammps_command_exists": bool(config.lammps_command and Path(config.lammps_command).exists()),
        "potentials_dir_exists": bool(config.potentials_dir and Path(config.potentials_dir).exists()),
        "ovito_available": ovito_status["ovito_available"],
        "ovito_backend": ovito_status["ovito_backend"],
        "ovito_location": ovito_status["ovito_location"],
    }
