from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from app.config import read_runtime_config_file, update_runtime_config_file


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


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

    resolved_ovitos = shutil.which("ovitos")
    if resolved_ovitos:
        return {
            "ovito_available": True,
            "ovito_backend": "ovitos",
            "ovito_location": resolved_ovitos,
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

    resolved_desktop_ovito = shutil.which("ovito")
    if resolved_desktop_ovito:
        return {
            "ovito_available": False,
            "ovito_backend": "ovito desktop executable (no scripting backend)",
            "ovito_location": resolved_desktop_ovito,
        }

    return {
        "ovito_available": False,
        "ovito_backend": "not found",
        "ovito_location": "",
    }


@dataclass
class LammpsConfig:
    allow_mock_fallback: bool = _env_bool("ALLOW_MOCK_FALLBACK", True)
    force_mock: bool = _env_bool("USE_MOCK", False)
    lammps_command: str = _default_lammps_command()
    potentials_dir: str = _default_potentials_dir()
    ovito_location: str = os.getenv("OVITO_LOCATION", "").strip()
    max_retries: int = int(os.getenv("MAX_RUN_RETRIES", "1"))
    lammps_preflight_dag_enabled: bool = _env_bool("LAMMPS_PREFLIGHT_DAG_ENABLED", False)
    lammps_red_blue_review_enabled: bool = _env_bool("LAMMPS_RED_BLUE_REVIEW_ENABLED", True)


_RUNTIME_OVERRIDES: dict[str, Any] = {}
_LOCK = Lock()


def load_lammps_config() -> LammpsConfig:
    base = asdict(LammpsConfig())
    persisted = read_runtime_config_file()
    for key in (
        "lammps_command",
        "potentials_dir",
        "ovito_location",
        "allow_mock_fallback",
        "force_mock",
        "max_retries",
        "lammps_preflight_dag_enabled",
        "lammps_red_blue_review_enabled",
    ):
        value = persisted.get(key)
        if value is None or value == "":
            continue
        if key in {"allow_mock_fallback", "force_mock", "lammps_preflight_dag_enabled", "lammps_red_blue_review_enabled"}:
            base[key] = _coerce_bool(value)
        elif key == "max_retries":
            base[key] = int(value)
        else:
            base[key] = str(value).strip()
    with _LOCK:
        merged = {**base, **_RUNTIME_OVERRIDES}
    return LammpsConfig(**merged)


def update_runtime_lammps_config(payload: dict[str, Any]) -> LammpsConfig:
    allowed = {
        "allow_mock_fallback",
        "force_mock",
        "lammps_command",
        "potentials_dir",
        "ovito_location",
        "max_retries",
        "lammps_preflight_dag_enabled",
        "lammps_red_blue_review_enabled",
    }
    persist_patch: dict[str, Any] = {}
    with _LOCK:
        for key, value in payload.items():
            if key not in allowed or value is None:
                continue
            if key in {"allow_mock_fallback", "force_mock", "lammps_preflight_dag_enabled", "lammps_red_blue_review_enabled"}:
                normalized = _coerce_bool(value)
                _RUNTIME_OVERRIDES[key] = normalized
                persist_patch[key] = normalized
            elif key == "max_retries":
                normalized = int(value)
                _RUNTIME_OVERRIDES[key] = normalized
                persist_patch[key] = normalized
            else:
                normalized = str(value).strip()
                _RUNTIME_OVERRIDES[key] = normalized
                persist_patch[key] = normalized
    if persist_patch:
        update_runtime_config_file(persist_patch)
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
        "lammps_preflight_dag_enabled": config.lammps_preflight_dag_enabled,
        "lammps_red_blue_review_enabled": config.lammps_red_blue_review_enabled,
        "lammps_command_exists": bool(config.lammps_command and Path(config.lammps_command).exists()),
        "potentials_dir_exists": bool(config.potentials_dir and Path(config.potentials_dir).exists()),
        "ovito_available": ovito_status["ovito_available"],
        "ovito_backend": ovito_status["ovito_backend"],
        "ovito_location": ovito_status["ovito_location"],
    }
