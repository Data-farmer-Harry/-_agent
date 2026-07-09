from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings
from app.materials_rag.models import MaterialsRagQuery
from app.materials_rag.retriever import _build_index, search_materials_rag
from app.rag.reranker import reranker_available
from app.rag.sqlite_vector_store import get_vector_store
from app.thermo.rag_index import build_thermo_card_index
from app.thermo.rag_retriever import search_thermo_cards


DEFAULT_DATASET = BACKEND_ROOT / "benchmarks" / "datasets" / "rag_blind_cases.jsonl"
DEFAULT_OUTPUT = BACKEND_ROOT / "outputs" / "rag_blind" / "latest.json"
EVAL_KS = (1, 3, 5)


def _mean(values) -> float:  # noqa: ANN001
    sequence = list(values)
    if not sequence:
        raise ValueError("mean requires at least one value")
    return sum(float(value) for value in sequence) / len(sequence)


@dataclass(frozen=True)
class BlindCase:
    case_id: str
    suite: str
    query: str
    expected: tuple[str, ...]
    domain: str | None
    doc_type: str | None
    material: str | None
    language: str
    difficulty: str


def _load_cases(path: Path) -> tuple[BlindCase, ...]:
    cases: list[BlindCase] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        case = BlindCase(
            case_id=str(payload["case_id"]),
            suite=str(payload["suite"]),
            query=str(payload["query"]),
            expected=tuple(str(item) for item in payload["expected"]),
            domain=str(payload.get("domain") or "") or None,
            doc_type=str(payload.get("doc_type") or "") or None,
            material=str(payload.get("material") or "") or None,
            language=str(payload.get("language") or "mixed"),
            difficulty=str(payload.get("difficulty") or "hard"),
        )
        if case.case_id in seen_ids or case.query.casefold() in seen_queries:
            raise ValueError(f"Duplicate blind case id or query: {case.case_id}")
        seen_ids.add(case.case_id)
        seen_queries.add(case.query.casefold())
        cases.append(case)
    if not 200 <= len(cases) <= 500:
        raise ValueError(f"Blind dataset must contain 200-500 cases, got {len(cases)}")
    return tuple(cases)


def _rank(retrieved: list[str], expected: tuple[str, ...]) -> int | None:
    expected_set = set(expected)
    for index, document_id in enumerate(retrieved, start=1):
        if document_id in expected_set:
            return index
    return None


def _ndcg(retrieved: list[str], expected: tuple[str, ...], k: int) -> float:
    expected_set = set(expected)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, document_id in enumerate(retrieved[:k], start=1)
        if document_id in expected_set
    )
    ideal_relevant = min(len(expected_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_relevant + 1))
    return dcg / idcg if idcg else 0.0


def _summarize(results: list[dict[str, object]], *, ranking: str) -> dict[str, object]:
    rank_key = f"{ranking}_rank"
    ranks = [int(result[rank_key]) for result in results if result[rank_key] is not None]
    summary: dict[str, object] = {
        "cases": len(results),
        "hits_at_pool": len(ranks),
        "misses_at_pool": len(results) - len(ranks),
        "mrr": round(sum(1.0 / rank for rank in ranks) / len(results), 4) if results else 0.0,
        "mean_rank_of_hits": round(_mean(ranks), 3) if ranks else None,
    }
    for k in EVAL_KS:
        summary[f"hit@{k}"] = round(sum(rank <= k for rank in ranks) / len(results), 4) if results else 0.0
        summary[f"ndcg@{k}"] = round(
            _mean(float(result[f"{ranking}_ndcg@{k}"]) for result in results),
            4,
        ) if results else 0.0
    return summary


def _evaluate_case(case: BlindCase, pool_size: int) -> dict[str, object]:
    if case.suite == "thermo_blind":
        hits = search_thermo_cards(case.query, top_k=pool_size, min_score=0.0)
        reranked = [hit.card.system_name for hit in hits]
        original = [
            hit.card.system_name
            for hit in sorted(hits, key=lambda item: item.original_rank or (pool_size + 1))
        ]
        hit_payloads = [
            {
                "id": hit.card.system_name,
                "hybrid_score": round(hit.score, 5),
                "rerank_score": hit.rerank_score,
                "original_rank": hit.original_rank,
                "embedding_backend": hit.embedding_backend,
                "reranker_backend": hit.reranker_backend,
            }
            for hit in hits
        ]
    else:
        hits = search_materials_rag(
            MaterialsRagQuery(
                query=case.query,
                domain=case.domain,
                doc_type=case.doc_type,
                material=case.material,
                top_k=pool_size,
            )
        )
        reranked = [hit.document.id for hit in hits]
        original = [
            hit.document.id
            for hit in sorted(hits, key=lambda item: item.original_rank or (pool_size + 1))
        ]
        hit_payloads = [
            {
                "id": hit.document.id,
                "hybrid_score": hit.score,
                "rerank_score": hit.rerank_score,
                "original_rank": hit.original_rank,
                "embedding_backend": hit.embedding_backend,
                "reranker_backend": hit.reranker_backend,
            }
            for hit in hits
        ]

    payload: dict[str, object] = {
        "case_id": case.case_id,
        "suite": case.suite,
        "query": case.query,
        "expected": list(case.expected),
        "language": case.language,
        "difficulty": case.difficulty,
        "original_rank": _rank(original, case.expected),
        "reranked_rank": _rank(reranked, case.expected),
        "original_top5": original[:5],
        "reranked_top5": reranked[:5],
        "hits": hit_payloads,
    }
    for ranking, retrieved in (("original", original), ("reranked", reranked)):
        for k in EVAL_KS:
            payload[f"{ranking}_ndcg@{k}"] = round(_ndcg(retrieved, case.expected, k), 6)
    return payload


def _group_summaries(results: list[dict[str, object]]) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for result in results:
        grouped[f"suite:{result['suite']}"].append(result)
        grouped[f"language:{result['language']}"].append(result)
        grouped[f"difficulty:{result['difficulty']}"].append(result)
    return {
        key: {
            "original": _summarize(items, ranking="original"),
            "reranked": _summarize(items, ranking="reranked"),
        }
        for key, items in sorted(grouped.items())
    }


def run_blind_eval(
    *,
    dataset: Path,
    workers: int,
    require_remote: bool,
    require_reranker: bool,
    limit: int | None = None,
) -> dict[str, object]:
    cases = _load_cases(dataset)
    if limit is not None:
        cases = cases[: max(1, limit)]
    pool_size = max(5, settings.rag_reranker_candidate_pool if settings.rag_reranker_enabled else 5)
    started = time.perf_counter()
    materials_index = _build_index()
    thermo_index = build_thermo_card_index()
    if require_remote:
        active = {
            materials_index[0].embedding_backend if materials_index else "",
            thermo_index[0].embedding_backend if thermo_index else "",
        }
        if active != {"llm_api"}:
            raise RuntimeError(f"Remote embedding required, active backends: {sorted(active)}")
    if require_reranker and not reranker_available():
        raise RuntimeError("Remote reranker required but not configured.")

    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        futures = {executor.submit(_evaluate_case, case, pool_size): case for case in cases}
        for completed, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if completed % 25 == 0 or completed == len(futures):
                print(f"evaluated {completed}/{len(futures)}", flush=True)
    results.sort(key=lambda item: str(item["case_id"]))

    embedding_backends = sorted(
        {
            str(hit["embedding_backend"])
            for result in results
            for hit in result["hits"]
            if hit.get("embedding_backend")
        }
    )
    reranker_backends = sorted(
        {
            str(hit["reranker_backend"])
            for result in results
            for hit in result["hits"]
            if hit.get("reranker_backend")
        }
    )
    if require_remote and embedding_backends != ["llm_api"]:
        raise RuntimeError(f"Blind evaluation used non-remote embedding backends: {embedding_backends}")
    if require_reranker:
        unexpected_rerankers = set(reranker_backends).difference({"openrouter", "not_needed"})
        if "openrouter" not in reranker_backends or unexpected_rerankers:
            raise RuntimeError(f"Blind evaluation used invalid reranker backends: {reranker_backends}")

    inventory = get_vector_store().inventory()
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dataset": str(dataset),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "case_count": len(cases),
        "pool_size": pool_size,
        "embedding": {
            "materials_model": settings.materials_rag_embedding_model,
            "thermo_model": settings.thermo_rag_embedding_model,
            "backends": embedding_backends,
            "vector_store_backend": inventory.get("backend"),
        },
        "reranker": {
            "enabled": settings.rag_reranker_enabled,
            "model": settings.rag_reranker_model,
            "backends": reranker_backends,
        },
        "original": _summarize(results, ranking="original"),
        "reranked": _summarize(results, ranking="reranked"),
        "groups": _group_summaries(results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen blind RAG cases before and after remote reranking.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--require-remote", action="store_true")
    parser.add_argument("--require-reranker", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases for smoke testing.")
    args = parser.parse_args()
    report = run_blind_eval(
        dataset=args.dataset,
        workers=args.workers,
        require_remote=args.require_remote,
        require_reranker=args.require_reranker,
        limit=args.limit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "case_count": report["case_count"],
                "embedding": report["embedding"],
                "reranker": report["reranker"],
                "original": report["original"],
                "reranked": report["reranked"],
                "elapsed_seconds": report["elapsed_seconds"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
