from __future__ import annotations

import json
from pathlib import Path

from app.config import CONFIGS_ROOT
from app.materials_rag.models import MaterialsRagDocument


DEFAULT_DOCUMENTS_PATH = CONFIGS_ROOT / "materials_rag_documents.jsonl"
WIKIPEDIA_DOCUMENTS_PATH = CONFIGS_ROOT / "materials_rag_wikipedia.jsonl"
DEFAULT_DOCUMENTS_PATHS = (DEFAULT_DOCUMENTS_PATH, WIKIPEDIA_DOCUMENTS_PATH)

_CACHE_KEY: tuple[tuple[str, int], ...] | None = None
_CACHE_DOCUMENTS: tuple[MaterialsRagDocument, ...] = ()


def load_materials_rag_documents(path: Path | None = None) -> tuple[MaterialsRagDocument, ...]:
    global _CACHE_KEY, _CACHE_DOCUMENTS

    targets = (path,) if path is not None else tuple(candidate for candidate in DEFAULT_DOCUMENTS_PATHS if candidate.exists())
    if not targets:
        return ()
    cache_key = tuple((str(target.resolve()), target.stat().st_mtime_ns) for target in targets)
    if _CACHE_KEY == cache_key:
        return _CACHE_DOCUMENTS

    documents: list[MaterialsRagDocument] = []
    document_ids: set[str] = set()
    for target in targets:
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            document = MaterialsRagDocument.model_validate(payload)
            if document.id in document_ids:
                raise ValueError(f"Duplicate materials RAG document id: {document.id}")
            document_ids.add(document.id)
            documents.append(document)

    _CACHE_KEY = cache_key
    _CACHE_DOCUMENTS = tuple(documents)
    return _CACHE_DOCUMENTS
