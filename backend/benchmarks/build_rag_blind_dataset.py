from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_ROOT / "benchmarks" / "datasets" / "rag_blind_cases.jsonl"

import sys

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings
from app.materials_rag.document_store import DEFAULT_DOCUMENTS_PATH, WIKIPEDIA_DOCUMENTS_PATH
from app.thermo.registry import THERMO_REGISTRY_PATH, load_thermo_database_cards


@dataclass(frozen=True)
class Target:
    key: str
    suite: str
    expected: tuple[str, ...]
    domain: str
    doc_type: str | None
    material: str | None
    evidence: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _targets() -> list[Target]:
    targets: list[Target] = []
    for document in _load_jsonl(DEFAULT_DOCUMENTS_PATH):
        keywords = ", ".join(str(item) for item in document.get("keywords", [])[:8])
        materials = ", ".join(str(item) for item in document.get("materials", [])[:5])
        content = " ".join(str(document.get("content") or "").split())[:650]
        targets.append(
            Target(
                key=f"curated::{document['id']}",
                suite="materials_curated_blind",
                expected=(str(document["id"]),),
                domain=str(document.get("domain") or ""),
                doc_type=str(document.get("doc_type") or "") or None,
                material=(str(document.get("materials", [""])[0]) if document.get("materials") else None),
                evidence=(
                    f"Title: {document.get('title', '')}\nDomain/type: {document.get('domain', '')}/{document.get('doc_type', '')}\n"
                    f"Signals: {keywords}; materials: {materials}\nReference: {content}"
                ),
            )
        )

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for document in _load_jsonl(WIKIPEDIA_DOCUMENTS_PATH):
        grouped[str(document["id"]).rsplit(".chunk", 1)[0]].append(document)
    for prefix, documents in sorted(grouped.items()):
        first = documents[0]
        title = str(first.get("title") or "").split(" - ", 1)[0].removeprefix("Wikipedia: ")
        keywords = list(
            dict.fromkeys(
                str(keyword)
                for document in documents
                for keyword in document.get("keywords", [])
            )
        )
        content = " ".join(str(first.get("content") or "").split())[:650]
        targets.append(
            Target(
                key=f"wikipedia::{prefix}",
                suite="wikipedia_blind",
                expected=tuple(str(document["id"]) for document in documents),
                domain=str(first.get("domain") or ""),
                doc_type=None,
                material=None,
                evidence=(
                    f"Topic: {title}\nDomain: {first.get('domain', '')}\n"
                    f"Signals: {', '.join(keywords[:12])}\nReference: {content}"
                ),
            )
        )

    for card in load_thermo_database_cards():
        targets.append(
            Target(
                key=f"thermo::{card.system_name}",
                suite="thermo_blind",
                expected=(card.system_name,),
                domain="thermo",
                doc_type=None,
                material=None,
                evidence=(
                    f"System: {card.system_name}\nAliases: {', '.join(card.aliases)}\n"
                    f"Components: {', '.join(card.components)}\nPhases: {', '.join(card.phases[:14])}\n"
                    f"Tags: {', '.join(card.tags)}\nSummary: {card.summary}"
                ),
            )
        )
    return targets


def _openrouter_key() -> str:
    return (
        settings.rag_reranker_api_key
        or settings.materials_rag_embedding_api_key
        or settings.thermo_rag_embedding_api_key
        or settings.llm_api_key
    )


def _extract_json_object(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", stripped, flags=re.IGNORECASE)
    candidate = fenced.group(1) if fenced else stripped
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _generate_batch(targets: list[Target], *, model: str, timeout: int) -> dict[str, dict[str, str]]:
    compact_targets = [{"key": target.key, "evidence": target.evidence} for target in targets]
    prompt = (
        "Create exactly one held-out retrieval question for every target below. The question must be answerable by the target, "
        "but should sound like a real materials researcher rather than a copied heading. Paraphrase aggressively: do not copy a full title "
        "or a full sentence from the reference. Mix Chinese, English, and Chinese-English technical language across the batch. Include enough "
        "technical clues to make the label defensible, but do not include document IDs. Use difficulty hard when the wording is indirect or has "
        "nearby confusable concepts. Return strict JSON only as {\"cases\":[{\"key\":...,\"query\":...,\"language\":\"zh|en|mixed\","
        "\"difficulty\":\"medium|hard\"}]}.\n\nTargets:\n"
        + json.dumps(compact_targets, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You design leakage-resistant held-out information-retrieval evaluations for materials science RAG."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.75,
        "max_tokens": 6000,
    }
    req = request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {_openrouter_key()}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    content = parsed["choices"][0]["message"]["content"]
    result = _extract_json_object(content)
    if result is None or not isinstance(result.get("cases"), list):
        raise RuntimeError("Blind-query generator returned invalid JSON.")
    generated: dict[str, dict[str, str]] = {}
    for item in result["cases"]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        query = " ".join(str(item.get("query") or "").split())
        if key and 8 <= len(query) <= 500:
            generated[key] = {
                "query": query,
                "language": str(item.get("language") or "mixed"),
                "difficulty": str(item.get("difficulty") or "hard"),
            }
    return generated


def _normalize_language(query: str, raw_language: str) -> str:
    normalized = raw_language.strip().lower()
    if normalized in {"zh", "en", "mixed"}:
        return normalized
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", query))
    has_ascii_words = bool(re.search(r"\b[a-zA-Z]{3,}\b", query))
    if has_cjk and has_ascii_words:
        return "mixed"
    return "zh" if has_cjk else "en"


def build_dataset(*, model: str, batch_size: int, timeout: int, delay: float) -> list[dict[str, object]]:
    targets = _targets()
    generated: dict[str, dict[str, str]] = {}
    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                generated.update(_generate_batch(batch, model=model, timeout=timeout))
                last_error = None
                break
            except (RuntimeError, error.URLError, error.HTTPError, TimeoutError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError(f"Blind-query batch {offset // batch_size + 1} failed: {last_error}")
        time.sleep(max(0.0, delay))

    missing = [target.key for target in targets if target.key not in generated]
    if missing:
        raise RuntimeError(f"Blind-query generator omitted {len(missing)} targets: {missing[:8]}")

    built_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, object]] = []
    seen_queries: set[str] = set()
    for index, target in enumerate(targets, start=1):
        generated_case = generated[target.key]
        query = generated_case["query"]
        normalized_query = query.casefold()
        if normalized_query in seen_queries:
            raise RuntimeError(f"Duplicate blind query: {query}")
        seen_queries.add(normalized_query)
        records.append(
            {
                "case_id": f"blind-{index:04d}",
                "suite": target.suite,
                "query": query,
                "expected": list(target.expected),
                "domain": target.domain,
                "doc_type": target.doc_type,
                "material": target.material,
                "language": _normalize_language(query, generated_case["language"]),
                "difficulty": generated_case["difficulty"],
                "generation": {
                    "kind": "synthetic_holdout",
                    "model": model,
                    "generated_at": built_at,
                    "frozen_before_first_evaluation": True,
                },
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a frozen, cross-model synthetic blind set for RAG evaluation.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="qwen/qwen3-30b-a3b-instruct-2507")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    if not _openrouter_key():
        raise RuntimeError("OpenRouter key is required to build the blind dataset.")
    records = build_dataset(
        model=args.model,
        batch_size=max(1, min(args.batch_size, 20)),
        timeout=max(15, args.timeout),
        delay=max(0.0, args.delay),
    )
    if not 200 <= len(records) <= 500:
        raise RuntimeError(f"Blind dataset must contain 200-500 cases, got {len(records)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n",
        encoding="utf-8",
    )
    report = {
        "output": str(args.output),
        "cases": len(records),
        "suites": {suite: sum(record["suite"] == suite for record in records) for suite in sorted({str(record["suite"]) for record in records})},
        "source_sha256": {
            str(DEFAULT_DOCUMENTS_PATH.name): _sha256(DEFAULT_DOCUMENTS_PATH),
            str(WIKIPEDIA_DOCUMENTS_PATH.name): _sha256(WIKIPEDIA_DOCUMENTS_PATH),
            str(THERMO_REGISTRY_PATH.name): _sha256(THERMO_REGISTRY_PATH),
        },
        "generator_model": args.model,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
