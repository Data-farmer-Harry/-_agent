from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class BM25DocumentStats:
    term_frequencies: dict[str, int]
    length: int


@dataclass(frozen=True)
class BM25Index:
    documents: tuple[BM25DocumentStats, ...]
    idf: dict[str, float]
    average_document_length: float
    k1: float = 1.5
    b: float = 0.75


def normalize_bm25_tokens(tokens: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for token in tokens:
        item = str(token or "").strip().lower()
        if item:
            normalized.append(item)
    return tuple(normalized)


def build_bm25_index(tokenized_documents: Iterable[Iterable[str]], *, k1: float = 1.5, b: float = 0.75) -> BM25Index:
    document_stats: list[BM25DocumentStats] = []
    document_frequencies: Counter[str] = Counter()

    for raw_tokens in tokenized_documents:
        tokens = normalize_bm25_tokens(raw_tokens)
        counts = Counter(tokens)
        document_stats.append(BM25DocumentStats(term_frequencies=dict(counts), length=len(tokens)))
        document_frequencies.update(counts.keys())

    document_count = len(document_stats)
    average_length = (
        sum(document.length for document in document_stats) / document_count
        if document_count
        else 0.0
    )
    idf = {
        term: math.log(1.0 + ((document_count - frequency + 0.5) / (frequency + 0.5)))
        for term, frequency in document_frequencies.items()
    }
    return BM25Index(
        documents=tuple(document_stats),
        idf=idf,
        average_document_length=average_length,
        k1=k1,
        b=b,
    )


def score_bm25(query_tokens: Iterable[str], document: BM25DocumentStats, index: BM25Index) -> float:
    tokens = tuple(dict.fromkeys(normalize_bm25_tokens(query_tokens)))
    if not tokens or not document.term_frequencies or index.average_document_length <= 0:
        return 0.0

    length_norm = 1.0 - index.b + index.b * (document.length / index.average_document_length)
    score = 0.0
    for token in tokens:
        frequency = document.term_frequencies.get(token, 0)
        if frequency <= 0:
            continue
        idf = index.idf.get(token, 0.0)
        numerator = frequency * (index.k1 + 1.0)
        denominator = frequency + index.k1 * length_norm
        if denominator > 0:
            score += idf * (numerator / denominator)
    return score
