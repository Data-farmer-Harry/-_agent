from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any, Callable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter

from app.core.cancellation import RunCancelledError, is_cancelled
from app.core.sandbox import SandboxLimits, get_sandbox_runner
from app.lammps.config import detect_ovito_backend
from app.utils.path_utils import write_json_file


_OVITO_RENDER_LOCK = Lock()


def resolve_dump_path(output_dir: Path, dump_file_name: str | None = None) -> Path:
    requested_name = (dump_file_name or "").strip()
    candidates: list[Path] = []
    if requested_name:
        candidates.append(output_dir / requested_name)
    candidates.extend(
        [
            output_dir / "dump.atom",
            output_dir / "trajectory.lammpstrj",
            output_dir / "dump.lammpstrj",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def convert_dump(output_dir: Path, dump_file_name: str | None = None) -> Path:
    dump_path = resolve_dump_path(output_dir, dump_file_name)
    summary = {
        "has_dump": dump_path.exists(),
        "dump_file": dump_path.name,
        "atom_count": 4 if dump_path.exists() else 0,
        "notes": "Basic dump summary for the demo pipeline.",
    }
    converted_path = output_dir / "structure_summary.json"
    converted_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return converted_path


def generate_plot(output_dir: Path) -> Path:
    thermo_path = output_dir / "thermo.csv"
    steps: list[float] = []
    temperatures: list[float] = []
    energy: list[float] = []
    with thermo_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            steps.append(float(row["step"]))
            temperatures.append(float(row["temp"]))
            energy.append(float(row["etotal"]))

    fig, ax1 = plt.subplots(figsize=(7, 4.5))
    fig.patch.set_facecolor("#f6f1e8")
    ax1.set_facecolor("#fffdf7")
    ax1.plot(steps, temperatures, color="#0b5d4d", linewidth=2.2, label="Temperature (K)")
    ax1.set_xlabel("Step")
    ax1.set_ylabel("Temperature (K)", color="#0b5d4d")
    ax1.tick_params(axis="y", labelcolor="#0b5d4d")

    ax2 = ax1.twinx()
    ax2.plot(steps, energy, color="#b04a1f", linewidth=2.0, linestyle="--", label="Total Energy")
    ax2.set_ylabel("Total Energy", color="#b04a1f")
    ax2.tick_params(axis="y", labelcolor="#b04a1f")

    ax1.set_title("MD Agent Thermo Overview")
    ax1.grid(alpha=0.25)
    plot_path = output_dir / "plot.png"
    plt.tight_layout()
    plt.savefig(plot_path, dpi=140)
    plt.close(fig)
    return plot_path


def generate_diffusion_trajectory_if_applicable(
    output_dir: Path,
    request: dict[str, Any],
    mode: str,
    run_id: str | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    status = {
        "supported_task": False,
        "generated": False,
        "reason": "",
        "backend": "not checked",
    }

    if mode != "real":
        status["reason"] = "mock mode: diffusion trajectory skipped"
        return status

    material = str(request.get("material") or "").strip() or "Unknown"
    task_type = str(request.get("task_type") or "").strip()

    if not (material in {"Al", "Cu", "Ni"} and task_type == "heating"):
        status["reason"] = "current task does not generate diffusion trajectory"
        return status

    status["supported_task"] = True
    ovito_status = detect_ovito_backend()
    status["backend"] = str(ovito_status["ovito_backend"])
    if not ovito_status["ovito_available"]:
        status["reason"] = "OVITO not installed or not detected"
        return status

    dump_path = resolve_dump_path(output_dir, str(request.get("dump_file") or ""))
    if not dump_path.exists():
        status["reason"] = f"{dump_path.name} not found"
        return status

    try:
        with _OVITO_RENDER_LOCK:
            if progress_callback:
                progress_callback("rendering_diffusion_preview", 72)
            if ovito_status["ovito_backend"] == "python module":
                image_path, animation_path, video_path, metadata = _render_with_python_subprocess(dump_path, output_dir, run_id, material)
            else:
                image_path, animation_path, video_path, metadata = _render_with_executable(
                    dump_path,
                    output_dir,
                    str(ovito_status.get("ovito_location") or ovito_status["ovito_backend"]),
                    run_id,
                    material,
                )
    except Exception as exc:  # noqa: BLE001
        status["reason"] = f"OVITO render failed: {exc}"
        return status

    status["generated"] = True
    status["reason"] = "diffusion trajectory image and 3D animation generated"
    status["dump_path"] = str(dump_path)
    status["image_path"] = str(image_path)
    status["animation_path"] = str(animation_path)
    status["video_path"] = str(video_path)
    status["metadata_path"] = str(metadata)
    return status


def _render_with_python_subprocess(dump_path: Path, output_dir: Path, run_id: str | None, material: str) -> tuple[Path, Path, Path, Path]:
    json_path = output_dir / "diffusion_lines.json"
    image_path = output_dir / "diffusion_trajectory.png"
    animation_path = output_dir / "diffusion_trajectory_3d.gif"
    video_path = output_dir / "ovito.mp4"
    metadata_path = output_dir / "diffusion_metadata.json"
    script = _ovito_script()
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(script)
        script_path = Path(handle.name)

    try:
        cmd = [sys.executable, str(script_path), str(dump_path), str(json_path), str(video_path)]
        proc = get_sandbox_runner().popen(
            cmd,
            cwd=output_dir,
            allow_network=False,
            read_roots=(script_path, dump_path),
            write_roots=(output_dir,),
            limits=SandboxLimits(timeout_seconds=600, cpu_seconds=600, memory_mb=12_288, max_file_size_mb=2_048),
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
        if cancelled:
            raise RunCancelledError("Simulation cancelled by user")
        stdout, stderr = proc.communicate()
        if proc.timed_out:
            raise RuntimeError("OVITO Python rendering exceeded the sandbox timeout.")
        if proc.returncode != 0:
            raise RuntimeError(stderr.strip() or stdout.strip() or "unknown OVITO python-module error")
        if not json_path.exists():
            raise RuntimeError(
                "OVITO python-module subprocess finished without producing diffusion_lines.json"
                + (f"; stdout={stdout.strip()}" if stdout.strip() else "")
                + (f"; stderr={stderr.strip()}" if stderr.strip() else "")
            )

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        positions = np.asarray(payload["positions"])
        particle_ids = np.asarray(payload["particle_ids"])
        selected_ids = payload["selected_particle_ids"]
        frame_count = int(payload["frame_count"])
        _render_trajectory_plot(positions, particle_ids, image_path, material)
        if run_id and is_cancelled(run_id):
            raise RunCancelledError("Simulation cancelled by user")
        _render_trajectory_animation(positions, particle_ids, animation_path, material, run_id)
        if run_id and is_cancelled(run_id):
            raise RunCancelledError("Simulation cancelled by user")
        _ensure_browser_friendly_mp4(video_path, run_id)
        write_json_file(
            metadata_path,
            {
                "backend": "python module subprocess",
                "selected_particle_ids": selected_ids,
                "frame_count": frame_count,
                "output": str(image_path),
                "animation_output": str(animation_path),
                "video_output": str(video_path),
                "projection": "xy",
                "animation_type": "3d-gif + ovito-mp4",
            },
        )
    finally:
        script_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)

    return image_path, animation_path, video_path, metadata_path


def _render_with_executable(
    dump_path: Path,
    output_dir: Path,
    backend: str,
    run_id: str | None,
    material: str,
) -> tuple[Path, Path, Path, Path]:
    executable = backend if Path(backend).exists() else shutil.which(backend)
    if not executable:
        raise RuntimeError(f"Could not resolve OVITO executable: {backend}")

    script = _ovito_script()
    json_path = output_dir / "diffusion_lines.json"
    image_path = output_dir / "diffusion_trajectory.png"
    animation_path = output_dir / "diffusion_trajectory_3d.gif"
    video_path = output_dir / "ovito.mp4"
    metadata_path = output_dir / "diffusion_metadata.json"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(script)
        script_path = Path(handle.name)

    try:
        cmd = [
            executable,
            "--nogui",
            "--script",
            str(script_path),
            "--",
            str(dump_path),
            str(json_path),
            str(video_path),
        ]
        proc = get_sandbox_runner().popen(
            cmd,
            cwd=output_dir,
            allow_network=False,
            read_roots=(script_path, dump_path),
            write_roots=(output_dir,),
            limits=SandboxLimits(timeout_seconds=600, cpu_seconds=600, memory_mb=12_288, max_file_size_mb=2_048),
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
        if cancelled:
            raise RunCancelledError("Simulation cancelled by user")
        stdout, stderr = proc.communicate()
        if proc.timed_out:
            raise RuntimeError("OVITO rendering exceeded the sandbox timeout.")
        if proc.returncode != 0:
            raise RuntimeError(stderr.strip() or stdout.strip() or "unknown OVITO error")
        if not json_path.exists():
            raise RuntimeError(
                "OVITO script finished without producing diffusion_lines.json"
                + (f"; stdout={stdout.strip()}" if stdout.strip() else "")
                + (f"; stderr={stderr.strip()}" if stderr.strip() else "")
            )

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        positions = payload["positions"]
        particle_ids = payload["particle_ids"]
        selected_ids = payload["selected_particle_ids"]
        frame_count = payload["frame_count"]
        _render_trajectory_plot(positions, particle_ids, image_path, material)
        _render_trajectory_animation(positions, particle_ids, animation_path, material, run_id)
        _ensure_browser_friendly_mp4(video_path, run_id)
        write_json_file(
            metadata_path,
            {
                "backend": "ovito executable",
                "selected_particle_ids": selected_ids,
                "frame_count": frame_count,
                "output": str(image_path),
                "animation_output": str(animation_path),
                "video_output": str(video_path),
                "projection": "xy",
                "animation_type": "3d-gif + ovito-mp4",
            },
        )
    finally:
        script_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)

    return image_path, animation_path, video_path, metadata_path


def _extract_trajectory_lines_with_ovito(import_file, dump_path: Path):
    from ovito.modifiers import ExpressionSelectionModifier, GenerateTrajectoryLinesModifier

    pipeline = import_file(str(dump_path), multiple_frames=True)
    data = pipeline.compute(0)
    identifiers = list(data.particles["Particle Identifier"])
    selected_ids = _sample_ids(identifiers, target_count=24)
    if not selected_ids:
        raise RuntimeError("No particle identifiers available for rendering.")

    selection_expr = " || ".join(f"ParticleIdentifier=={pid}" for pid in selected_ids)
    pipeline.modifiers.append(ExpressionSelectionModifier(expression=selection_expr))
    pipeline.modifiers.append(
        GenerateTrajectoryLinesModifier(
            only_selected=True,
            unwrap_trajectories=True,
            sampling_enabled=True,
            sample_particle_property="Particle Identifier",
        )
    )
    out = pipeline.compute()
    if not out.lines:
        raise RuntimeError("OVITO did not generate trajectory line data.")
    lines = list(out.lines.values())[0]
    positions = lines["Position"][...]
    particle_ids = lines["Particle Identifier"][...]
    return positions, particle_ids, selected_ids, pipeline.source.num_frames


def _sample_ids(identifiers: list[int], target_count: int = 24) -> list[int]:
    if not identifiers:
        return []
    identifiers = sorted(int(i) for i in identifiers)
    if len(identifiers) <= target_count:
        return identifiers
    step = max(1, len(identifiers) // target_count)
    return identifiers[::step][:target_count]


def _ovito_script() -> str:
    return r"""
import json
import sys
from ovito.io import import_file
from ovito.modifiers import AssignColorModifier, ExpressionSelectionModifier, GenerateTrajectoryLinesModifier
from ovito.vis import TachyonRenderer, Viewport

dump_path, json_path, video_path = sys.argv[1:4]
pipeline = import_file(dump_path, multiple_frames=True)
data = pipeline.compute(0)
ids = sorted(int(i) for i in list(data.particles['Particle Identifier']))
target_count = 24
step = max(1, len(ids) // target_count) if ids else 1
selected = ids[::step][:target_count]
expr = " || ".join(f"ParticleIdentifier=={pid}" for pid in selected)
pipeline.modifiers.append(ExpressionSelectionModifier(expression=expr))
pipeline.modifiers.append(GenerateTrajectoryLinesModifier(
    only_selected=True,
    unwrap_trajectories=True,
    sampling_enabled=True,
    sample_particle_property='Particle Identifier',
))
out = pipeline.compute()
lines = list(out.lines.values())[0]
with open(json_path, 'w', encoding='utf-8') as handle:
    json.dump({
        'positions': lines['Position'][...].tolist(),
        'particle_ids': lines['Particle Identifier'][...].tolist(),
        'selected_particle_ids': selected,
        'frame_count': pipeline.source.num_frames,
    }, handle)

scene = pipeline.source.data
pipeline.modifiers.append(AssignColorModifier(color=(0.92, 0.41, 0.41)))
scene.particles.vis.radius = 1.22
scene.cell.vis.enabled = True
scene.cell.vis.render_cell = True
scene.cell.vis.line_width = 0.08
scene.cell.vis.rendering_color = (0.12, 0.12, 0.12)
pipeline.add_to_scene()
try:
    vp = Viewport(type=Viewport.Type.Perspective)
    vp.camera_dir = (-0.88, -0.52, -0.75)
    vp.camera_up = (0.0, 1.0, 0.0)
    vp.zoom_all()
    vp.render_anim(
        filename=video_path,
        size=(900, 900),
        fps=12,
        background=(1.0, 1.0, 1.0),
        renderer=TachyonRenderer(),
        range=(0, pipeline.source.num_frames - 1),
        every_nth=max(1, pipeline.source.num_frames // 48),
    )
finally:
    pipeline.remove_from_scene()
"""


def _render_trajectory_plot(positions, particle_ids, image_path: Path, material: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 7.2))
    fig.patch.set_facecolor("#f5f1e8")
    ax.set_facecolor("#fffdf7")
    unique_ids = sorted(set(int(i) for i in particle_ids))
    cmap = plt.get_cmap("viridis", len(unique_ids))
    for idx, pid in enumerate(unique_ids):
        mask = particle_ids == pid
        pts = positions[mask]
        ax.plot(pts[:, 0], pts[:, 1], color=cmap(idx), linewidth=1.6, alpha=0.88)
        ax.scatter(pts[0, 0], pts[0, 1], color=cmap(idx), s=14, alpha=0.55)
        ax.scatter(pts[-1, 0], pts[-1, 1], color=cmap(idx), s=26)
    ax.set_title(f"{material} Heating Diffusion Trajectories")
    ax.set_xlabel("x (Angstrom)")
    ax.set_ylabel("y (Angstrom)")
    ax.grid(alpha=0.22)
    ax.set_aspect("equal", adjustable="box")
    plt.tight_layout()
    plt.savefig(image_path, dpi=160)
    plt.close(fig)


def _render_trajectory_animation(
    positions,
    particle_ids,
    animation_path: Path,
    material: str,
    run_id: str | None = None,
) -> None:
    trajectories = _build_trajectories(positions, particle_ids)
    if not trajectories:
        raise RuntimeError("No trajectory data available for animation rendering.")
    max_points = max(len(points) for points in trajectories.values())
    frame_steps = _frame_steps(max_points)
    all_points = np.concatenate(list(trajectories.values()), axis=0)
    mins = all_points.min(axis=0)
    maxs = all_points.max(axis=0)
    spans = np.maximum(maxs - mins, 1.0)
    padding = spans * 0.08

    fig = plt.figure(figsize=(7.5, 7.5))
    ax = fig.add_subplot(111, projection="3d")
    fig.patch.set_facecolor("#f5f1e8")
    ax.set_facecolor("#fffdf7")
    ax.set_xlim(mins[0] - padding[0], maxs[0] + padding[0])
    ax.set_ylim(mins[1] - padding[1], maxs[1] + padding[1])
    ax.set_zlim(mins[2] - padding[2], maxs[2] + padding[2])
    ax.set_xlabel("x (Angstrom)")
    ax.set_ylabel("y (Angstrom)")
    ax.set_zlabel("z (Angstrom)")
    cmap = plt.get_cmap("viridis", len(trajectories))
    lines = []
    points = []
    for idx, (pid, _) in enumerate(trajectories.items()):
        color = cmap(idx)
        line, = ax.plot([], [], [], color=color, linewidth=1.4, alpha=0.8)
        point = ax.scatter([], [], [], color=[color], s=28)
        lines.append((pid, line))
        points.append((pid, point))

    def update(frame_idx: int):
        if run_id and is_cancelled(run_id):
            raise RunCancelledError("Simulation cancelled by user")
        step = frame_steps[frame_idx]
        artists = []
        for pid, line in lines:
            pts = trajectories[pid][: step + 1]
            line.set_data(pts[:, 0], pts[:, 1])
            line.set_3d_properties(pts[:, 2])
            artists.append(line)
        for pid, point in points:
            latest = trajectories[pid][step]
            point._offsets3d = ([latest[0]], [latest[1]], [latest[2]])
            artists.append(point)
        ax.set_title(f"{material} Heating Trajectory Frame {frame_idx + 1}/{len(frame_steps)}")
        return artists

    anim = FuncAnimation(fig, update, frames=len(frame_steps), interval=120, blit=False)
    writer = PillowWriter(fps=10)
    anim.save(animation_path, writer=writer)
    plt.close(fig)


def _build_trajectories(positions, particle_ids):
    trajectories: dict[int, np.ndarray] = {}
    for pid in sorted(set(int(i) for i in particle_ids)):
        mask = particle_ids == pid
        trajectories[pid] = positions[mask]
    return trajectories


def _frame_steps(max_points: int) -> list[int]:
    if max_points <= 1:
        return [0]
    frame_count = min(max_points, 48)
    step_positions = np.linspace(0, max_points - 1, frame_count).astype(int)
    return sorted(set(step_positions.tolist()))


def _render_ovito_video_with_python_module(import_file, dump_path: Path, video_path: Path) -> None:
    from ovito.modifiers import AssignColorModifier
    from ovito.vis import TachyonRenderer, Viewport

    pipeline = import_file(str(dump_path), multiple_frames=True)
    scene = pipeline.source.data
    pipeline.modifiers.append(AssignColorModifier(color=(0.92, 0.41, 0.41)))
    scene.particles.vis.radius = 1.22
    scene.cell.vis.enabled = True
    scene.cell.vis.render_cell = True
    scene.cell.vis.line_width = 0.08
    scene.cell.vis.rendering_color = (0.12, 0.12, 0.12)
    pipeline.add_to_scene()
    try:
        vp = Viewport(type=Viewport.Type.Perspective)
        vp.camera_dir = (-0.88, -0.52, -0.75)
        vp.camera_up = (0.0, 1.0, 0.0)
        vp.zoom_all()
        vp.render_anim(
            filename=str(video_path),
            size=(900, 900),
            fps=12,
            background=(1.0, 1.0, 1.0),
            renderer=TachyonRenderer(),
            range=(0, pipeline.source.num_frames - 1),
            every_nth=max(1, pipeline.source.num_frames // 48),
        )
    finally:
        pipeline.remove_from_scene()


def _ensure_browser_friendly_mp4(video_path: Path, run_id: str | None = None) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path.exists():
        return
    temp_path = video_path.with_name(video_path.stem + "_browser.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(temp_path),
    ]
    proc = get_sandbox_runner().popen(
        cmd,
        cwd=video_path.parent,
        allow_network=False,
        read_roots=(video_path,),
        write_roots=(video_path.parent,),
        limits=SandboxLimits(timeout_seconds=300, cpu_seconds=300, memory_mb=4_096, max_file_size_mb=2_048),
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
    if cancelled:
        raise RunCancelledError("Simulation cancelled by user")
    proc.communicate()
    if proc.timed_out:
        raise RuntimeError("FFmpeg transcoding exceeded the sandbox timeout.")
    if proc.returncode == 0 and temp_path.exists():
        temp_path.replace(video_path)
