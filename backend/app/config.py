from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Mapping

from pydantic import BaseModel


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
CONFIGS_ROOT = BACKEND_ROOT / "configs"
DEFAULT_JSON_FILE = CONFIGS_ROOT / "llm_config.json"
DEFAULT_ENV_FILES = (
    BACKEND_ROOT / ".env",
    CONFIGS_ROOT / ".env",
)


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _read_env_file(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        values[normalized_key] = _strip_wrapping_quotes(value.strip())
    return values


def _read_json_config(config_file: Path) -> dict[str, str]:
    if not config_file.exists():
        return {}

    raw = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, str] = {}
    key_map = {
        "python_executable": "PHASE_DIAGRAM_PYTHON_EXECUTABLE",
        "llm_api_base_url": "PHASE_DIAGRAM_LLM_API_BASE_URL",
        "llm_api_key": "PHASE_DIAGRAM_LLM_API_KEY",
        "llm_model": "PHASE_DIAGRAM_LLM_MODEL",
        "llm_request_timeout_seconds": "PHASE_DIAGRAM_LLM_REQUEST_TIMEOUT_SECONDS",
        "llm_request_max_retries": "PHASE_DIAGRAM_LLM_REQUEST_MAX_RETRIES",
        "llm_retry_backoff_seconds": "PHASE_DIAGRAM_LLM_RETRY_BACKOFF_SECONDS",
        "llm_max_tokens": "PHASE_DIAGRAM_LLM_MAX_TOKENS",
        "agent_max_steps": "PHASE_DIAGRAM_AGENT_MAX_STEPS",
        "agent_max_repair_attempts": "PHASE_DIAGRAM_AGENT_MAX_REPAIR_ATTEMPTS",
        "thermo_rag_enabled": "PHASE_DIAGRAM_THERMO_RAG_ENABLED",
        "thermo_rag_top_k": "PHASE_DIAGRAM_THERMO_RAG_TOP_K",
        "thermo_rag_min_score": "PHASE_DIAGRAM_THERMO_RAG_MIN_SCORE",
        "thermo_rag_auto_select_threshold": "PHASE_DIAGRAM_THERMO_RAG_AUTO_SELECT_THRESHOLD",
        "thermo_rag_auto_select_margin": "PHASE_DIAGRAM_THERMO_RAG_AUTO_SELECT_MARGIN",
        "thermo_rag_embedding_backend": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_BACKEND",
        "thermo_rag_embedding_model": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_MODEL",
    }
    for json_key, env_key in key_map.items():
        value = raw.get(json_key)
        if value is None or value == "":
            continue
        normalized[env_key] = str(value)
    return normalized


class Settings(BaseModel):
    app_name: str = "Phase Diagram Agent API"
    app_version: str = "0.2.0"
    tmp_dir: Path = BACKEND_ROOT / "outputs"
    memory_dir_name: str = "memory"
    runs_dir_name: str = "runs"
    code_file_name: str = "code.py"
    result_file_name: str = "result.html"
    trace_file_name: str = "trace.json"
    summary_file_name: str = "summary.json"
    python_executable: str = sys.executable
    llm_enabled: bool = True
    require_llm_for_agents: bool = True
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen3-coder-plus"
    llm_request_timeout_seconds: int = 120
    llm_request_max_retries: int = 2
    llm_retry_backoff_seconds: float = 1.5
    llm_max_tokens: int = 4000
    agent_max_steps: int = 10
    agent_max_repair_attempts: int = 2
    thermo_rag_enabled: bool = True
    thermo_rag_top_k: int = 5
    thermo_rag_min_score: float = 0.22
    thermo_rag_auto_select_threshold: float = 0.72
    thermo_rag_auto_select_margin: float = 0.08
    thermo_rag_embedding_backend: str = "planned"
    thermo_rag_embedding_model: str = "BAAI/bge-m3"
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:4174",
        "http://127.0.0.1:4174",
        "http://localhost:4176",
        "http://127.0.0.1:4176",
    ]


def build_settings(
    *,
    environ: Mapping[str, str] | None = None,
    env_files: tuple[Path, ...] | None = None,
    json_file: Path = DEFAULT_JSON_FILE,
) -> Settings:
    merged_env = _read_json_config(json_file)
    for candidate in env_files or DEFAULT_ENV_FILES:
        merged_env.update(_read_env_file(candidate))
    merged_env.update(dict(environ or os.environ))
    return Settings(
        tmp_dir=BACKEND_ROOT / "outputs",
        python_executable=merged_env.get("PHASE_DIAGRAM_PYTHON_EXECUTABLE", sys.executable),
        llm_enabled=str(merged_env.get("PHASE_DIAGRAM_LLM_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"},
        require_llm_for_agents=(
            str(merged_env.get("PHASE_DIAGRAM_REQUIRE_LLM_FOR_AGENTS", "true")).strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        llm_api_base_url=merged_env.get("PHASE_DIAGRAM_LLM_API_BASE_URL", "").rstrip("/"),
        llm_api_key=merged_env.get("PHASE_DIAGRAM_LLM_API_KEY", ""),
        llm_model=merged_env.get("PHASE_DIAGRAM_LLM_MODEL", "qwen3-coder-plus"),
        llm_request_timeout_seconds=int(merged_env.get("PHASE_DIAGRAM_LLM_REQUEST_TIMEOUT_SECONDS", "120")),
        llm_request_max_retries=int(merged_env.get("PHASE_DIAGRAM_LLM_REQUEST_MAX_RETRIES", "2")),
        llm_retry_backoff_seconds=float(merged_env.get("PHASE_DIAGRAM_LLM_RETRY_BACKOFF_SECONDS", "1.5")),
        llm_max_tokens=int(merged_env.get("PHASE_DIAGRAM_LLM_MAX_TOKENS", "4000")),
        agent_max_steps=int(merged_env.get("PHASE_DIAGRAM_AGENT_MAX_STEPS", "10")),
        agent_max_repair_attempts=int(merged_env.get("PHASE_DIAGRAM_AGENT_MAX_REPAIR_ATTEMPTS", "2")),
        thermo_rag_enabled=str(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"},
        thermo_rag_top_k=int(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_TOP_K", "5")),
        thermo_rag_min_score=float(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_MIN_SCORE", "0.22")),
        thermo_rag_auto_select_threshold=float(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_AUTO_SELECT_THRESHOLD", "0.72")),
        thermo_rag_auto_select_margin=float(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_AUTO_SELECT_MARGIN", "0.08")),
        thermo_rag_embedding_backend=merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_BACKEND", "planned"),
        thermo_rag_embedding_model=merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_MODEL", "BAAI/bge-m3"),
    )


settings = build_settings()
_SETTINGS_LOCK = Lock()


def update_runtime_llm_config(payload: Mapping[str, object]) -> Settings:
    allowed = {
        "llm_enabled",
        "require_llm_for_agents",
        "python_executable",
        "llm_api_base_url",
        "llm_api_key",
        "llm_model",
        "llm_request_timeout_seconds",
        "llm_request_max_retries",
        "llm_retry_backoff_seconds",
        "llm_max_tokens",
    }
    with _SETTINGS_LOCK:
        for key, value in payload.items():
            if key not in allowed or value is None:
                continue
            if key in {"llm_enabled", "require_llm_for_agents"}:
                normalized = str(value).strip().lower() not in {"0", "false", "no", "off"}
                setattr(settings, key, normalized)
            elif key in {"llm_request_timeout_seconds", "llm_request_max_retries", "llm_max_tokens"}:
                setattr(settings, key, int(value))
            elif key == "llm_retry_backoff_seconds":
                setattr(settings, key, float(value))
            else:
                setattr(settings, key, str(value).strip())
    return settings


def llm_config_public_payload() -> dict[str, object]:
    masked_key = ""
    if settings.llm_api_key:
        masked_key = f"{settings.llm_api_key[:4]}...{settings.llm_api_key[-4:]}" if len(settings.llm_api_key) > 8 else "***set***"
    return {
        "llm_enabled": settings.llm_enabled,
        "require_llm_for_agents": settings.require_llm_for_agents,
        "python_executable": settings.python_executable,
        "llm_api_base_url": settings.llm_api_base_url,
        "llm_model": settings.llm_model,
        "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
        "llm_request_max_retries": settings.llm_request_max_retries,
        "llm_retry_backoff_seconds": settings.llm_retry_backoff_seconds,
        "llm_max_tokens": settings.llm_max_tokens,
        "api_key_set": bool(settings.llm_api_key),
        "api_key_masked": masked_key,
    }
