from __future__ import annotations

from pathlib import Path
from typing import Dict

from src.config.supervisor_config import SupervisorConfig, load_supervisor_config
from src.schemas.state import AgentState
from src.tools.dump_convert import convert_dump
from src.tools.generate_lammps_in import generate_lammps_input
from src.tools.lammps_run import run_lammps, run_mock
from src.tools.ovito_diffusion import generate_diffusion_trajectory_if_applicable
from src.tools.visualization import generate_plot
from src.utils.cancellation import SimulationCancelledError, is_cancelled
from src.utils.path_utils import write_json


class MDAgent:
    def __init__(self, config: SupervisorConfig | None = None) -> None:
        self.config = config

    def run(self, state: AgentState, output_dir: Path) -> AgentState:
        config = self.config or load_supervisor_config()
        self._write_progress(output_dir, state, "preparing_input", 10)
        
        def _check_cancel():
            if state.run_id and is_cancelled(state.run_id):
                raise SimulationCancelledError("Simulation cancelled by user")

        try:
            _check_cancel()
            request_payload = dict(state.normalized_request)
            request_path = output_dir / "request.json"
            write_json(
                request_path,
                {
                    "original_query": state.user_query,
                    "normalized_request": state.normalized_request,
                },
            )
            input_path = generate_lammps_input(
                request_payload,
                output_dir,
                potentials_dir=config.potentials_dir,
            )

            summary: Dict[str, float]
            mode = "real"
            error = ""
            try:
                _check_cancel()
                self._write_progress(output_dir, state, "running_lammps", 28)
                if config.force_mock:
                    raise RuntimeError("Mock mode forced by USE_MOCK=true.")
                
                mode, error, summary = run_lammps(
                    input_path,
                    output_dir,
                    request_payload,
                    config,
                    state.run_id,
                )
            except SimulationCancelledError:
                raise
            except Exception as exc:
                error = str(exc)
                if not config.allow_mock_fallback and not config.force_mock:
                    state.error = error
                    state.status = "failed"
                    state.summary = {
                        "status": "failed",
                        "mode": "real",
                        "error": error,
                        "request": {
                            "original_query": state.user_query,
                            "normalized_request": state.normalized_request,
                        },
                        "progress": {"stage": "failed", "percent": 100, "message": error},
                        "artifacts": {},
                    }
                    write_json(output_dir / "summary.json", state.summary)
                    return state
                mode = "mock"
                summary = run_mock(output_dir, request_payload, error)
            
            _check_cancel()
            self._write_progress(output_dir, state, "converting_dump", 55)
            structure_path = convert_dump(output_dir)
            
            _check_cancel()
            self._write_progress(output_dir, state, "generating_thermo_plot", 65)
            plot_path = generate_plot(output_dir)
            
            _check_cancel()
            diffusion_status = generate_diffusion_trajectory_if_applicable(
                output_dir,
                state.normalized_request,
                mode,
                run_id=state.run_id,
                progress_callback=lambda stage, percent: self._write_progress(output_dir, state, stage, percent),
            )
            
            _check_cancel()
            self._write_progress(output_dir, state, "building_report", 96)
            report_path = self._build_report(
                output_dir,
                state,
                summary,
                mode,
                error,
                config,
                diffusion_status,
            )
            artifacts = {
                "request.json": str(request_path),
                "in.lammps": str(input_path),
                "run.log": str(output_dir / "run.log"),
                "thermo.csv": str(output_dir / "thermo.csv"),
                "summary.json": str(output_dir / "summary.json"),
                "plot.png": str(plot_path),
                "report.md": str(report_path),
                "structure_summary.json": str(structure_path),
            }
            if diffusion_status.get("generated"):
                artifacts["diffusion_trajectory.png"] = str(diffusion_status["image_path"])
                artifacts["diffusion_trajectory_3d.gif"] = str(diffusion_status["animation_path"])
                artifacts["ovito.mp4"] = str(diffusion_status["video_path"])
                artifacts["diffusion_metadata.json"] = str(diffusion_status["metadata_path"])
            summary_payload = {
                "status": "completed",
                "mode": mode,
                "error": error,
                "request": {
                    "original_query": state.user_query,
                    "normalized_request": state.normalized_request,
                },
                "metrics": summary,
                "progress": {
                    "stage": "completed",
                    "percent": 100,
                    "message": "任务已完成，所有产物已写入 outputs 目录。",
                },
                "postprocess": {
                    "ovito_status": diffusion_status,
                },
                "artifacts": artifacts,
            }
            write_json(output_dir / "summary.json", summary_payload)
            state.mode = mode
            state.status = "completed"
            state.error = error
            state.summary = summary_payload
            state.artifacts = summary_payload["artifacts"]
            return state
            
        except SimulationCancelledError as exc:
            state.status = "cancelled"
            state.error = str(exc)
            self._write_progress(output_dir, state, "cancelled", 100)
            return state

    def _write_progress(self, output_dir: Path, state: AgentState, stage: str, percent: int) -> None:
        progress_messages = {
            "preparing_input": "正在生成 LAMMPS 输入脚本。",
            "running_lammps": "正在执行 LAMMPS 模拟。",
            "converting_dump": "正在转换 dump 轨迹和结构摘要。",
            "generating_thermo_plot": "正在生成热力学曲线图。",
            "rendering_diffusion_preview": "正在生成扩散轨迹图与 3D 动画。",
            "building_report": "正在汇总结果并生成报告。",
            "failed": "任务执行失败。",
            "completed": "任务已完成。",
            "cancelled": "任务已被主动取消。",
        }
        target_status = "running"
        if stage in ("failed", "cancelled", "completed"):
            target_status = stage
            
        payload = {
            "status": target_status,
            "mode": state.mode,
            "error": state.error,
            "request": {
                "original_query": state.user_query,
                "normalized_request": state.normalized_request,
            },
            "progress": {
                "stage": stage,
                "percent": percent,
                "message": progress_messages.get(stage, stage),
            },
            "artifacts": {},
        }
        write_json(output_dir / "summary.json", payload)

    def _build_report(
        self,
        output_dir: Path,
        state: AgentState,
        metrics: Dict[str, float],
        mode: str,
        error: str,
        config: SupervisorConfig,
        diffusion_status: Dict[str, object],
    ) -> Path:
        risk = error if error else "No blocking errors detected."
        diffusion_section = f"""## 扩散轨迹图
supported_task={diffusion_status.get('supported_task')}
backend={diffusion_status.get('backend')}
generated={diffusion_status.get('generated')}
reason={diffusion_status.get('reason')}
animation_path={diffusion_status.get('animation_path', '')}
video_path={diffusion_status.get('video_path', '')}
"""
        report = f"""# MD Agent Run Report

## 用户目标
{state.user_query}

## 归一化参数
{state.normalized_request}

## 执行模式
{mode}

## LAMMPS 配置
command={config.lammps_command}
potentials_dir={config.potentials_dir}

## 关键热力学结果
{metrics}

{diffusion_section}

## 失败信息或风险提示
{risk}

## 后续建议
- 若当前为 mock 模式，请配置 `LAMMPS_CMD` 与 `POTENTIALS_DIR` 后重试。
- 若需要更多体系，请扩展 `generate_lammps_in.py` 中的模板和材料映射。
"""
        report_path = output_dir / "report.md"
        report_path.write_text(report, encoding="utf-8")
        return report_path
