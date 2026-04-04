from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from src.tools.lammps_template_processor import LammpsTemplateProcessor


FCC_LATTICE = {
    "Al": 4.05,
    "Cu": 3.615,
    "Ni": 3.52,
}

ATOMIC_MASS = {
    "Al": 26.9815,
    "Cu": 63.546,
    "Ni": 58.6934,
}

EAM_FILES = {
    "Al": "Al_zhou.eam.alloy",
    "Cu": "Cu_u3.eam",
    "Ni": "Ni_u3.eam",
}


def get_lammps_form_schema() -> Dict[str, Any]:
    return LammpsTemplateProcessor().get_form_schema()


def generate_lammps_input(
    request: Dict[str, object],
    output_dir: Path,
    potentials_dir: str = "",
) -> Path:
    config = build_lammps_template_config(request, potentials_dir=potentials_dir)
    script = LammpsTemplateProcessor().render(config)
    path = output_dir / str(config["script_name"])
    path.write_text(script, encoding="utf-8")
    return path


def build_lammps_template_config(request: Dict[str, object], potentials_dir: str = "") -> Dict[str, Any]:
    material = str(request["material"])
    target_temp = _coerce_int(request.get("target_temp", request.get("temperature")), default=900)
    run_steps = _coerce_int(request.get("run_steps", request.get("steps")), default=5000)
    task_type = str(request.get("task_type", "equilibration"))
    potential_family = str(request.get("potential_family", "eam"))
    ensemble = str(request.get("ensemble", "NVT")).upper()
    box_size = _coerce_int(request.get("box_size"), default=4)
    initial_temp_default = 300 if task_type == "heating" else target_temp
    initial_temp = _coerce_int(request.get("initial_temp"), default=initial_temp_default)
    time_step = _coerce_float(request.get("time_step"), default=0.001)
    dump_file = str(request.get("dump_file", "dump.atom"))
    custom_potential_path = str(request.get("custom_potential_path", "") or "").strip()
    custom_structure_path = str(request.get("custom_structure_path", "") or "").strip()
    custom_structure_format = str(request.get("custom_structure_format", "") or "").strip()

    lattice = FCC_LATTICE.get(material, 4.0)
    mass = ATOMIC_MASS.get(material, 50.0)
    pair_style, pair_coeff = resolve_pair_settings(
        material,
        potential_family,
        potentials_dir,
        custom_potential_path=custom_potential_path,
    )
    dump_interval, dump_fields, dump_modify_line = resolve_dump_settings(material, task_type, run_steps)
    structure_setup = resolve_structure_setup(
        lattice=lattice,
        box_size=box_size,
        atomic_mass=mass,
        custom_structure_path=custom_structure_path,
        custom_structure_format=custom_structure_format,
    )

    return {
        "material": material,
        "task_type": task_type,
        "ensemble": ensemble,
        "initial_temp": initial_temp,
        "target_temp": target_temp,
        "run_steps": run_steps,
        "box_size": box_size,
        "lattice_constant": lattice,
        "atomic_mass": mass,
        "pair_style": pair_style,
        "pair_coeff": pair_coeff,
        "time_step": time_step,
        "velocity_temp": initial_temp,
        "structure_setup": structure_setup,
        "dump_interval": dump_interval,
        "dump_file": dump_file,
        "dump_fields": dump_fields,
        "dump_modify_line": dump_modify_line,
        "fix_line": resolve_fix_line(task_type, ensemble, initial_temp, target_temp),
        "script_name": str(request.get("script_name", "in.lammps")),
    }


def resolve_dump_settings(material: str, task_type: str, steps: int) -> Tuple[int, str, str]:
    dump_interval = 100
    dump_fields = "id type x y z"
    dump_modify_line = ""
    if material == "Cu" and task_type == "heating":
        dump_interval = max(50, min(250, steps // 100))
        dump_fields = "id type xu yu zu x y z"
        dump_modify_line = "dump_modify 1 sort id"
    return dump_interval, dump_fields, dump_modify_line


def resolve_fix_line(task_type: str, ensemble: str, initial_temp: int, target_temp: int) -> str:
    if ensemble == "NPT":
        if task_type == "heating":
            return f"fix 1 all npt temp {initial_temp} {target_temp} 0.1 iso 0.0 0.0 1.0"
        return f"fix 1 all npt temp {target_temp} {target_temp} 0.1 iso 0.0 0.0 1.0"
    if task_type == "heating":
        return f"fix 1 all nvt temp {initial_temp} {target_temp} 0.1"
    return f"fix 1 all nvt temp {target_temp} {target_temp} 0.1"


def resolve_structure_setup(
    lattice: float,
    box_size: int,
    atomic_mass: float,
    custom_structure_path: str,
    custom_structure_format: str,
) -> str:
    if custom_structure_path and custom_structure_format == "read_data":
        structure_path = Path(custom_structure_path).as_posix()
        return "\n".join(
            [
                f"read_data {structure_path}",
                f"mass 1 {atomic_mass}",
            ]
        )
    return "\n".join(
        [
            f"lattice fcc {lattice}",
            f"region box block 0 {box_size} 0 {box_size} 0 {box_size}",
            "create_box 1 box",
            "create_atoms 1 box",
            f"mass 1 {atomic_mass}",
        ]
    )


def resolve_pair_settings(
    material: str,
    potential_family: str,
    potentials_dir: str,
    custom_potential_path: str = "",
) -> Tuple[str, str]:
    if potential_family == "lj":
        return "lj/cut 2.5", "1 1 1.0 1.0 2.5"

    if custom_potential_path:
        eam_path = Path(custom_potential_path)
    else:
        eam_file = EAM_FILES.get(material, f"{material}.eam.alloy")
        eam_path = Path(potentials_dir) / eam_file if potentials_dir else Path("potentials") / eam_file
    suffix = "".join(eam_path.suffixes[-2:]) if len(eam_path.suffixes) >= 2 else eam_path.suffix
    if suffix == ".eam.alloy":
        return "eam/alloy", f"* * {eam_path.as_posix()} {material}"
    if suffix == ".eam.fs":
        return "eam/fs", f"* * {eam_path.as_posix()} {material}"
    return "eam", f"1 1 {eam_path.as_posix()}"


def _coerce_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
