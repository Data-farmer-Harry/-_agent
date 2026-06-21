from __future__ import annotations

from dataclasses import dataclass

from app.materials_rag.normalizer import (
    canonical_expansion_terms,
    canonical_terms,
    extract_materials,
    material_expansion_terms,
)
from app.utils.constants import normalize_system_key


@dataclass(frozen=True)
class RagQueryRewrite:
    original_query: str
    search_query: str
    rerank_query: str
    expansion_terms: tuple[str, ...]
    canonical_terms: tuple[str, ...]
    materials: tuple[str, ...]
    normalized_system_keys: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.expansion_terms or self.normalized_system_keys)


def _compact_query(text: str) -> str:
    return " ".join((text or "").split())


def _dedupe(items: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(str(item).split())
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        output.append(normalized)
        seen.add(key)
    return tuple(output)


def _append_expansion(original_query: str, terms: tuple[str, ...], *, label: str) -> str:
    query = _compact_query(original_query)
    if not terms:
        return query
    return f"{query}\n{label}: {'; '.join(terms)}"


def rewrite_materials_query(query: str) -> RagQueryRewrite:
    original = _compact_query(query)
    canonicals = canonical_terms(original)
    materials = extract_materials(original)
    terms: list[str] = []
    terms.extend(canonical_expansion_terms(original))
    terms.extend(material_expansion_terms(materials))

    if len(materials) >= 5 and any(marker in original.casefold() for marker in ("hea", "alloy", "合金")):
        terms.extend(
            [
                "high entropy alloy",
                "multi principal element alloy",
                "configurational entropy",
                "mixing entropy",
                "solid solution",
            ]
        )

    expansion_terms = _dedupe(tuple(terms))[:48]
    search_query = _append_expansion(original, expansion_terms, label="retrieval rewrite terms")
    rerank_query = _append_expansion(original, expansion_terms[:24], label="related materials terms")
    return RagQueryRewrite(
        original_query=original,
        search_query=search_query,
        rerank_query=rerank_query,
        expansion_terms=expansion_terms,
        canonical_terms=canonicals,
        materials=materials,
    )


def rewrite_thermo_query(query: str) -> RagQueryRewrite:
    original = _compact_query(query)
    canonicals = canonical_terms(original)
    materials = extract_materials(original)
    terms: list[str] = []
    terms.extend(canonical_expansion_terms(original))
    terms.extend(material_expansion_terms(materials, max_aliases_per_material=1))

    system_keys: list[str] = []
    original_key = normalize_system_key(original)
    if original_key:
        system_keys.append(original_key)
    if len(materials) >= 2:
        joined = "-".join(materials[:4])
        terms.append(joined)
        terms.append("binary phase diagram" if len(materials) == 2 else "multicomponent phase diagram")
        terms.append("thermodynamic database")
        system_key = normalize_system_key(" ".join(materials[:4]))
        if system_key:
            system_keys.append(system_key)

    expansion_terms = _dedupe(tuple(terms))[:40]
    normalized_system_keys = _dedupe(tuple(system_keys))
    if normalized_system_keys:
        expansion_terms = _dedupe((*expansion_terms, *normalized_system_keys))[:44]
    search_query = _append_expansion(original, expansion_terms, label="thermo retrieval rewrite terms")
    rerank_query = _append_expansion(original, expansion_terms[:20], label="related thermo terms")
    return RagQueryRewrite(
        original_query=original,
        search_query=search_query,
        rerank_query=rerank_query,
        expansion_terms=expansion_terms,
        canonical_terms=canonicals,
        materials=materials,
        normalized_system_keys=normalized_system_keys,
    )
