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
CONFIG_KEY_MAP = {
    "python_executable": "PHASE_DIAGRAM_PYTHON_EXECUTABLE",
    "llm_enabled": "PHASE_DIAGRAM_LLM_ENABLED",
    "require_llm_for_agents": "PHASE_DIAGRAM_REQUIRE_LLM_FOR_AGENTS",
    "llm_api_base_url": "PHASE_DIAGRAM_LLM_API_BASE_URL",
    "llm_api_key": "PHASE_DIAGRAM_LLM_API_KEY",
    "llm_model": "PHASE_DIAGRAM_LLM_MODEL",
    "llm_enable_thinking": "PHASE_DIAGRAM_LLM_ENABLE_THINKING",
    "llm_supports_chat": "PHASE_DIAGRAM_LLM_SUPPORTS_CHAT",
    "llm_supports_vision": "PHASE_DIAGRAM_LLM_SUPPORTS_VISION",
    "llm_supports_embedding": "PHASE_DIAGRAM_LLM_SUPPORTS_EMBEDDING",
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
    "thermo_rag_embedding_api_base_url": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_BASE_URL",
    "thermo_rag_embedding_api_key": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_KEY",
    "thermo_rag_embedding_model": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_MODEL",
    "thermo_rag_embedding_dimensions": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_DIMENSIONS",
    "thermo_rag_vector_weight": "PHASE_DIAGRAM_THERMO_RAG_VECTOR_WEIGHT",
    "thermo_rag_vector_min_similarity": "PHASE_DIAGRAM_THERMO_RAG_VECTOR_MIN_SIMILARITY",
    "thermo_rag_bm25_weight": "PHASE_DIAGRAM_THERMO_RAG_BM25_WEIGHT",
    "thermo_rag_embedding_api_batch_size": "PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_BATCH_SIZE",
    "materials_rag_enabled": "PHASE_DIAGRAM_MATERIALS_RAG_ENABLED",
    "materials_rag_top_k": "PHASE_DIAGRAM_MATERIALS_RAG_TOP_K",
    "materials_rag_embedding_backend": "PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_BACKEND",
    "materials_rag_embedding_api_base_url": "PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_BASE_URL",
    "materials_rag_embedding_api_key": "PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_KEY",
    "materials_rag_embedding_model": "PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_MODEL",
    "materials_rag_embedding_dimensions": "PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_DIMENSIONS",
    "materials_rag_vector_weight": "PHASE_DIAGRAM_MATERIALS_RAG_VECTOR_WEIGHT",
    "materials_rag_vector_min_similarity": "PHASE_DIAGRAM_MATERIALS_RAG_VECTOR_MIN_SIMILARITY",
    "materials_rag_bm25_weight": "PHASE_DIAGRAM_MATERIALS_RAG_BM25_WEIGHT",
    "materials_rag_embedding_api_batch_size": "PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_BATCH_SIZE",
    "rag_reranker_enabled": "PHASE_DIAGRAM_RAG_RERANKER_ENABLED",
    "rag_reranker_api_base_url": "PHASE_DIAGRAM_RAG_RERANKER_API_BASE_URL",
    "rag_reranker_api_key": "PHASE_DIAGRAM_RAG_RERANKER_API_KEY",
    "rag_reranker_model": "PHASE_DIAGRAM_RAG_RERANKER_MODEL",
    "rag_reranker_candidate_pool": "PHASE_DIAGRAM_RAG_RERANKER_CANDIDATE_POOL",
    "rag_reranker_timeout_seconds": "PHASE_DIAGRAM_RAG_RERANKER_TIMEOUT_SECONDS",
    "rag_vector_store_path": "PHASE_DIAGRAM_RAG_VECTOR_STORE_PATH",
    "artifact_retention_keep_latest": "PHASE_DIAGRAM_ARTIFACT_RETENTION_KEEP_LATEST",
    "artifact_retention_max_age_days": "PHASE_DIAGRAM_ARTIFACT_RETENTION_MAX_AGE_DAYS",
    "lammps_command": "LAMMPS_CMD",
    "potentials_dir": "POTENTIALS_DIR",
    "ovito_location": "OVITO_LOCATION",
    "allow_mock_fallback": "ALLOW_MOCK_FALLBACK",
    "force_mock": "USE_MOCK",
    "max_retries": "MAX_RUN_RETRIES",
}
SECRET_CONFIG_KEYS = {
    "llm_api_key",
    "thermo_rag_embedding_api_key",
    "materials_rag_embedding_api_key",
    "rag_reranker_api_key",
}
SECRET_ENV_KEYS = {CONFIG_KEY_MAP[key] for key in SECRET_CONFIG_KEYS}


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


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_runtime_env_file(
    patch: Mapping[str, object],
    env_file: Path | None = None,
) -> dict[str, str]:
    """Persist secret runtime values to a local env file.

    This is intentionally separate from ``llm_config.json`` so API keys can be
    kept in ignored local files while non-sensitive model/runtime settings stay
    in the JSON config.
    """

    env_file = env_file or DEFAULT_ENV_FILES[0]
    existing = _read_env_file(env_file)
    for key, value in patch.items():
        env_key = CONFIG_KEY_MAP.get(key, key)
        if env_key not in SECRET_ENV_KEYS or value is None:
            continue
        normalized = str(value).strip()
        if not normalized:
            continue
        existing[env_key] = normalized
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Local secret runtime configuration. Do not commit this file.",
        "# Non-secret defaults live in backend/configs/llm_config.json.",
    ]
    for key in sorted(existing):
        lines.append(f"{key}={_quote_env_value(existing[key])}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return existing


def _read_json_config(config_file: Path) -> dict[str, str]:
    if not config_file.exists():
        return {}

    raw = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, str] = {}
    for json_key, env_key in CONFIG_KEY_MAP.items():
        value = raw.get(json_key)
        if value is None or value == "":
            continue
        normalized[env_key] = str(value)
    return normalized


def read_runtime_config_file(config_file: Path | None = None) -> dict[str, object]:
    config_file = config_file or DEFAULT_JSON_FILE
    if not config_file.exists():
        return {}
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def update_runtime_config_file(patch: Mapping[str, object], config_file: Path | None = None) -> dict[str, object]:
    config_file = config_file or DEFAULT_JSON_FILE
    existing = read_runtime_config_file(config_file)
    for key, value in patch.items():
        if key not in CONFIG_KEY_MAP or key in SECRET_CONFIG_KEYS or value is None:
            continue
        if isinstance(value, str):
            existing[key] = value.strip()
        else:
            existing[key] = value
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return existing


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
    artifact_manifest_file_name: str = "artifact_manifest.json"
    observability_dir_name: str = "logs"
    observability_events_file_name: str = "events.jsonl"
    python_executable: str = sys.executable
    llm_enabled: bool = True
    require_llm_for_agents: bool = True
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen3.5-plus"
    llm_enable_thinking: bool = False
    llm_supports_chat: bool = True
    llm_supports_vision: bool = True
    llm_supports_embedding: bool = False
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
    thermo_rag_embedding_backend: str = "llm_api"
    thermo_rag_embedding_api_base_url: str = ""
    thermo_rag_embedding_api_key: str = ""
    thermo_rag_embedding_model: str = "text-embedding-v4"
    thermo_rag_embedding_dimensions: int = 256
    thermo_rag_vector_weight: float = 0.24
    thermo_rag_vector_min_similarity: float = 0.1
    thermo_rag_bm25_weight: float = 0.12
    thermo_rag_embedding_api_batch_size: int = 10
    materials_rag_enabled: bool = True
    materials_rag_top_k: int = 5
    materials_rag_embedding_backend: str = "llm_api"
    materials_rag_embedding_api_base_url: str = ""
    materials_rag_embedding_api_key: str = ""
    materials_rag_embedding_model: str = "text-embedding-v4"
    materials_rag_embedding_dimensions: int = 256
    materials_rag_vector_weight: float = 0.24
    materials_rag_vector_min_similarity: float = 0.1
    materials_rag_bm25_weight: float = 0.05
    materials_rag_embedding_api_batch_size: int = 10
    rag_reranker_enabled: bool = False
    rag_reranker_api_base_url: str = ""
    rag_reranker_api_key: str = ""
    rag_reranker_model: str = "cohere/rerank-v3.5"
    rag_reranker_candidate_pool: int = 20
    rag_reranker_timeout_seconds: int = 60
    rag_vector_store_path: str = ""
    artifact_retention_keep_latest: int = 120
    artifact_retention_max_age_days: int = 30
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
    merged_env: dict[str, str] = {}
    merged_env.update(_read_json_config(json_file))
    for candidate in DEFAULT_ENV_FILES if env_files is None else env_files:
        merged_env.update(_read_env_file(candidate))
    # JSON stores non-secret defaults. Local .env files can override those
    # defaults and hold secrets; explicit process environment remains highest.
    merged_env.update(dict(os.environ if environ is None else environ))
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
        llm_model=merged_env.get("PHASE_DIAGRAM_LLM_MODEL", "qwen3.5-plus"),
        llm_enable_thinking=str(merged_env.get("PHASE_DIAGRAM_LLM_ENABLE_THINKING", "false")).strip().lower()
        not in {"0", "false", "no", "off"},
        llm_supports_chat=str(merged_env.get("PHASE_DIAGRAM_LLM_SUPPORTS_CHAT", "true")).strip().lower()
        not in {"0", "false", "no", "off"},
        llm_supports_vision=str(merged_env.get("PHASE_DIAGRAM_LLM_SUPPORTS_VISION", "true")).strip().lower()
        not in {"0", "false", "no", "off"},
        llm_supports_embedding=str(merged_env.get("PHASE_DIAGRAM_LLM_SUPPORTS_EMBEDDING", "false")).strip().lower()
        not in {"0", "false", "no", "off"},
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
        thermo_rag_embedding_backend=merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_BACKEND", "llm_api"),
        thermo_rag_embedding_api_base_url=merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_BASE_URL", "").rstrip("/"),
        thermo_rag_embedding_api_key=merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_KEY", ""),
        thermo_rag_embedding_model=merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_MODEL", "text-embedding-v4"),
        thermo_rag_embedding_dimensions=int(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_DIMENSIONS", "256")),
        thermo_rag_vector_weight=float(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_VECTOR_WEIGHT", "0.24")),
        thermo_rag_vector_min_similarity=float(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_VECTOR_MIN_SIMILARITY", "0.1")),
        thermo_rag_bm25_weight=float(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_BM25_WEIGHT", "0.12")),
        thermo_rag_embedding_api_batch_size=int(merged_env.get("PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_API_BATCH_SIZE", "10")),
        materials_rag_enabled=str(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_ENABLED", "true")).strip().lower()
        not in {"0", "false", "no", "off"},
        materials_rag_top_k=int(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_TOP_K", "5")),
        materials_rag_embedding_backend=merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_BACKEND", "llm_api"),
        materials_rag_embedding_api_base_url=merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_BASE_URL", "").rstrip("/"),
        materials_rag_embedding_api_key=merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_KEY", ""),
        materials_rag_embedding_model=merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_MODEL", "text-embedding-v4"),
        materials_rag_embedding_dimensions=int(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_DIMENSIONS", "256")),
        materials_rag_vector_weight=float(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_VECTOR_WEIGHT", "0.24")),
        materials_rag_vector_min_similarity=float(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_VECTOR_MIN_SIMILARITY", "0.1")),
        materials_rag_bm25_weight=float(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_BM25_WEIGHT", "0.05")),
        materials_rag_embedding_api_batch_size=int(merged_env.get("PHASE_DIAGRAM_MATERIALS_RAG_EMBEDDING_API_BATCH_SIZE", "10")),
        rag_reranker_enabled=str(merged_env.get("PHASE_DIAGRAM_RAG_RERANKER_ENABLED", "false")).strip().lower()
        not in {"0", "false", "no", "off"},
        rag_reranker_api_base_url=merged_env.get("PHASE_DIAGRAM_RAG_RERANKER_API_BASE_URL", "").rstrip("/"),
        rag_reranker_api_key=merged_env.get("PHASE_DIAGRAM_RAG_RERANKER_API_KEY", ""),
        rag_reranker_model=merged_env.get("PHASE_DIAGRAM_RAG_RERANKER_MODEL", "cohere/rerank-v3.5"),
        rag_reranker_candidate_pool=int(merged_env.get("PHASE_DIAGRAM_RAG_RERANKER_CANDIDATE_POOL", "20")),
        rag_reranker_timeout_seconds=int(merged_env.get("PHASE_DIAGRAM_RAG_RERANKER_TIMEOUT_SECONDS", "60")),
        rag_vector_store_path=merged_env.get("PHASE_DIAGRAM_RAG_VECTOR_STORE_PATH", ""),
        artifact_retention_keep_latest=int(merged_env.get("PHASE_DIAGRAM_ARTIFACT_RETENTION_KEEP_LATEST", "120")),
        artifact_retention_max_age_days=int(merged_env.get("PHASE_DIAGRAM_ARTIFACT_RETENTION_MAX_AGE_DAYS", "30")),
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
        "llm_enable_thinking",
        "llm_supports_chat",
        "llm_supports_vision",
        "llm_supports_embedding",
        "llm_request_timeout_seconds",
        "llm_request_max_retries",
        "llm_retry_backoff_seconds",
        "llm_max_tokens",
        "thermo_rag_embedding_backend",
        "thermo_rag_embedding_api_base_url",
        "thermo_rag_embedding_api_key",
        "thermo_rag_embedding_model",
        "thermo_rag_embedding_dimensions",
        "thermo_rag_bm25_weight",
        "thermo_rag_embedding_api_batch_size",
        "materials_rag_enabled",
        "materials_rag_top_k",
        "materials_rag_embedding_backend",
        "materials_rag_embedding_api_base_url",
        "materials_rag_embedding_api_key",
        "materials_rag_embedding_model",
        "materials_rag_embedding_dimensions",
        "materials_rag_vector_weight",
        "materials_rag_vector_min_similarity",
        "materials_rag_bm25_weight",
        "materials_rag_embedding_api_batch_size",
        "rag_reranker_enabled",
        "rag_reranker_api_base_url",
        "rag_reranker_api_key",
        "rag_reranker_model",
        "rag_reranker_candidate_pool",
        "rag_reranker_timeout_seconds",
        "rag_vector_store_path",
    }
    persist_patch: dict[str, object] = {}
    secret_patch: dict[str, object] = {}
    with _SETTINGS_LOCK:
        for key, value in payload.items():
            if key not in allowed or value is None:
                continue
            if key in {
                "llm_enabled",
                "require_llm_for_agents",
                "llm_enable_thinking",
                "llm_supports_chat",
                "llm_supports_vision",
                "llm_supports_embedding",
                "materials_rag_enabled",
                "rag_reranker_enabled",
            }:
                normalized = str(value).strip().lower() not in {"0", "false", "no", "off"}
                setattr(settings, key, normalized)
                persist_patch[key] = normalized
            elif key in {
                "llm_request_timeout_seconds",
                "llm_request_max_retries",
                "llm_max_tokens",
                "thermo_rag_embedding_dimensions",
                "thermo_rag_embedding_api_batch_size",
                "materials_rag_top_k",
                "materials_rag_embedding_dimensions",
                "materials_rag_embedding_api_batch_size",
                "rag_reranker_candidate_pool",
                "rag_reranker_timeout_seconds",
            }:
                normalized = int(value)
                setattr(settings, key, normalized)
                persist_patch[key] = normalized
            elif key in {
                "llm_retry_backoff_seconds",
                "thermo_rag_bm25_weight",
                "materials_rag_vector_weight",
                "materials_rag_vector_min_similarity",
                "materials_rag_bm25_weight",
            }:
                normalized = float(value)
                setattr(settings, key, normalized)
                persist_patch[key] = normalized
            else:
                normalized = str(value).strip()
                setattr(settings, key, normalized)
                if key in SECRET_CONFIG_KEYS:
                    secret_patch[key] = normalized
                else:
                    persist_patch[key] = normalized
        if persist_patch:
            update_runtime_config_file(persist_patch)
        if secret_patch:
            update_runtime_env_file(secret_patch)
    return settings


def llm_config_public_payload() -> dict[str, object]:
    masked_key = ""
    if settings.llm_api_key:
        masked_key = f"{settings.llm_api_key[:4]}...{settings.llm_api_key[-4:]}" if len(settings.llm_api_key) > 8 else "***set***"
    thermo_embedding_key_masked = ""
    if settings.thermo_rag_embedding_api_key:
        thermo_embedding_key_masked = (
            f"{settings.thermo_rag_embedding_api_key[:4]}...{settings.thermo_rag_embedding_api_key[-4:]}"
            if len(settings.thermo_rag_embedding_api_key) > 8
            else "***set***"
        )
    materials_embedding_key_masked = ""
    if settings.materials_rag_embedding_api_key:
        materials_embedding_key_masked = (
            f"{settings.materials_rag_embedding_api_key[:4]}...{settings.materials_rag_embedding_api_key[-4:]}"
            if len(settings.materials_rag_embedding_api_key) > 8
            else "***set***"
        )
    effective_reranker_key = (
        settings.rag_reranker_api_key
        or settings.materials_rag_embedding_api_key
        or settings.thermo_rag_embedding_api_key
        or settings.llm_api_key
    )
    reranker_key_masked = ""
    if effective_reranker_key:
        reranker_key_masked = (
            f"{effective_reranker_key[:4]}...{effective_reranker_key[-4:]}"
            if len(effective_reranker_key) > 8
            else "***set***"
        )
    return {
        "llm_enabled": settings.llm_enabled,
        "require_llm_for_agents": settings.require_llm_for_agents,
        "python_executable": settings.python_executable,
        "llm_api_base_url": settings.llm_api_base_url,
        "llm_model": settings.llm_model,
        "llm_enable_thinking": settings.llm_enable_thinking,
        "llm_supports_chat": settings.llm_supports_chat,
        "llm_supports_vision": settings.llm_supports_vision,
        "llm_supports_embedding": settings.llm_supports_embedding,
        "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
        "llm_request_max_retries": settings.llm_request_max_retries,
        "llm_retry_backoff_seconds": settings.llm_retry_backoff_seconds,
        "llm_max_tokens": settings.llm_max_tokens,
        "thermo_rag_embedding_backend": settings.thermo_rag_embedding_backend,
        "thermo_rag_embedding_api_base_url": settings.thermo_rag_embedding_api_base_url,
        "thermo_rag_embedding_model": settings.thermo_rag_embedding_model,
        "thermo_rag_embedding_dimensions": settings.thermo_rag_embedding_dimensions,
        "thermo_rag_bm25_weight": settings.thermo_rag_bm25_weight,
        "thermo_rag_embedding_api_batch_size": settings.thermo_rag_embedding_api_batch_size,
        "thermo_rag_embedding_api_key_set": bool(settings.thermo_rag_embedding_api_key),
        "thermo_rag_embedding_api_key_masked": thermo_embedding_key_masked,
        "materials_rag_enabled": settings.materials_rag_enabled,
        "materials_rag_top_k": settings.materials_rag_top_k,
        "materials_rag_embedding_backend": settings.materials_rag_embedding_backend,
        "materials_rag_embedding_api_base_url": settings.materials_rag_embedding_api_base_url,
        "materials_rag_embedding_model": settings.materials_rag_embedding_model,
        "materials_rag_embedding_dimensions": settings.materials_rag_embedding_dimensions,
        "materials_rag_vector_weight": settings.materials_rag_vector_weight,
        "materials_rag_vector_min_similarity": settings.materials_rag_vector_min_similarity,
        "materials_rag_bm25_weight": settings.materials_rag_bm25_weight,
        "materials_rag_embedding_api_batch_size": settings.materials_rag_embedding_api_batch_size,
        "materials_rag_embedding_api_key_set": bool(settings.materials_rag_embedding_api_key),
        "materials_rag_embedding_api_key_masked": materials_embedding_key_masked,
        "rag_reranker_enabled": settings.rag_reranker_enabled,
        "rag_reranker_api_base_url": settings.rag_reranker_api_base_url,
        "rag_reranker_model": settings.rag_reranker_model,
        "rag_reranker_candidate_pool": settings.rag_reranker_candidate_pool,
        "rag_reranker_timeout_seconds": settings.rag_reranker_timeout_seconds,
        "rag_reranker_api_key_set": bool(effective_reranker_key),
        "rag_reranker_api_key_masked": reranker_key_masked,
        "rag_vector_store_path": settings.rag_vector_store_path,
        "api_key_set": bool(settings.llm_api_key),
        "api_key_masked": masked_key,
    }
