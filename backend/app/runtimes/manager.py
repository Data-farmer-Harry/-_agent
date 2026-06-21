from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.lammps.config import lammps_config_public_payload
from app.thermo.registry import load_thermo_database_cards


class RuntimeCapabilityProfile(BaseModel):
    name: str
    compute_domain: str
    status: str = "unknown"
    summary: str = ""
    supports_structured_request: bool = True
    supports_streaming_progress: bool = True
    supports_cancellation: bool = False
    supports_repair_loop: bool = True
    supports_mock_fallback: bool = False
    required_dependencies: list[str] = Field(default_factory=list)
    optional_dependencies: list[str] = Field(default_factory=list)
    artifact_kinds: list[str] = Field(default_factory=list)
    default_tool_chain: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)


class RuntimeManagerReport(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    runtimes: list[RuntimeCapabilityProfile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _python_runtime_ready() -> bool:
    executable = settings.python_executable
    return bool(executable and (Path(executable).exists() or shutil.which(executable)))


def _phase_runtime_profile() -> RuntimeCapabilityProfile:
    cards = load_thermo_database_cards()
    python_ready = _python_runtime_ready()
    llm_ready = bool(settings.llm_enabled and settings.llm_api_base_url and settings.llm_api_key and settings.llm_model)
    status = "ok" if cards and python_ready and llm_ready else "warning"
    recommendations: list[str] = []
    if not cards:
        recommendations.append("补充 thermo_registry 和 TDB 数据库，否则相图 runtime 只能返回 no-database 诊断。")
    if not python_ready:
        recommendations.append("配置可执行 Python 路径，pycalphad wrapper 需要本地 Python 执行。")
    if not llm_ready:
        recommendations.append("配置 LLM 后，相图 runtime 才能稳定完成请求解析、代码生成和 review。")
    recommendations.append("后续可加入 TDB 文件 sha256 到 provenance，进一步增强可复现性。")
    return RuntimeCapabilityProfile(
        name="PhaseDiagramRuntime",
        compute_domain="phase_diagram",
        status=status,
        summary=f"pycalphad + TDB runtime，当前注册 {len(cards)} 个热力学体系。",
        supports_structured_request=True,
        supports_streaming_progress=True,
        supports_cancellation=False,
        supports_repair_loop=True,
        supports_mock_fallback=False,
        required_dependencies=["python", "pycalphad", "registered TDB", "LLM"],
        optional_dependencies=["thermo RAG", "accuracy gate"],
        artifact_kinds=["html", "image", "code", "json"],
        default_tool_chain=[
            "request_interpreter",
            "thermo_database_lookup",
            "phase_diagram_codegen",
            "python_execute",
            "phase_diagram_result_review",
        ],
        config={
            "python_executable": settings.python_executable,
            "llm_model": settings.llm_model,
            "agent_max_steps": settings.agent_max_steps,
            "agent_max_repair_attempts": settings.agent_max_repair_attempts,
            "registered_systems": [card.system_name for card in cards],
        },
        recommendations=recommendations,
    )


def _lammps_runtime_profile() -> RuntimeCapabilityProfile:
    payload = lammps_config_public_payload()
    command_ready = bool(payload.get("lammps_command_exists"))
    potentials_ready = bool(payload.get("potentials_dir_exists"))
    ovito_ready = bool(payload.get("ovito_available"))
    force_mock = bool(payload.get("force_mock"))
    allow_mock = bool(payload.get("allow_mock_fallback"))
    status = "ok" if command_ready and potentials_ready else ("warning" if allow_mock or force_mock else "error")
    recommendations: list[str] = []
    if not command_ready:
        recommendations.append("配置 lammps_command，真实 MD 执行依赖本地 LAMMPS 可执行文件。")
    if not potentials_ready:
        recommendations.append("配置 potentials_dir，EAM/MEAM 势函数选择依赖本地势函数目录。")
    if not ovito_ready:
        recommendations.append("安装 OVITO Python module 或 ovitos，可提升轨迹动画和 diffusion preview 质量。")
    recommendations.append("后续可把 LAMMPS executable version 和 potential file hash 写入 provenance。")
    return RuntimeCapabilityProfile(
        name="LammpsRuntime",
        compute_domain="lammps",
        status=status,
        summary="LAMMPS + postprocess runtime，负责真实 MD、热力学图、轨迹和 OVITO 动画产物。",
        supports_structured_request=True,
        supports_streaming_progress=True,
        supports_cancellation=True,
        supports_repair_loop=True,
        supports_mock_fallback=allow_mock,
        required_dependencies=["LAMMPS executable", "potentials_dir", "python"],
        optional_dependencies=["OVITO", "materials RAG"],
        artifact_kinds=["code", "csv", "image", "video", "markdown", "json", "text"],
        default_tool_chain=[
            "materials_rag_search",
            "lammps_request_interpreter",
            "lammps_registry_lookup",
            "lammps_validation",
            "lammps_input_codegen",
            "lammps_execute",
            "lammps_postprocess",
            "lammps_result_review",
        ],
        config=payload,
        recommendations=recommendations,
    )


def build_runtime_manager_report() -> RuntimeManagerReport:
    return RuntimeManagerReport(
        runtimes=[_phase_runtime_profile(), _lammps_runtime_profile()],
        notes=[
            "Runtime manager is read-only; it reports capability and dependency status without triggering calculations.",
            "Per-run runtime_profile is written into summary metadata after each runtime finishes.",
        ],
    )
