import os
import sys
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = BACKEND_ROOT / ".env"


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


def build_settings(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path = DEFAULT_ENV_FILE,
) -> "Settings":
    merged_env = _read_env_file(env_file)
    merged_env.update(dict(environ or os.environ))
    return Settings(
        tmp_dir=BACKEND_ROOT / "tmp",
        python_executable=merged_env.get("PHASE_DIAGRAM_PYTHON_EXECUTABLE", sys.executable),
        llm_api_base_url=merged_env.get("PHASE_DIAGRAM_LLM_API_BASE_URL", "").rstrip("/"),
        llm_api_key=merged_env.get("PHASE_DIAGRAM_LLM_API_KEY", ""),
        llm_model=merged_env.get("PHASE_DIAGRAM_LLM_MODEL", "qwen3-coder-plus"),
        llm_request_timeout_seconds=int(merged_env.get("PHASE_DIAGRAM_LLM_REQUEST_TIMEOUT_SECONDS", "120")),
        llm_max_tokens=int(merged_env.get("PHASE_DIAGRAM_LLM_MAX_TOKENS", "4000")),
        agent_max_steps=int(merged_env.get("PHASE_DIAGRAM_AGENT_MAX_STEPS", "10")),
        agent_max_repair_attempts=int(merged_env.get("PHASE_DIAGRAM_AGENT_MAX_REPAIR_ATTEMPTS", "1")),
    )


class Settings(BaseModel):
    app_name: str = "Phase Diagram Agent API"
    app_version: str = "0.1.0"
    tmp_dir: Path = BACKEND_ROOT / "tmp"
    runs_dir_name: str = "runs"
    latest_result_file_name: str = "latest_result.html"
    code_file_name: str = "code.py"
    result_file_name: str = "result.html"
    trace_file_name: str = "trace.json"
    python_executable: str = sys.executable
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen3-coder-plus"
    llm_request_timeout_seconds: int = 120
    llm_max_tokens: int = 4000
    agent_max_steps: int = 10
    agent_max_repair_attempts: int = 1
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


settings = build_settings()
