from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from typing import Any

from app.config import settings
from app.lammps.config import detect_ovito_backend, lammps_config_public_payload
from app.state import DiagnosticCheck, SystemDiagnosticsResponse
from app.thermo.registry import load_thermo_database_cards
from app.utils.path_utils import ensure_directory


def _safe_command_probe(command: list[str], *, timeout: float = 3.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "output": ""}

    output = "\n".join(
        line.strip()
        for line in (completed.stdout.splitlines() + completed.stderr.splitlines())
        if line.strip()
    )
    return {
        "ok": completed.returncode == 0 or bool(output),
        "returncode": completed.returncode,
        "output": output[:1200],
    }


def _llm_check() -> DiagnosticCheck:
    configured = bool(settings.llm_api_base_url and settings.llm_api_key and settings.llm_model)
    if not settings.llm_enabled:
        status = "warning"
        summary = "LLM 已关闭，agent 会拒绝需要真实模型的能力。"
    elif configured:
        status = "ok"
        summary = "LLM 配置完整，可以用于真实 agent 路由、代码生成和审查。"
    else:
        status = "error"
        summary = "LLM 已启用，但 base URL / API key / model 至少缺少一项。"
    return DiagnosticCheck(
        name="LLM",
        status=status,
        summary=summary,
        details={
            "llm_enabled": settings.llm_enabled,
            "require_llm_for_agents": settings.require_llm_for_agents,
            "api_base_url": settings.llm_api_base_url,
            "model": settings.llm_model,
            "api_key_set": bool(settings.llm_api_key),
        },
    )


def _python_check() -> DiagnosticCheck:
    executable = settings.python_executable
    exists = bool(executable) and (Path(executable).exists() or shutil.which(executable))
    if not exists:
        return DiagnosticCheck(
            name="Python Runtime",
            status="error",
            summary="当前配置的 Python 可执行文件不存在或不可执行。",
            details={"python_executable": executable, "exists": False},
        )
    probe = _safe_command_probe([executable, "--version"])
    return DiagnosticCheck(
        name="Python Runtime",
        status="ok" if probe.get("ok") else "warning",
        summary="Python 运行时可用于本地 wrapper 执行。" if probe.get("ok") else "Python 路径存在，但版本检测返回异常。",
        details={
            "python_executable": executable,
            "exists": True,
            "version_output": probe.get("output", ""),
        },
    )


def _lammps_check() -> DiagnosticCheck:
    payload = lammps_config_public_payload()
    command = str(payload.get("lammps_command") or "").strip()
    exists = bool(payload.get("lammps_command_exists"))
    if not command or not exists:
        return DiagnosticCheck(
            name="LAMMPS Runtime",
            status="warning",
            summary="LAMMPS 路径尚未就绪，当前只能依赖 mock fallback 或无法运行真实 MD。",
            details=payload,
        )
    probe = _safe_command_probe([command, "-help"])
    version_output = str(probe.get("output", "")).splitlines()[0] if probe.get("output") else ""
    return DiagnosticCheck(
        name="LAMMPS Runtime",
        status="ok" if probe.get("ok") else "warning",
        summary="LAMMPS 本地运行时已就绪。" if probe.get("ok") else "LAMMPS 路径存在，但版本探测未返回稳定结果。",
        details={**payload, "version_output": version_output},
    )


def _ovito_check() -> DiagnosticCheck:
    status = detect_ovito_backend()
    available = bool(status.get("ovito_available"))
    return DiagnosticCheck(
        name="OVITO",
        status="ok" if available else "warning",
        summary="OVITO 后处理已就绪，可生成 GIF/MP4 动画。" if available else "OVITO 不可用，LAMMPS 只能返回静态图与轨迹文件。",
        details=status,
    )


def _thermo_registry_check() -> DiagnosticCheck:
    cards = load_thermo_database_cards()
    systems = [card.system_name for card in cards]
    return DiagnosticCheck(
        name="Thermodynamic Registry",
        status="ok" if cards else "warning",
        summary=f"当前已注册 {len(cards)} 个 TDB 体系，可用于真实 pycalphad 计算。",
        details={"count": len(cards), "systems": systems},
    )


def _storage_check() -> DiagnosticCheck:
    outputs_root = ensure_directory(settings.tmp_dir)
    runs_root = ensure_directory(outputs_root / settings.runs_dir_name)
    memory_root = ensure_directory(outputs_root / settings.memory_dir_name)
    return DiagnosticCheck(
        name="Storage",
        status="ok",
        summary="运行产物、会话记忆目录已就绪。",
        details={
            "outputs_root": str(outputs_root),
            "runs_root": str(runs_root),
            "memory_root": str(memory_root),
            "outputs_root_exists": outputs_root.exists(),
            "runs_root_exists": runs_root.exists(),
            "memory_root_exists": memory_root.exists(),
        },
    )


def build_system_diagnostics() -> SystemDiagnosticsResponse:
    checks = [
        _llm_check(),
        _python_check(),
        _lammps_check(),
        _ovito_check(),
        _thermo_registry_check(),
        _storage_check(),
    ]
    if any(check.status == "error" for check in checks):
        overall = "error"
    elif any(check.status == "warning" for check in checks):
        overall = "warning"
    else:
        overall = "ok"
    return SystemDiagnosticsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        overall_status=overall,
        checks=checks,
    )
