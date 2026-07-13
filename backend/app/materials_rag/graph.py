from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.materials_rag.models import MaterialsRagDocument, MaterialsRagHit
from app.materials_rag.normalizer import canonical_terms, normalize_text, tokenize_text


@dataclass(frozen=True)
class GraphEvidence:
    document_id: str
    score: float
    paths: tuple[str, ...]
    community: str


def document_entities(document: MaterialsRagDocument) -> set[str]:
    entities: set[str] = set()
    entities.update(f"material:{item.lower()}" for item in document.materials if item)
    entities.update(f"method:{item.lower()}" for item in document.methods if item)
    entities.update(f"tool:{item.lower()}" for item in document.tools if item)
    entities.update(f"keyword:{item.lower()}" for item in document.keywords if item)
    entities.add(f"domain:{document.domain.lower()}")
    for phase in document.metadata.get("phases", []) if isinstance(document.metadata.get("phases"), list) else []:
        entities.add(f"phase:{str(phase).lower()}")
    potential = str(document.metadata.get("potential_family") or "").strip().lower()
    if potential:
        entities.add(f"potential:{potential}")
    return entities


def build_graph_evidence(
    query: str,
    documents: Iterable[MaterialsRagDocument],
    lexical_seeds: Iterable[MaterialsRagHit] = (),
) -> dict[str, GraphEvidence]:
    docs = list(documents)
    doc_entities = {document.id: document_entities(document) for document in docs}
    entity_to_docs: dict[str, set[str]] = defaultdict(set)
    for document_id, entities in doc_entities.items():
        for entity in entities:
            entity_to_docs[entity].add(document_id)

    query_text = normalize_text(query)
    query_tokens = set(tokenize_text(query_text)) | {item.split(":", 1)[-1] for item in canonical_terms(query_text)}
    query_entities = {
        entity
        for entity in entity_to_docs
        if entity.split(":", 1)[-1] in query_tokens or entity.split(":", 1)[-1] in query_text
    }
    seed_hits = list(lexical_seeds)[:5]
    seed_weights = {hit.document.id: 1.0 / (index + 1) for index, hit in enumerate(seed_hits)}
    output: dict[str, GraphEvidence] = {}
    for document in docs:
        paths: list[str] = []
        direct = doc_entities[document.id] & query_entities
        score = min(len(direct) * 0.32, 0.96)
        paths.extend(f"query->{entity}->{document.id}" for entity in sorted(direct)[:4])
        for seed_id, seed_weight in seed_weights.items():
            if seed_id == document.id:
                continue
            shared = doc_entities[document.id] & doc_entities.get(seed_id, set())
            informative = {entity for entity in shared if not entity.startswith("domain:")}
            if informative:
                score += min(0.24, 0.08 * len(informative)) * seed_weight
                paths.append(f"{seed_id}->{sorted(informative)[0]}->{document.id}")
        community = _community_for(document)
        output[document.id] = GraphEvidence(
            document_id=document.id,
            score=round(min(score, 1.0), 6),
            paths=tuple(paths[:6]),
            community=community,
        )
    return output


def community_summaries(documents: Iterable[MaterialsRagDocument]) -> dict[str, str]:
    grouped: dict[str, list[MaterialsRagDocument]] = defaultdict(list)
    for document in documents:
        grouped[_community_for(document)].append(document)
    summaries: dict[str, str] = {}
    for community, docs in grouped.items():
        materials = sorted({material for document in docs for material in document.materials})
        methods = sorted({method for document in docs for method in document.methods})
        tools = sorted({tool for document in docs for tool in document.tools})
        summaries[community] = " | ".join(
            part
            for part in (
                f"community={community}",
                f"documents={len(docs)}",
                f"materials={','.join(materials[:12])}" if materials else "",
                f"methods={','.join(methods[:12])}" if methods else "",
                f"tools={','.join(tools[:12])}" if tools else "",
            )
            if part
        )
    return summaries


def _community_for(document: MaterialsRagDocument) -> str:
    material = document.materials[0].lower() if document.materials else "general"
    return f"{document.domain.lower()}:{material}"
