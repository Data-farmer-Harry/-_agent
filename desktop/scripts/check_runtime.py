from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


def executable(*names: str) -> str:
    return next((resolved for name in names if (resolved := shutil.which(name))), "")


def lammps_executable() -> str:
    resolved = executable("lmp", "lammps", "lmp_serial")
    if resolved:
        return resolved
    vendor_root = Path(os.getenv("MATTERLAB_LAMMPS_ROOT", ""))
    if vendor_root.is_dir():
        return next((str(path) for path in vendor_root.rglob("lmp.exe")), "")
    return ""


def main() -> None:
    modules = ["fastapi", "uvicorn", "langgraph", "numpy", "pandas", "scipy", "matplotlib", "pycalphad", "sqlite_vec"]
    missing_modules = [name for name in modules if importlib.util.find_spec(name) is None]
    report = {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "lammps": lammps_executable(),
        "ffmpeg": executable("ffmpeg"),
        "ovito": importlib.util.find_spec("ovito") is not None,
        "missing_modules": missing_modules,
    }
    print(json.dumps(report, indent=2))
    if missing_modules or not report["lammps"] or not report["ffmpeg"] or not report["ovito"]:
        raise SystemExit("Desktop runtime is missing required capabilities.")


if __name__ == "__main__":
    main()
