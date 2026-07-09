from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.tools.builtin.file_tools import _decode_data_url, _resolve_safe_path
from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


@dataclass
class AtomSite:
    element: str
    x: float
    y: float
    z: float


@dataclass
class StructureModel:
    atoms: list[AtomSite] = field(default_factory=list)
    cell: list[list[float]] = field(default_factory=list)
    coordinate_mode: str = "cartesian"
    source_format: str = "unknown"

    @property
    def elements(self) -> list[str]:
        seen: list[str] = []
        for atom in self.atoms:
            if atom.element not in seen:
                seen.append(atom.element)
        return seen


def _infer_format(path: Path | None, explicit: str, text: str) -> str:
    normalized = explicit.lower().strip()
    if normalized and normalized != "auto":
        return normalized
    if path is not None:
        if path.name.upper() in {"POSCAR", "CONTCAR"}:
            return "poscar"
        suffix = path.suffix.lower()
        if suffix == ".xyz":
            return "xyz"
        if suffix == ".cif":
            return "cif"
        if suffix in {".data", ".lmp"}:
            return "lammps_data"
    head = text[:1000].lower()
    if "_cell_length_a" in head or "loop_" in head and "_atom_site" in head:
        return "cif"
    if "atoms" in head and "atom types" in head:
        return "lammps_data"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0].isdigit():
        return "xyz"
    return "poscar"


def _default_cell(atoms: list[AtomSite]) -> list[list[float]]:
    if not atoms:
        side = 10.0
    else:
        coords = [coord for atom in atoms for coord in (atom.x, atom.y, atom.z)]
        span = max(coords) - min(coords) if coords else 0.0
        side = max(10.0, span + 8.0)
    return [[side, 0.0, 0.0], [0.0, side, 0.0], [0.0, 0.0, side]]


def _parse_xyz(text: str) -> StructureModel:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("XYZ 内容为空。")
    atom_count = int(lines[0])
    atom_lines = lines[2 : 2 + atom_count] if len(lines) >= atom_count + 2 else lines[1 : 1 + atom_count]
    atoms: list[AtomSite] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        atoms.append(AtomSite(parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
    return StructureModel(atoms=atoms, cell=_default_cell(atoms), source_format="xyz")


def _parse_poscar(text: str) -> StructureModel:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 8:
        raise ValueError("POSCAR/CONTCAR 内容过短。")
    scale = float(lines[1].split()[0])
    cell = [[float(value) * scale for value in lines[index].split()[:3]] for index in range(2, 5)]
    elements = lines[5].split()
    counts = [int(value) for value in lines[6].split()]
    coord_line_index = 7
    if lines[coord_line_index].lower().startswith("s"):
        coord_line_index += 1
    mode = "direct" if lines[coord_line_index].lower().startswith("d") else "cartesian"
    cursor = coord_line_index + 1
    atoms: list[AtomSite] = []
    for element, count in zip(elements, counts):
        for _ in range(count):
            parts = lines[cursor].split()
            cursor += 1
            x, y, z = [float(value) for value in parts[:3]]
            if mode == "direct":
                x, y, z = _frac_to_cart([x, y, z], cell)
            atoms.append(AtomSite(element, x, y, z))
    return StructureModel(atoms=atoms, cell=cell, coordinate_mode="cartesian", source_format="poscar")


def _frac_to_cart(frac: list[float], cell: list[list[float]]) -> tuple[float, float, float]:
    return (
        frac[0] * cell[0][0] + frac[1] * cell[1][0] + frac[2] * cell[2][0],
        frac[0] * cell[0][1] + frac[1] * cell[1][1] + frac[2] * cell[2][1],
        frac[0] * cell[0][2] + frac[1] * cell[1][2] + frac[2] * cell[2][2],
    )


def _parse_cif(text: str) -> StructureModel:
    def number_for(key: str, default: float) -> float:
        match = re.search(rf"^{re.escape(key)}\s+([0-9.+\-Ee()]+)", text, flags=re.MULTILINE)
        if not match:
            return default
        return float(match.group(1).split("(")[0])

    a = number_for("_cell_length_a", 10.0)
    b = number_for("_cell_length_b", a)
    c = number_for("_cell_length_c", a)
    alpha = math.radians(number_for("_cell_angle_alpha", 90.0))
    beta = math.radians(number_for("_cell_angle_beta", 90.0))
    gamma = math.radians(number_for("_cell_angle_gamma", 90.0))
    cell = _cell_from_lengths_angles(a, b, c, alpha, beta, gamma)

    atoms: list[AtomSite] = []
    lines = [line.strip() for line in text.splitlines()]
    headers: list[str] = []
    for index, line in enumerate(lines):
        if line != "loop_":
            continue
        headers = []
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].startswith("_"):
            headers.append(lines[cursor])
            cursor += 1
        if not any("_atom_site_fract_x" in header for header in headers):
            continue
        header_map = {header: position for position, header in enumerate(headers)}
        element_idx = next((pos for key, pos in header_map.items() if key in {"_atom_site_type_symbol", "_atom_site_label"}), 0)
        fx_idx = next(pos for key, pos in header_map.items() if key == "_atom_site_fract_x")
        fy_idx = next(pos for key, pos in header_map.items() if key == "_atom_site_fract_y")
        fz_idx = next(pos for key, pos in header_map.items() if key == "_atom_site_fract_z")
        while cursor < len(lines) and lines[cursor] and not lines[cursor].startswith("_") and lines[cursor] != "loop_":
            parts = lines[cursor].split()
            cursor += 1
            if len(parts) <= max(element_idx, fx_idx, fy_idx, fz_idx):
                continue
            element = re.sub(r"[^A-Za-z]+", "", parts[element_idx]) or "X"
            frac = [float(parts[fx_idx].split("(")[0]), float(parts[fy_idx].split("(")[0]), float(parts[fz_idx].split("(")[0])]
            x, y, z = _frac_to_cart(frac, cell)
            atoms.append(AtomSite(element, x, y, z))
        break
    return StructureModel(atoms=atoms, cell=cell, coordinate_mode="cartesian", source_format="cif")


def _cell_from_lengths_angles(a: float, b: float, c: float, alpha: float, beta: float, gamma: float) -> list[list[float]]:
    ax, ay, az = a, 0.0, 0.0
    bx, by, bz = b * math.cos(gamma), b * math.sin(gamma), 0.0
    cx = c * math.cos(beta)
    cy = c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / max(math.sin(gamma), 1e-12)
    cz = math.sqrt(max(c * c - cx * cx - cy * cy, 0.0))
    return [[ax, ay, az], [bx, by, bz], [cx, cy, cz]]


def _parse_lammps_data(text: str) -> StructureModel:
    lines = text.splitlines()
    atom_count = 0
    xlo = ylo = zlo = 0.0
    xhi = yhi = zhi = 10.0
    for line in lines[:80]:
        if match := re.match(r"\s*(\d+)\s+atoms", line):
            atom_count = int(match.group(1))
        if "xlo xhi" in line:
            xlo, xhi = [float(value) for value in line.split()[:2]]
        if "ylo yhi" in line:
            ylo, yhi = [float(value) for value in line.split()[:2]]
        if "zlo zhi" in line:
            zlo, zhi = [float(value) for value in line.split()[:2]]
    atoms: list[AtomSite] = []
    in_atoms = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("atoms"):
            in_atoms = True
            continue
        if in_atoms and not stripped:
            continue
        if in_atoms and re.match(r"^[A-Za-z]", stripped):
            break
        if in_atoms:
            parts = stripped.split()
            if len(parts) >= 5 and parts[0].isdigit():
                # atom-ID atom-type x y z, or atom-ID mol atom-type q x y z.
                if len(parts) >= 7:
                    atom_type, x, y, z = parts[2], parts[-3], parts[-2], parts[-1]
                else:
                    atom_type, x, y, z = parts[1], parts[2], parts[3], parts[4]
                atoms.append(AtomSite(f"T{atom_type}", float(x), float(y), float(z)))
            if atom_count and len(atoms) >= atom_count:
                break
    cell = [[xhi - xlo, 0.0, 0.0], [0.0, yhi - ylo, 0.0], [0.0, 0.0, zhi - zlo]]
    return StructureModel(atoms=atoms, cell=cell, source_format="lammps_data")


def _format_xyz(structure: StructureModel) -> str:
    lines = [str(len(structure.atoms)), f"converted_from={structure.source_format}"]
    lines.extend(f"{atom.element} {atom.x:.8f} {atom.y:.8f} {atom.z:.8f}" for atom in structure.atoms)
    return "\n".join(lines) + "\n"


def _format_poscar(structure: StructureModel) -> str:
    elements = structure.elements
    counts = [sum(1 for atom in structure.atoms if atom.element == element) for element in elements]
    lines = ["MatterPilot converted structure", "1.0"]
    cell = structure.cell or _default_cell(structure.atoms)
    lines.extend("  " + "  ".join(f"{value:.10f}" for value in vector) for vector in cell)
    lines.append("  " + "  ".join(elements))
    lines.append("  " + "  ".join(str(count) for count in counts))
    lines.append("Cartesian")
    for element in elements:
        for atom in structure.atoms:
            if atom.element == element:
                lines.append(f"  {atom.x:.10f}  {atom.y:.10f}  {atom.z:.10f}")
    return "\n".join(lines) + "\n"


def _format_lammps_data(structure: StructureModel) -> str:
    elements = structure.elements
    type_by_element = {element: index + 1 for index, element in enumerate(elements)}
    cell = structure.cell or _default_cell(structure.atoms)
    xhi = max(cell[0][0], 1.0)
    yhi = max(cell[1][1], 1.0)
    zhi = max(cell[2][2], 1.0)
    lines = [
        "LAMMPS data file converted by MatterPilot",
        "",
        f"{len(structure.atoms)} atoms",
        f"{len(elements)} atom types",
        "",
        f"0.0 {xhi:.10f} xlo xhi",
        f"0.0 {yhi:.10f} ylo yhi",
        f"0.0 {zhi:.10f} zlo zhi",
        "",
        "Masses",
        "",
    ]
    lines.extend(f"{type_by_element[element]} 1.0 # {element}" for element in elements)
    lines.extend(["", "Atoms # atomic", ""])
    for index, atom in enumerate(structure.atoms, start=1):
        lines.append(f"{index} {type_by_element[atom.element]} {atom.x:.10f} {atom.y:.10f} {atom.z:.10f}")
    return "\n".join(lines) + "\n"


def _format_cif(structure: StructureModel) -> str:
    cell = structure.cell or _default_cell(structure.atoms)
    a = max(cell[0][0], 1.0)
    b = max(cell[1][1], 1.0)
    c = max(cell[2][2], 1.0)
    lines = [
        "data_matterpilot_converted",
        f"_cell_length_a {a:.8f}",
        f"_cell_length_b {b:.8f}",
        f"_cell_length_c {c:.8f}",
        "_cell_angle_alpha 90",
        "_cell_angle_beta 90",
        "_cell_angle_gamma 90",
        "loop_",
        "_atom_site_label",
        "_atom_site_type_symbol",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
    ]
    for index, atom in enumerate(structure.atoms, start=1):
        lines.append(f"{atom.element}{index} {atom.element} {atom.x / a:.8f} {atom.y / b:.8f} {atom.z / c:.8f}")
    return "\n".join(lines) + "\n"


def _load_structure_text(arguments: dict[str, Any], context: ToolContext) -> tuple[str, Path | None]:
    if text := str(arguments.get("text") or "").strip():
        return text, None
    if path_value := str(arguments.get("path") or "").strip():
        path = _resolve_safe_path(path_value, context)
        return path.read_text(encoding="utf-8", errors="replace"), path
    assets = context.uploaded_assets
    if not assets:
        raise ValueError("structure.convert 需要 path、text 或上传结构文件。")
    asset = assets[0]
    return _decode_data_url(asset.data_url).decode("utf-8", errors="replace"), Path(asset.name or "uploaded_structure")


def _structure_convert(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    text, path = _load_structure_text(arguments, context)
    source_format = _infer_format(path, str(arguments.get("source_format") or "auto"), text)
    target_format = str(arguments.get("target_format") or "lammps_data").lower().strip()
    parser = {
        "xyz": _parse_xyz,
        "poscar": _parse_poscar,
        "cif": _parse_cif,
        "lammps_data": _parse_lammps_data,
    }.get(source_format)
    if parser is None:
        raise ValueError(f"暂不支持源结构格式：{source_format}")
    structure = parser(text)
    if not structure.atoms:
        raise ValueError("没有从结构文件中解析到原子坐标。")

    formatter = {
        "xyz": _format_xyz,
        "poscar": _format_poscar,
        "cif": _format_cif,
        "lammps_data": _format_lammps_data,
    }.get(target_format)
    if formatter is None:
        raise ValueError(f"暂不支持目标结构格式：{target_format}")
    converted = formatter(structure)
    extension = {"xyz": "xyz", "poscar": "vasp", "cif": "cif", "lammps_data": "data"}[target_format]
    artifact_name = f"converted_structure.{extension}"
    output_path = context.artifact_service.get_artifact_path(context.run_id, artifact_name)
    output_path.write_text(converted, encoding="utf-8")
    artifact = context.artifact_service.build_artifact_ref(
        kind="text",
        name=artifact_name,
        path=output_path,
        url=context.artifact_service.build_artifact_url(context.run_id, artifact_name),
        metadata={"source_format": source_format, "target_format": target_format, "atom_count": len(structure.atoms)},
    )
    return ToolResult(
        tool_name="structure.convert",
        success=True,
        summary=f"已将结构从 {source_format} 转换为 {target_format}，共 {len(structure.atoms)} 个原子。",
        output={
            "source_format": source_format,
            "target_format": target_format,
            "atom_count": len(structure.atoms),
            "elements": structure.elements,
            "artifact": artifact.model_dump(mode="json"),
            "preview": converted[:4000],
        },
        artifacts=[artifact],
    )


def register_structure_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="structure.convert",
            description="Convert lightweight materials structures among CIF, POSCAR/CONTCAR, XYZ, and simple LAMMPS data formats, writing the converted artifact into the run directory.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                    "source_format": {"type": "string", "enum": ["auto", "cif", "poscar", "xyz", "lammps_data"]},
                    "target_format": {"type": "string", "enum": ["cif", "poscar", "xyz", "lammps_data"]},
                },
            },
            risk=ToolRisk.WRITE_ARTIFACT,
            read_only=False,
            output_kind="artifact",
        ),
        _structure_convert,
    )
