from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:  # pragma: no cover - optional dependency
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pdfplumber = None


MAX_UPLOAD_FILES = 10
MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024
TEXT_EXTRACT_BYTE_LIMIT = 200_000
TEXT_EXTRACT_CHAR_LIMIT = 12_000
PDF_EXTRACT_PAGE_LIMIT = 5

POTENTIAL_SUFFIXES = (".eam.alloy", ".eam.fs", ".eam", ".setfl", ".meam")
STRUCTURE_DATA_SUFFIXES = (".data", ".lmp", ".lammps", ".dat")
TRAJECTORY_SUFFIXES = (".dump", ".atom")
TEXT_SUFFIXES = (
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".log",
    ".yaml",
    ".yml",
    ".xml",
    ".ini",
    ".cfg",
    ".conf",
)

TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/csv",
    "text/csv",
}

MATERIAL_HINTS = {
    "al": "Al",
    "aluminum": "Al",
    "aluminium": "Al",
    "cu": "Cu",
    "copper": "Cu",
    "ni": "Ni",
    "nickel": "Ni",
}


def sanitize_upload_filename(filename: str) -> str:
    name = Path(filename or "").name.strip()
    if not name:
        return "upload.bin"
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "upload.bin"


def guess_mime_type(filename: str, explicit_mime: str = "") -> str:
    mime_type = (explicit_mime or "").strip().lower()
    if mime_type:
        return mime_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def is_text_like(filename: str, mime_type: str) -> bool:
    lower = filename.lower()
    if lower.endswith(TEXT_SUFFIXES):
        return True
    if mime_type.startswith("text/"):
        return True
    if mime_type in TEXT_MIME_TYPES:
        return True
    return False


def detect_attachment_category(filename: str, mime_type: str) -> str:
    lower = filename.lower()
    if lower.endswith(POTENTIAL_SUFFIXES):
        return "potential"
    if lower.endswith(STRUCTURE_DATA_SUFFIXES):
        return "structure"
    if lower.endswith(TRAJECTORY_SUFFIXES):
        return "trajectory"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type == "application/pdf":
        return "pdf"
    if is_text_like(filename, mime_type):
        return "text"
    if mime_type == "application/octet-stream":
        return "binary"
    return "other"


def detect_structure_loader(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith(STRUCTURE_DATA_SUFFIXES):
        return "read_data"
    return None


def infer_material_hint(filename: str) -> str | None:
    lower = filename.lower()
    for token, symbol in MATERIAL_HINTS.items():
        if token in lower:
            return symbol
    return None


def default_conversation_mode(category: str) -> str:
    if category == "image":
        return "multimodal"
    if category in {"pdf", "text", "structure", "trajectory", "potential"}:
        return "extracted"
    return "metadata-only"


def default_usage(category: str, structure_loader: str | None) -> str:
    if category == "potential":
        return "custom_potential"
    if category == "structure" and structure_loader:
        return "initial_structure"
    return ""


def build_attachment_metadata(
    upload_id: str,
    original_name: str,
    stored_name: str,
    size_bytes: int,
    mime_type: str = "",
) -> Dict[str, Any]:
    detected_mime = guess_mime_type(original_name or stored_name, mime_type)
    category = detect_attachment_category(original_name or stored_name, detected_mime)
    structure_loader = detect_structure_loader(original_name or stored_name)
    usage = default_usage(category, structure_loader)
    conversation_mode = default_conversation_mode(category)
    previewable = category == "image"
    return {
        "upload_id": upload_id,
        "original_name": original_name,
        "stored_name": stored_name,
        "size_bytes": int(size_bytes),
        "mime_type": detected_mime,
        "category": category,
        "kind": category,
        "conversation_mode": conversation_mode,
        "supported_for_run": bool(usage),
        "usage": usage,
        "previewable": previewable,
        "material_hint": infer_material_hint(original_name or stored_name),
        "structure_loader": structure_loader or "",
    }


def public_attachment_payload(attachments: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    public: List[Dict[str, Any]] = []
    for attachment in attachments:
        item = dict(attachment)
        item.pop("path", None)
        item.pop("_extracted_text", None)
        public.append(item)
    return public


def infer_request_hints(attachments: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    hints: Dict[str, Any] = {}
    for attachment in attachments:
        material_hint = attachment.get("material_hint")
        if material_hint and not hints.get("material"):
            hints["material"] = material_hint
        if attachment.get("category") == "potential" and not hints.get("potential_family"):
            hints["potential_family"] = "eam"
    return hints


def extract_attachment_text(attachment: Dict[str, Any]) -> Dict[str, Any]:
    path_value = str(attachment.get("path", "") or "").strip()
    category = str(attachment.get("category", "") or "")
    if not path_value:
        return {"text": "", "used": False, "fallback_reason": "attachment path missing"}
    path = Path(path_value)
    if not path.exists():
        return {"text": "", "used": False, "fallback_reason": "attachment file missing"}

    if category == "pdf":
        return _extract_pdf_text(path)
    if category in {"text", "structure", "trajectory", "potential"}:
        return _extract_plain_text(path)
    return {"text": "", "used": False, "fallback_reason": ""}


def build_data_url(path: Path, mime_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_base64_payload(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _extract_plain_text(path: Path) -> Dict[str, Any]:
    try:
        raw_bytes = path.read_bytes()[:TEXT_EXTRACT_BYTE_LIMIT]
    except Exception as exc:
        return {"text": "", "used": False, "fallback_reason": f"failed to read text: {exc}"}
    text = raw_bytes.decode("utf-8", errors="replace")
    normalized = text.strip()[:TEXT_EXTRACT_CHAR_LIMIT]
    if not normalized:
        return {"text": "", "used": False, "fallback_reason": "no extractable text found"}
    return {"text": normalized, "used": True, "fallback_reason": ""}


def _extract_pdf_text(path: Path) -> Dict[str, Any]:
    if pdfplumber is None:
        return {"text": "", "used": False, "fallback_reason": "pdfplumber is not available"}
    try:
        chunks: List[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:PDF_EXTRACT_PAGE_LIMIT]:
                text = (page.extract_text() or "").strip()
                if not text:
                    continue
                chunks.append(text)
                if len("\n\n".join(chunks)) >= TEXT_EXTRACT_CHAR_LIMIT:
                    break
        merged = "\n\n".join(chunks)[:TEXT_EXTRACT_CHAR_LIMIT].strip()
    except Exception as exc:
        return {"text": "", "used": False, "fallback_reason": f"failed to parse pdf: {exc}"}
    if not merged:
        return {"text": "", "used": False, "fallback_reason": "no extractable pdf text found"}
    return {"text": merged, "used": True, "fallback_reason": ""}
