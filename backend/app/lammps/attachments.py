from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from app.state import UploadedAsset


POTENTIAL_SUFFIXES = (".eam.alloy", ".eam.fs", ".eam", ".setfl", ".meam")
STRUCTURE_SUFFIXES = (".data", ".lmp", ".lammps", ".dat")


def sanitize_upload_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "").name.strip())
    return cleaned or "upload.bin"


def _decode_data_url(data_url: str) -> bytes:
    if not data_url:
        return b""
    if "," not in data_url:
        return base64.b64decode(data_url)
    _, encoded = data_url.split(",", 1)
    return base64.b64decode(encoded)


def _detect_category(filename: str, media_type: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(POTENTIAL_SUFFIXES):
        return "potential"
    if lowered.endswith(STRUCTURE_SUFFIXES):
        return "structure"
    if media_type.startswith("image/"):
        return "image"
    return "attachment"


def _detect_structure_format(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(STRUCTURE_SUFFIXES):
        return "read_data"
    return ""


def persist_uploaded_assets(uploaded_assets: list[UploadedAsset], output_dir: Path) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    for index, asset in enumerate(uploaded_assets, start=1):
        safe_name = sanitize_upload_filename(asset.name or f"upload_{index}.bin")
        stored_name = f"uploaded_{safe_name}"
        destination = output_dir / stored_name
        destination.write_bytes(_decode_data_url(asset.data_url))
        category = _detect_category(safe_name, asset.media_type or "application/octet-stream")
        persisted.append(
            {
                "asset_id": asset.asset_id,
                "original_name": asset.name or safe_name,
                "stored_name": stored_name,
                "media_type": asset.media_type or "application/octet-stream",
                "path": str(destination),
                "category": category,
                "structure_format": _detect_structure_format(safe_name),
            }
        )
    return persisted


def infer_request_overrides(attachments: list[dict[str, Any]]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for attachment in attachments:
        category = attachment.get("category")
        path = str(attachment.get("path") or "")
        if category == "potential" and path and not overrides.get("custom_potential_path"):
            overrides["custom_potential_path"] = path
        if category == "structure" and path and not overrides.get("custom_structure_path"):
            overrides["custom_structure_path"] = path
            overrides["custom_structure_format"] = str(attachment.get("structure_format") or "read_data")
    return overrides

