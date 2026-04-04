from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict


def _default_lammps_command() -> str:
    for candidate in (
        os.getenv("LAMMPS_CMD", ""),
        "/opt/homebrew/bin/lmp_serial",
        "/opt/homebrew/bin/lmp_mpi",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def _default_potentials_dir() -> str:
    for candidate in (
        os.getenv("POTENTIALS_DIR", ""),
        "/opt/homebrew/opt/lammps/share/lammps/potentials",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def detect_ovito_backend() -> Dict[str, str | bool]:
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

    for candidate in ("ovitos", "ovito"):
        resolved = shutil.which(candidate)
        if resolved:
            return {
                "ovito_available": True,
                "ovito_backend": candidate,
                "ovito_location": resolved,
            }

    app_binary = Path("/Applications/Ovito.app/Contents/MacOS/ovito")
    if app_binary.exists():
        return {
            "ovito_available": True,
            "ovito_backend": "ovito app",
            "ovito_location": str(app_binary),
        }

    return {
        "ovito_available": False,
        "ovito_backend": "not found",
        "ovito_location": "",
    }


@dataclass
class SupervisorConfig:
    allow_mock_fallback: bool = os.getenv("ALLOW_MOCK_FALLBACK", "true").lower() == "true"
    force_mock: bool = os.getenv("USE_MOCK", "false").lower() == "true"
    lammps_command: str = _default_lammps_command()
    potentials_dir: str = _default_potentials_dir()
    max_retries: int = int(os.getenv("MAX_RUN_RETRIES", "1"))


_RUNTIME_OVERRIDES: Dict[str, Any] = {}
_LOCK = Lock()


def load_supervisor_config() -> SupervisorConfig:
    base = asdict(SupervisorConfig())
    with _LOCK:
        merged = {**base, **_RUNTIME_OVERRIDES}
    return SupervisorConfig(**merged)


def update_runtime_supervisor_config(payload: Dict[str, Any]) -> SupervisorConfig:
    allowed = {"allow_mock_fallback", "force_mock", "lammps_command", "potentials_dir", "max_retries"}
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
    return load_supervisor_config()


def supervisor_config_public_payload() -> Dict[str, Any]:
    config = load_supervisor_config()
    ovito_status = detect_ovito_backend()
    return {
        "lammps_command": config.lammps_command,
        "potentials_dir": config.potentials_dir,
        "allow_mock_fallback": config.allow_mock_fallback,
        "force_mock": config.force_mock,
        "max_retries": config.max_retries,
        "lammps_command_exists": bool(config.lammps_command and Path(config.lammps_command).exists()),
        "potentials_dir_exists": bool(config.potentials_dir and Path(config.potentials_dir).exists()),
        "ovito_available": ovito_status["ovito_available"],
        "ovito_backend": ovito_status["ovito_backend"],
        "ovito_location": ovito_status["ovito_location"],
    }
