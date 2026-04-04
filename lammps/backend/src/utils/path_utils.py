from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict

from src.utils.constants import OUTPUTS_DIR


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_run_dir(run_id: str | None = None) -> Path:
    run_id = run_id or uuid.uuid4().hex[:12]
    return ensure_dir(OUTPUTS_DIR / run_id)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
