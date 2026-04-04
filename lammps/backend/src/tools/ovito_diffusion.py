from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from src.config.supervisor_config import detect_ovito_backend
from src.utils.path_utils import write_json
from src.utils.cancellation import is_cancelled, SimulationCancelledError
import time

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter


def generate_diffusion_trajectory_if_applicable(
    output_dir: Path,
    request: Dict[str, Any],
    mode: str,
    run_id: str | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
) -> Dict[str, Any]:
    status = {
        "supported_task": False,
        "generated": False,
        "reason": "",
        "backend": "not checked",
    }

    if mode != "real":
        status["reason"] = "mock mode: diffusion trajectory skipped"
        return status

    if not (request.get("material") == "Cu" and request.get("task_type") == "heating"):
        status["reason"] = "current task does not generate diffusion trajectory"
        return status

    status["supported_task"] = True
    ovito_status = detect_ovito_backend()
    status["backend"] = str(ovito_status["ovito_backend"])
    if not ovito_status["ovito_available"]:
        status["reason"] = "OVITO not installed or not detected"
        return status

    dump_path = output_dir / "dump.atom"
    if not dump_path.exists():
        status["reason"] = "dump.atom not found"
        return status

    try:
        if progress_callback:
            progress_callback("rendering_diffusion_preview", 72)
        if ovito_status["ovito_backend"] == "python module":
            image_path, animation_path, video_path, metadata = _render_with_python_module(dump_path, output_dir, run_id)
        else:
            image_path, animation_path, video_path, metadata = _render_with_executable(
                dump_path,
                output_dir,
                str(ovito_status.get("ovito_location") or ovito_status["ovito_backend"]),
                run_id
            )
    except Exception as exc:
        status["reason"] = f"OVITO render failed: {exc}"
        return status

    status["generated"] = True
    status["reason"] = "diffusion trajectory image and 3D animation generated"
    status["image_path"] = str(image_path)
    status["animation_path"] = str(animation_path)
    status["video_path"] = str(video_path)
    status["metadata_path"] = str(metadata)
    return status


def _render_with_python_module(dump_path: Path, output_dir: Path, run_id: str | None) -> Tuple[Path, Path, Path, Path]:
    from ovito.io import import_file

    positions, particle_ids, selected_ids, frame_count = _extract_trajectory_lines_with_ovito(import_file, dump_path)
    if run_id and is_cancelled(run_id): raise SimulationCancelledError("Simulation cancelled by user")
    
    image_path = output_dir / "diffusion_trajectory.png"
    animation_path = output_dir / "diffusion_trajectory_3d.gif"
    video_path = output_dir / "ovito.mp4"
    _render_trajectory_plot(positions, particle_ids, image_path)
    if run_id and is_cancelled(run_id): raise SimulationCancelledError("Simulation cancelled by user")
    
    _render_trajectory_animation(positions, particle_ids, animation_path, run_id)
    if run_id and is_cancelled(run_id): raise SimulationCancelledError("Simulation cancelled by user")
    
    _render_ovito_video_with_python_module(import_file, dump_path, video_path)
    if run_id and is_cancelled(run_id): raise SimulationCancelledError("Simulation cancelled by user")
    
    _ensure_browser_friendly_mp4(video_path, run_id)

    metadata = {
        "backend": "python module",
        "selected_particle_ids": selected_ids,
        "frame_count": frame_count,
        "output": str(image_path),
        "animation_output": str(animation_path),
        "video_output": str(video_path),
        "projection": "xy",
        "animation_type": "3d-gif + ovito-mp4",
    }
    metadata_path = output_dir / "diffusion_metadata.json"
    write_json(metadata_path, metadata)
    return image_path, animation_path, video_path, metadata_path


def _render_with_executable(dump_path: Path, output_dir: Path, backend: str, run_id: str | None) -> Tuple[Path, Path, Path, Path]:
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
        cmd = [executable, "--nogui", str(script_path), str(dump_path), str(json_path), str(video_path)]
        proc = _spawn_captured_process(cmd)
        
        cancelled = False
        while proc.poll() is None:
            if run_id and is_cancelled(run_id):
                proc.terminate()
                cancelled = True
                break
            time.sleep(0.5)
            
        if cancelled:
            raise SimulationCancelledError("Simulation cancelled by user")
            
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.strip() or stdout.strip() or "unknown OVITO error")
            
        import json

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        positions = payload["positions"]
        particle_ids = payload["particle_ids"]
        selected_ids = payload["selected_particle_ids"]
        frame_count = payload["frame_count"]
        _render_trajectory_plot(positions, particle_ids, image_path)
        if run_id and is_cancelled(run_id): raise SimulationCancelledError("Simulation cancelled by user")
        
        _render_trajectory_animation(positions, particle_ids, animation_path, run_id)
        if run_id and is_cancelled(run_id): raise SimulationCancelledError("Simulation cancelled by user")
        
        _ensure_browser_friendly_mp4(video_path, run_id)
        write_json(
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


def _sample_ids(identifiers: List[int], target_count: int = 24) -> List[int]:
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


def _render_trajectory_plot(positions, particle_ids, image_path: Path) -> None:
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
    ax.set_title("Cu Heating Diffusion Trajectories")
    ax.set_xlabel("x (Angstrom)")
    ax.set_ylabel("y (Angstrom)")
    ax.grid(alpha=0.22)
    ax.set_aspect("equal", adjustable="box")
    plt.tight_layout()
    plt.savefig(image_path, dpi=160)
    plt.close(fig)


def _render_trajectory_animation(positions, particle_ids, animation_path: Path, run_id: str | None = None) -> None:
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

    fig = plt.figure(figsize=(8.8, 7.4))
    fig.patch.set_facecolor("#f5f1e8")
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("#fffdf7")
    ax.set_title("Cu Heating Diffusion Trajectory (3D)")
    ax.set_xlabel("x (Angstrom)")
    ax.set_ylabel("y (Angstrom)")
    ax.set_zlabel("z (Angstrom)")
    ax.set_xlim(mins[0] - padding[0], maxs[0] + padding[0])
    ax.set_ylim(mins[1] - padding[1], maxs[1] + padding[1])
    ax.set_zlim(mins[2] - padding[2], maxs[2] + padding[2])
    ax.view_init(elev=26, azim=38)

    identifiers = sorted(trajectories)
    cmap = plt.get_cmap("viridis", len(identifiers))
    lines = []
    for idx, pid in enumerate(identifiers):
        (line,) = ax.plot([], [], [], color=cmap(idx), linewidth=1.6, alpha=0.9)
        lines.append((pid, line))

    def update(frame_index: int):
        if run_id and is_cancelled(run_id):
            raise SimulationCancelledError("Simulation cancelled by user")
        step = frame_steps[frame_index]
        for pid, line in lines:
            pts = trajectories[pid]
            visible = pts[:step]
            line.set_data(visible[:, 0], visible[:, 1])
            line.set_3d_properties(visible[:, 2])
        ax.set_title(f"Cu Heating Diffusion Trajectory (3D) | frame {frame_index + 1}/{len(frame_steps)}")
        return [line for _, line in lines]

    animation = FuncAnimation(
        fig,
        update,
        frames=len(frame_steps),
        interval=120,
        blit=False,
        repeat=True,
    )
    animation.save(animation_path, writer=PillowWriter(fps=8))
    plt.close(fig)


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
        viewport = Viewport(type=Viewport.Type.Perspective)
        viewport.camera_dir = (-0.88, -0.52, -0.75)
        viewport.camera_up = (0.0, 1.0, 0.0)
        viewport.zoom_all()
        viewport.render_anim(
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
    temp_path = video_path.with_name(f"{video_path.stem}.h264.mp4")
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
    
    proc = _spawn_captured_process(cmd)
    cancelled = False
    while proc.poll() is None:
        if run_id and is_cancelled(run_id):
            proc.terminate()
            cancelled = True
            break
        time.sleep(0.5)
        
    if cancelled:
        temp_path.unlink(missing_ok=True)
        raise SimulationCancelledError("Simulation cancelled by user")

    stdout, stderr = proc.communicate()
    if proc.returncode != 0 or not temp_path.exists():
        temp_path.unlink(missing_ok=True)
        return
    temp_path.replace(video_path)


def _spawn_captured_process(cmd: List[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _build_trajectories(positions, particle_ids) -> Dict[int, np.ndarray]:
    positions = np.asarray(positions)
    particle_ids = np.asarray(particle_ids)
    trajectories: Dict[int, np.ndarray] = {}
    for pid in sorted(set(int(i) for i in particle_ids)):
        mask = particle_ids == pid
        pts = np.asarray(positions[mask], dtype=float)
        if len(pts):
            trajectories[pid] = pts
    return trajectories


def _frame_steps(max_points: int, target_frames: int = 48) -> List[int]:
    if max_points <= 1:
        return [1]
    frame_count = min(target_frames, max_points)
    indices = np.linspace(1, max_points, num=frame_count, dtype=int)
    steps = []
    for idx in indices.tolist():
        if not steps or idx != steps[-1]:
            steps.append(idx)
    return steps
