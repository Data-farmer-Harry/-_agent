from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from app.core.cancellation import RunCancelledError, is_cancelled
from app.lammps.config import LammpsConfig
from app.lammps.quality import parse_real_thermo_to_csv, seed_thermo_rows, summarize_thermo_rows, write_thermo_csv
from app.lammps.template import EAM_FILES


def detect_lammps_command(config: LammpsConfig) -> str | None:
    if config.lammps_command:
        return config.lammps_command
    for candidate in ("lmp", "lammps"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def run_lammps(
    input_path: Path,
    output_dir: Path,
    request: dict[str, object],
    config: LammpsConfig,
    run_id: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    command = detect_lammps_command(config)
    if not command:
        raise RuntimeError("LAMMPS executable not found.")

    custom_potential_path = str(request.get("custom_potential_path", "") or "").strip()
    custom_structure_path = str(request.get("custom_structure_path", "") or "").strip()

    if custom_structure_path and not Path(custom_structure_path).exists():
        raise RuntimeError(f"Structure file not found: {custom_structure_path}")

    if request.get("potential_family") == "eam" and not custom_potential_path and not config.potentials_dir:
        raise RuntimeError("POTENTIALS_DIR is not configured for EAM runs.")
    if request.get("potential_family") == "eam":
        if custom_potential_path:
            potential_path = Path(custom_potential_path)
        else:
            material = str(request["material"])
            potential_name = EAM_FILES.get(material, f"{material}.eam.alloy")
            potential_path = Path(config.potentials_dir) / potential_name
        if not potential_path.exists():
            raise RuntimeError(f"Potential file not found: {potential_path}")

    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    run_log_path = output_dir / "run.log"
    thermo_path = output_dir / "thermo.csv"
    cmd: list[str] = [command, "-in", str(input_path)]
    proc = subprocess.Popen(
        cmd,
        cwd=output_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cancelled = False
    while proc.poll() is None:
        if run_id and is_cancelled(run_id):
            proc.terminate()
            cancelled = True
            break
        time.sleep(0.5)

    stdout, stderr = proc.communicate()
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    run_log_path.write_text(stdout + "\n" + stderr, encoding="utf-8")

    if cancelled:
        raise RunCancelledError("Simulation cancelled by user")
    if proc.returncode != 0:
        raise RuntimeError(f"LAMMPS execution failed with exit code {proc.returncode}.")

    summary = parse_real_thermo_to_csv(stdout, thermo_path)
    return "real", "", summary


def run_mock(output_dir: Path, request: dict[str, Any], error: str) -> dict[str, Any]:
    thermo_path = output_dir / "thermo.csv"
    rows = seed_thermo_rows(int(request["temperature"]), int(request["steps"]))
    write_thermo_csv(rows, thermo_path)
    (output_dir / "thermo_metadata.json").write_text(
        json.dumps(
            {
                "synthetic_thermo": True,
                "source": "mock_seed",
                "reason": error,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    dump_name = str(request.get("dump_file") or "dump.atom")
    dump_path = output_dir / dump_name
    dump_path.write_text(
        "ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n4\nITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\nITEM: ATOMS id type x y z\n1 1 1 1 1\n2 1 2 2 2\n3 1 3 3 3\n4 1 4 4 4\n",
        encoding="utf-8",
    )
    (output_dir / "run.log").write_text(f"Mock fallback enabled.\nOriginal error: {error}\n", encoding="utf-8")
    return summarize_thermo_rows(rows, synthetic=True)
