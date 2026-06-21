from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from app.config import settings
from app.utils.path_utils import ensure_directory


_LOG_LOCK = Lock()


def new_request_id() -> str:
    return uuid4().hex[:12]


def structured_log_path() -> Path:
    return ensure_directory(settings.tmp_dir / settings.observability_dir_name) / settings.observability_events_file_name


def _compact(value: Any, *, limit: int = 800) -> Any:
    if isinstance(value, str):
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: max(limit - 3, 1)].rstrip()}..."
    if isinstance(value, dict):
        return {str(key): _compact(item, limit=limit) for key, item in value.items() if "api_key" not in str(key).lower()}
    if isinstance(value, list):
        return [_compact(item, limit=limit) for item in value[:20]]
    return value


def log_event(
    event: str,
    *,
    level: str = "info",
    request_id: str = "",
    job_id: str = "",
    run_id: str = "",
    conversation_id: str = "",
    message: str = "",
    **fields: Any,
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
        "request_id": request_id,
        "job_id": job_id,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "message": _compact(message, limit=400),
        **{key: _compact(value) for key, value in fields.items()},
    }
    path = structured_log_path()
    with _LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
