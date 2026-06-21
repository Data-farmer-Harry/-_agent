from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Any

from app.config import DEFAULT_ENV_FILES, DEFAULT_JSON_FILE, settings
from app.core.artifacts import ArtifactService
from app.core.observability import structured_log_path
from app.lammps.config import detect_ovito_backend, lammps_config_public_payload
from app.rag.data_manager import RagDataManager
from app.rag.sqlite_vector_store import SqliteVectorStore, get_vector_store
from app.state import DiagnosticCheck, SystemDiagnosticsResponse
from app.thermo.registry import load_thermo_database_cards
from app.utils.path_utils import ensure_directory


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "***set***"


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


def _config_source_check() -> DiagnosticCheck:
    env_files = [{"path": str(path), "exists": path.exists()} for path in DEFAULT_ENV_FILES]
    json_exists = DEFAULT_JSON_FILE.exists()
    return DiagnosticCheck(
        name="Config Center",
        status="ok" if json_exists else "warning",
        summary=(
            "运行配置中心文件已就绪，运行时修改会持久化到 config。"
            if json_exists
            else "未找到 config JSON，系统仍可用默认值和环境变量启动。"
        ),
        details={
            "config_file": str(DEFAULT_JSON_FILE),
            "config_file_exists": json_exists,
            "env_files": env_files,
            "plaintext_keys_allowed": True,
            "public_api_masks_keys": True,
            "managed_sections": ["llm", "rag_embedding", "lammps", "ovito", "artifact_retention"],
        },
    )


def _llm_check() -> DiagnosticCheck:
    configured = bool(settings.llm_api_base_url and settings.llm_api_key and settings.llm_model)
    chat_ready = configured and settings.llm_supports_chat
    vision_ready = configured and settings.llm_supports_vision
    if not settings.llm_enabled:
        status = "warning"
        summary = "LLM 已关闭，agent 会拒绝需要真实模型的能力。"
    elif chat_ready:
        status = "ok"
        summary = "LLM 配置完整，可以用于真实 agent 路由、代码生成和审查。"
    else:
        status = "error"
        summary = "LLM 已启用，但 chat 能力或 base URL / API key / model 配置不完整。"
    return DiagnosticCheck(
        name="LLM / Multimodal",
        status=status,
        summary=summary,
        details={
            "llm_enabled": settings.llm_enabled,
            "require_llm_for_agents": settings.require_llm_for_agents,
            "api_base_url": settings.llm_api_base_url,
            "model": settings.llm_model,
            "api_key_set": bool(settings.llm_api_key),
            "api_key_masked": _mask_secret(settings.llm_api_key),
            "enable_thinking": settings.llm_enable_thinking,
            "chat_capability_configured": chat_ready,
            "vision_capability_configured": vision_ready,
            "native_embedding_capability_configured": configured and settings.llm_supports_embedding,
            "vision_note": (
                "当前 config 明确声明该模型支持 image_url 多模态输入。"
                if vision_ready
                else "当前 config 未声明视觉能力，图片任务会被标记为不可用。"
            ),
            "request_timeout_seconds": settings.llm_request_timeout_seconds,
            "max_tokens": settings.llm_max_tokens,
        },
    )


def _embedding_check() -> DiagnosticCheck:
    thermo_base = settings.thermo_rag_embedding_api_base_url or settings.llm_api_base_url
    thermo_key = settings.thermo_rag_embedding_api_key or settings.llm_api_key
    materials_base = settings.materials_rag_embedding_api_base_url or settings.llm_api_base_url
    materials_key = settings.materials_rag_embedding_api_key or settings.llm_api_key
    thermo_ready = settings.thermo_rag_embedding_backend == "local_hash" or bool(thermo_base and thermo_key)
    materials_ready = settings.materials_rag_embedding_backend == "local_hash" or bool(materials_base and materials_key)
    sqlite_vec_ready = SqliteVectorStore.extension_available()
    vector_inventory: dict[str, object] = {}
    vector_store_error = ""
    if sqlite_vec_ready:
        try:
            vector_inventory = get_vector_store().inventory()
        except Exception as exc:  # noqa: BLE001
            sqlite_vec_ready = False
            vector_store_error = str(exc)
    if thermo_ready and materials_ready and sqlite_vec_ready:
        status = "ok"
        summary = "Embedding 配置完整，SQLite + sqlite-vec 持久化向量库可用。"
    elif not sqlite_vec_ready:
        status = "error"
        summary = "sqlite-vec 未安装或无法加载，dense vector retrieval 不可用。"
    elif thermo_ready or materials_ready:
        status = "warning"
        summary = "部分 embedding 配置完整，另一部分会依赖 fallback 或可能降级。"
    else:
        status = "error"
        summary = "两套 embedding 都缺少可用 API key/base URL，且没有切到 local_hash。"
    return DiagnosticCheck(
        name="Embedding / Vector Retrieval",
        status=status,
        summary=summary,
        details={
            "thermo_backend": settings.thermo_rag_embedding_backend,
            "thermo_base_url": thermo_base,
            "thermo_model": settings.thermo_rag_embedding_model,
            "thermo_dimensions": settings.thermo_rag_embedding_dimensions,
            "thermo_api_key_set": bool(thermo_key),
            "thermo_api_key_masked": _mask_secret(thermo_key),
            "materials_backend": settings.materials_rag_embedding_backend,
            "materials_base_url": materials_base,
            "materials_model": settings.materials_rag_embedding_model,
            "materials_dimensions": settings.materials_rag_embedding_dimensions,
            "materials_api_key_set": bool(materials_key),
            "materials_api_key_masked": _mask_secret(materials_key),
            "vector_store_backend": vector_inventory.get("backend", "sqlite_vec"),
            "vector_store_path": vector_inventory.get("database_path", str(get_vector_store().path)),
            "sqlite_vec_version": vector_inventory.get("extension_version", ""),
            "vector_collections": vector_inventory.get("collections", []),
            "vector_store_error": vector_store_error,
        },
    )


def _rag_check() -> DiagnosticCheck:
    report = RagDataManager().inventory()
    collections = [collection.model_dump(mode="json") for collection in report.collections]
    warning = any(int(collection.get("document_count") or 0) <= 0 for collection in collections)
    return DiagnosticCheck(
        name="RAG Knowledge Bases",
        status="warning" if warning else "ok",
        summary="RAG 数据管理器已加载 materials / thermo 两套知识库。" if not warning else "至少一个 RAG collection 没有可用文档。",
        details={
            "collections": collections,
            "benchmark": report.benchmark.model_dump(mode="json"),
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
    short_term_root = ensure_directory(memory_root / "short_term")
    long_term_root = ensure_directory(memory_root / "long_term")
    return DiagnosticCheck(
        name="Storage",
        status="ok",
        summary="运行产物以及短期/长期记忆目录已就绪。",
        details={
            "outputs_root": str(outputs_root),
            "runs_root": str(runs_root),
            "memory_root": str(memory_root),
            "short_term_memory_root": str(short_term_root),
            "long_term_memory_root": str(long_term_root),
            "outputs_root_exists": outputs_root.exists(),
            "runs_root_exists": runs_root.exists(),
            "memory_root_exists": memory_root.exists(),
            "short_term_memory_root_exists": short_term_root.exists(),
            "long_term_memory_root_exists": long_term_root.exists(),
        },
    )


def _sqlite_memory_check() -> DiagnosticCheck:
    memory_root = ensure_directory(settings.tmp_dir / settings.memory_dir_name)
    db_path = memory_root / "memory.sqlite3"
    can_open = False
    table_count = 0
    error = ""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_count = len(rows)
        can_open = True
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    finally:
        if conn is not None:
            conn.close()
    return DiagnosticCheck(
        name="SQLite Memory",
        status="ok" if can_open else "error",
        summary="SQLite memory 数据库可打开。" if can_open else "SQLite memory 数据库无法打开。",
        details={
            "path": str(db_path),
            "exists": db_path.exists(),
            "can_open": can_open,
            "table_count": table_count,
            "error": error,
        },
    )


def _artifact_lifecycle_check() -> DiagnosticCheck:
    inventory = ArtifactService(root_dir=settings.tmp_dir).artifact_inventory(limit=50)
    return DiagnosticCheck(
        name="Artifact Lifecycle",
        status="ok",
        summary="Artifact inventory 与保留策略可用。",
        details={
            "runs_root": inventory.get("runs_root"),
            "run_count": inventory.get("run_count"),
            "total_size_bytes": inventory.get("total_size_bytes"),
            "retention_policy": inventory.get("retention_policy"),
            "sample_size": len(inventory.get("runs", [])),
        },
    )


def _observability_check() -> DiagnosticCheck:
    path = structured_log_path()
    parent = ensure_directory(path.parent)
    return DiagnosticCheck(
        name="Observability Logs",
        status="ok" if parent.exists() else "warning",
        summary="结构化 JSONL 日志目录已就绪。" if parent.exists() else "结构化日志目录尚未创建。",
        details={
            "events_file": str(path),
            "events_file_exists": path.exists(),
            "events_file_size_bytes": path.stat().st_size if path.exists() else 0,
        },
    )


def _benchmark_check() -> DiagnosticCheck:
    latest = settings.tmp_dir / "benchmarks" / "latest.json"
    available = latest.exists()
    return DiagnosticCheck(
        name="Benchmark Report",
        status="ok" if available else "warning",
        summary="已找到最近一次 benchmark 固化报告。" if available else "尚未生成 benchmark latest.json，可运行 run-all 生成。",
        details={
            "latest_report": str(latest),
            "available": available,
            "size_bytes": latest.stat().st_size if available else 0,
        },
    )


def build_system_diagnostics() -> SystemDiagnosticsResponse:
    checks = [
        _config_source_check(),
        _llm_check(),
        _embedding_check(),
        _rag_check(),
        _python_check(),
        _lammps_check(),
        _ovito_check(),
        _thermo_registry_check(),
        _storage_check(),
        _sqlite_memory_check(),
        _artifact_lifecycle_check(),
        _observability_check(),
        _benchmark_check(),
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
