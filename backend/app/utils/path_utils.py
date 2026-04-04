import json
from pathlib import Path
from typing import Any, Optional


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_text_file(path: Path, content: str) -> Path:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")
    return path


def read_text_file_if_exists(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def remove_file_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def write_json_file(path: Path, payload: dict[str, Any]) -> Path:
    ensure_directory(path.parent)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)
    return path


def read_json_file_if_exists(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
