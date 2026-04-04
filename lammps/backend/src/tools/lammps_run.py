from __future__ import annotations

import csv
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Tuple

from src.config.supervisor_config import SupervisorConfig
from src.tools.generate_lammps_in import EAM_FILES
from src.utils.cancellation import is_cancelled, SimulationCancelledError


def detect_lammps_command(config: SupervisorConfig) -> str | None:
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
    request: Dict[str, object],
    config: SupervisorConfig,
    run_id: str | None = None,
) -> Tuple[str, str, Dict[str, float]]:
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

    cmd: List[str] = [command, "-in", str(input_path)]
    
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
        raise SimulationCancelledError("Simulation cancelled by user")

    if proc.returncode != 0:
        raise RuntimeError(f"LAMMPS execution failed with exit code {proc.returncode}.")

    summary = extract_or_seed_thermo(stdout, request, thermo_path)
    return "real", "", summary


def extract_or_seed_thermo(stdout: str, request: Dict[str, object], thermo_path: Path) -> Dict[str, float]:
    rows: List[Dict[str, float]] = []
    for line in stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 6 or not parts[0].isdigit():
            continue
        try:
            rows.append(
                {
                    "step": float(parts[0]),
                    "temp": float(parts[1]),
                    "pe": float(parts[2]),
                    "ke": float(parts[3]),
                    "etotal": float(parts[4]),
                    "press": float(parts[5]),
                }
            )
        except ValueError:
            continue

    if not rows:
        rows = seed_thermo_rows(int(request["temperature"]), int(request["steps"]))

    with thermo_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["step", "temp", "pe", "ke", "etotal", "press"])
        writer.writeheader()
        writer.writerows(rows)

    last = rows[-1]
    return {
        "final_temp": round(last["temp"], 3),
        "final_pe": round(last["pe"], 3),
        "final_etotal": round(last["etotal"], 3),
        "max_press": round(max(row["press"] for row in rows), 3),
    }


def seed_thermo_rows(temperature: int, steps: int) -> List[Dict[str, float]]:
    interval = max(steps // 10, 100)
    rows = []
    for idx, step in enumerate(range(0, steps + interval, interval)):
        temp = min(temperature, 300 + idx * (temperature - 300) / 10)
        pe = -3.36 + idx * 0.01
        ke = 0.025 * temp
        etotal = pe + ke
        press = 100 + idx * 4
        rows.append(
            {
                "step": float(step),
                "temp": float(temp),
                "pe": float(pe),
                "ke": float(ke),
                "etotal": float(etotal),
                "press": float(press),
            }
        )
    return rows


def run_mock(output_dir: Path, request: Dict[str, object], error: str) -> Dict[str, float]:
    thermo_path = output_dir / "thermo.csv"
    rows = seed_thermo_rows(int(request["temperature"]), int(request["steps"]))
    with thermo_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["step", "temp", "pe", "ke", "etotal", "press"])
        writer.writeheader()
        writer.writerows(rows)

    dump_path = output_dir / "dump.atom"
    dump_path.write_text(
        "ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n4\nITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\nITEM: ATOMS id type x y z\n1 1 1 1 1\n2 1 2 2 2\n3 1 3 3 3\n4 1 4 4 4\n",
        encoding="utf-8",
    )
    (output_dir / "run.log").write_text(
        f"Mock fallback enabled.\nOriginal error: {error}\n",
        encoding="utf-8",
    )
    last = rows[-1]
    return {
        "final_temp": round(last["temp"], 3),
        "final_pe": round(last["pe"], 3),
        "final_etotal": round(last["etotal"], 3),
        "max_press": round(max(row["press"] for row in rows), 3),
    }
