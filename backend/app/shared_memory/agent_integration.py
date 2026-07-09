from __future__ import annotations

import re
from typing import Any

from app.shared_memory.canonicalization import canonicalize_key_text
from app.shared_memory.models import MemoryItem, MemoryRetrievalResult, MemoryScope, MemoryWriteResult
from app.state import AgentChatRequest, AgentRunResponse, TaskRoute


_LAMMPS_MATERIAL_ALIASES = {
    "al": "Al",
    "aluminum": "Al",
    "aluminium": "Al",
    "铝": "Al",
    "cu": "Cu",
    "copper": "Cu",
    "铜": "Cu",
    "ni": "Ni",
    "nickel": "Ni",
    "镍": "Ni",
    "fe": "Fe",
    "iron": "Fe",
    "铁": "Fe",
}
_MATERIAL_PATTERN = re.compile(
    r"\b(al|aluminum|aluminium|cu|copper|ni|nickel|fe|iron)\b|铝|铜|镍|铁",
    flags=re.IGNORECASE,
)
_TEMPERATURE_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>K|k|kelvin|Kelvin|°C|℃|C|c)\b|温度\s*(?P<zh_value>\d+(?:\.\d+)?)",
    flags=re.IGNORECASE,
)
_STEPS_PATTERN = re.compile(r"(?P<value>\d{2,9})\s*(?:steps?|步)\b|步数\s*(?P<zh_value>\d{2,9})", flags=re.IGNORECASE)
_ENSEMBLE_PATTERN = re.compile(r"\b(?P<ensemble>NVE|NVT|NPT|NPH)\b", flags=re.IGNORECASE)
_TASK_PATTERN = re.compile(r"\b(heating|equilibration|relaxation|diffusion)\b|升温|平衡|弛豫|扩散", flags=re.IGNORECASE)
_PHASE_SYSTEM_PATTERN = re.compile(r"\b([A-Z][a-z]?\s*[-/]\s*[A-Z][a-z]?(?:\s*[-/]\s*[A-Z][a-z]?)?)\b")
_PREFERENCE_MARKERS = ("必须", "不要", "别", "优先", "记得", "保持", "只要", "先不要", "尽量", "最好")


def conversation_scope(conversation_id: str, *, include_global: bool = True) -> MemoryScope:
    return MemoryScope(scope_type="conversation", scope_id=conversation_id or "default", include_global=include_global)


def build_user_constraint_items(
    *,
    request: AgentChatRequest,
    route: TaskRoute,
    decision: dict[str, Any] | None = None,
    run_id: str = "",
) -> list[MemoryItem]:
    message = request.message.strip()
    if not message:
        return []
    decision = decision or {}
    scope_id = request.conversation_id or "default"
    source_ref = f"run:{run_id}:user_request" if run_id else "user_request"
    context = route.compute_domain if route.compute_domain != "none" else route.name
    items: list[MemoryItem] = [
        MemoryItem(
            scope_type="conversation",
            scope_id=scope_id,
            item_type="evidence",
            subject=f"{context} user request",
            predicate="request_text",
            value=message,
            text=message,
            authority="user",
            source_refs=[source_ref],
            metadata={
                "route_name": route.name,
                "compute_domain": route.compute_domain,
                "intent": str(decision.get("intent") or route.intent or ""),
                "run_id": run_id,
            },
        )
    ]
    if route.compute_domain == "lammps" or route.name == "lammps.generate":
        items.extend(_lammps_request_constraints(scope_id=scope_id, message=message, source_ref=source_ref, run_id=run_id))
    if route.compute_domain == "phase_diagram" or route.name in {"phase_diagram.generate", "mixed.request"}:
        items.extend(_phase_request_constraints(scope_id=scope_id, request=request, message=message, source_ref=source_ref, run_id=run_id))
    if any(marker in message for marker in _PREFERENCE_MARKERS):
        items.append(
            MemoryItem(
                scope_type="conversation",
                scope_id=scope_id,
                item_type="preference",
                subject="user workflow preference",
                predicate="preference_text",
                value=message,
                text=message,
                authority="user",
                source_refs=[source_ref],
                metadata={"locked": True, "route_name": route.name, "run_id": run_id},
            )
        )
    return items


def build_lammps_execution_fact_items(*, response: AgentRunResponse) -> list[MemoryItem]:
    if response.route.compute_domain != "lammps" and response.route.name != "lammps.generate":
        return []
    summary = response.summary if isinstance(response.summary, dict) else {}
    metadata = response.metadata if isinstance(response.metadata, dict) else {}
    request_payload = summary.get("request") if isinstance(summary.get("request"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    validation = summary.get("validation") if isinstance(summary.get("validation"), dict) else {}
    source_ref = f"run:{response.run_id}:lammps_result"
    scope_id = response.conversation_id or "default"
    subject = "LAMMPS request"
    items: list[MemoryItem] = [
        MemoryItem(
            scope_type="conversation",
            scope_id=scope_id,
            item_type="result",
            subject=f"LAMMPS run {response.run_id}",
            predicate="final_status",
            value=response.termination_reason or ("success" if response.success else "failed"),
            text=response.final_message[:1200],
            authority="execution",
            source_refs=[source_ref],
            metadata={
                "run_id": response.run_id,
                "route_name": response.route.name,
                "success": response.success,
                "run_mode": str(metadata.get("run_mode") or summary.get("mode") or ""),
            },
        )
    ]
    for predicate, unit in [
        ("material", ""),
        ("task_type", ""),
        ("target_temperature", "K"),
        ("steps", "steps"),
        ("ensemble", ""),
    ]:
        key = "temperature" if predicate == "target_temperature" else predicate
        value = request_payload.get(key)
        if value in (None, ""):
            continue
        items.append(
            MemoryItem(
                scope_type="conversation",
                scope_id=scope_id,
                item_type="fact",
                subject=subject,
                predicate=predicate,
                value=value,
                unit=unit,
                text=f"{predicate}={value} {unit}".strip(),
                authority="execution",
                source_refs=[source_ref],
                metadata={"run_id": response.run_id, "route_name": response.route.name},
            )
        )
    run_mode = str(metadata.get("run_mode") or summary.get("mode") or "").strip()
    if run_mode:
        items.append(
            MemoryItem(
                scope_type="conversation",
                scope_id=scope_id,
                item_type="fact",
                subject=f"LAMMPS run {response.run_id}",
                predicate="run_mode",
                value=run_mode,
                text=f"LAMMPS execution mode was {run_mode}.",
                authority="execution",
                source_refs=[source_ref],
                metadata={"run_id": response.run_id},
            )
        )
    for key, value in sorted(metrics.items()):
        if value in (None, ""):
            continue
        items.append(
            MemoryItem(
                scope_type="conversation",
                scope_id=scope_id,
                item_type="fact",
                subject=f"LAMMPS run {response.run_id}",
                predicate=f"metric:{canonicalize_key_text(str(key))}",
                value=value,
                text=f"{key}={value}",
                authority="execution",
                source_refs=[source_ref],
                metadata={"run_id": response.run_id, "metric_name": key},
            )
        )
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []
    if warnings:
        items.append(
            MemoryItem(
                scope_type="conversation",
                scope_id=scope_id,
                item_type="finding",
                subject=f"LAMMPS run {response.run_id}",
                predicate="validation_warnings",
                value=[str(item) for item in warnings],
                text="; ".join(str(item) for item in warnings[:5]),
                authority="execution",
                source_refs=[source_ref],
                metadata={"run_id": response.run_id},
            )
        )
    return items


def build_materials_rag_evidence_items(
    *,
    conversation_id: str,
    query: str,
    hits: list[Any],
    run_id: str = "",
    stage: str = "chat_materials_rag",
    domain: str | None = None,
    doc_type: str | None = None,
    material: str | None = None,
) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    scope_id = conversation_id or "default"
    for index, raw_hit in enumerate(hits):
        hit = _coerce_hit(raw_hit)
        document = _coerce_hit_document(hit)
        title = str(document.get("title") or hit.get("title") or "").strip()
        if not title:
            continue
        document_id = str(document.get("id") or hit.get("id") or title).strip()
        source_url = str(document.get("source_url") or hit.get("source_url") or "").strip()
        source = str(document.get("source") or hit.get("source") or "").strip()
        source_ref = source_url or source or f"materials_rag:{document_id}"
        excerpt = str(document.get("content") or hit.get("content_excerpt") or "").strip()
        items.append(
            MemoryItem(
                scope_type="conversation",
                scope_id=scope_id,
                item_type="evidence",
                subject=f"materials_rag:{document_id}",
                predicate="supports_query",
                value={
                    "query": query,
                    "title": title,
                    "score": hit.get("score"),
                    "source_url": source_url,
                },
                text=f"{title}: {excerpt}"[:1800] if excerpt else title,
                authority="rag",
                source_refs=[source_ref],
                metadata={
                    "run_id": run_id,
                    "stage": stage,
                    "rank": index + 1,
                    "domain": str(document.get("domain") or hit.get("domain") or domain or ""),
                    "doc_type": str(document.get("doc_type") or hit.get("doc_type") or doc_type or ""),
                    "material": str(material or ""),
                    "score": hit.get("score"),
                    "lexical_score": hit.get("lexical_score"),
                    "bm25_score": hit.get("bm25_score"),
                    "vector_score": hit.get("vector_score"),
                    "rerank_score": hit.get("rerank_score"),
                    "embedding_backend": str(hit.get("embedding_backend") or ""),
                    "reranker_backend": str(hit.get("reranker_backend") or ""),
                    "matched_fields": hit.get("matched_fields") or [],
                    "trust_level": str(document.get("trust_level") or hit.get("trust_level") or ""),
                },
            )
        )
    return items


def build_run_rag_evidence_items(*, response: AgentRunResponse) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    items.extend(_build_lammps_materials_rag_items(response=response))
    items.extend(_build_thermo_rag_items(response=response))
    return items


def retrieval_metadata(result: MemoryRetrievalResult | None) -> dict[str, Any]:
    if result is None:
        return {"available": False}
    return {
        "available": True,
        "backend": result.retrieval_backend,
        "scope_filter": result.scope_filter,
        "selected_item_ids": result.selected_item_ids,
        "forced_retention_ids": result.forced_retention_ids,
        "dropped_reasons": result.dropped_reasons,
        "estimated_before_bytes": result.estimated_before_bytes,
        "estimated_after_bytes": result.estimated_after_bytes,
    }


def write_results_metadata(results: list[MemoryWriteResult]) -> list[dict[str, Any]]:
    return [
        {
            "memory_id": result.item.memory_id,
            "item_type": result.item.item_type,
            "subject": result.item.subject,
            "predicate": result.item.predicate,
            "status": result.item.status,
            "created": result.created,
            "deduplicated": result.deduplicated,
            "dedup_level": result.dedup_level,
            "conflict_ids": result.conflict_ids,
            "conflict_statuses": result.conflict_statuses,
            "conflict_types": result.conflict_types,
            "needs_user": "needs_user" in result.conflict_statuses,
            "quarantined": result.item.status == "quarantined",
            "conflicted": result.item.status == "conflicted",
        }
        for result in results
    ]


def _build_lammps_materials_rag_items(*, response: AgentRunResponse) -> list[MemoryItem]:
    summary = response.summary if isinstance(response.summary, dict) else {}
    metadata = response.metadata if isinstance(response.metadata, dict) else {}
    materials_rag = summary.get("materials_rag") if isinstance(summary.get("materials_rag"), dict) else metadata.get("materials_rag")
    if not isinstance(materials_rag, dict):
        return []
    items: list[MemoryItem] = []
    for stage in ("planning", "error_diagnosis"):
        payload = materials_rag.get(stage)
        if not isinstance(payload, dict):
            continue
        hits = payload.get("hits")
        if not isinstance(hits, list):
            continue
        items.extend(
            build_materials_rag_evidence_items(
                conversation_id=response.conversation_id,
                query=str(payload.get("query") or ""),
                hits=hits,
                run_id=response.run_id,
                stage=f"lammps_{stage}_materials_rag",
                domain="lammps",
                material=str(payload.get("material") or ""),
            )
        )
    return items


def _build_thermo_rag_items(*, response: AgentRunResponse) -> list[MemoryItem]:
    if response.route.compute_domain != "phase_diagram" and response.route.name not in {"phase_diagram.generate", "mixed.request"}:
        return []
    summary = response.summary if isinstance(response.summary, dict) else {}
    metadata = response.metadata if isinstance(response.metadata, dict) else {}
    lookup = metadata.get("thermo_lookup") if isinstance(metadata.get("thermo_lookup"), dict) else summary.get("thermo_lookup")
    if not isinstance(lookup, dict):
        return []
    lookup_payloads = [lookup]
    nested_rag = lookup.get("rag")
    if isinstance(nested_rag, dict):
        lookup_payloads.append(nested_rag)
    items: list[MemoryItem] = []
    for payload in lookup_payloads:
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates:
            for index, candidate in enumerate(candidate for candidate in candidates if isinstance(candidate, dict)):
                items.append(_thermo_candidate_to_memory_item(response=response, payload=payload, candidate=candidate, rank=index + 1))
        elif payload.get("matched") and (payload.get("system_name") or payload.get("selected_system_name") or payload.get("database_name")):
            candidate = {
                "system_name": payload.get("system_name") or payload.get("selected_system_name") or summary.get("system_name") or "",
                "database_name": payload.get("database_name") or "",
                "database_file": payload.get("database_file") or "",
                "source_url": payload.get("source_url") or "",
                "documentation_url": payload.get("documentation_url") or "",
                "components": payload.get("components") or [],
                "phases": payload.get("phases") or [],
                "summary": payload.get("summary") or "",
                "score": payload.get("top_score"),
                "selection_strategy": payload.get("selection_strategy") or payload.get("lookup_mode") or "registry_match",
            }
            items.append(_thermo_candidate_to_memory_item(response=response, payload=payload, candidate=candidate, rank=1))
    return items


def _thermo_candidate_to_memory_item(
    *,
    response: AgentRunResponse,
    payload: dict[str, Any],
    candidate: dict[str, Any],
    rank: int,
) -> MemoryItem:
    system_name = str(candidate.get("system_name") or payload.get("selected_system_name") or "thermo_candidate").strip()
    database_name = str(candidate.get("database_name") or candidate.get("database_file") or "").strip()
    source_url = str(candidate.get("source_url") or candidate.get("documentation_url") or "").strip()
    source_ref = source_url or f"thermo_rag:{system_name}:{database_name or rank}"
    text = str(candidate.get("summary") or "").strip()
    if not text:
        text = f"{system_name} -> {database_name}".strip()
    authority = (
        "rag"
        if payload.get("selection_strategy") in {"rag_auto_select", "rag_candidates_only"}
        or candidate.get("match_reasons")
        else "registry"
    )
    return MemoryItem(
        scope_type="conversation",
        scope_id=response.conversation_id or "default",
        item_type="evidence",
        subject=f"thermo_rag:{system_name}",
        predicate="database_candidate",
        value={
            "query": payload.get("query") or "",
            "system_name": system_name,
            "database_name": database_name,
            "score": candidate.get("score") or payload.get("top_score"),
        },
        text=text[:1800],
        authority=authority,
        source_refs=[source_ref],
        metadata={
            "run_id": response.run_id,
            "stage": "thermo_rag_lookup",
            "rank": rank,
            "selection_strategy": str(candidate.get("selection_strategy") or payload.get("selection_strategy") or ""),
            "lookup_mode": str(payload.get("lookup_mode") or ""),
            "score": candidate.get("score") or payload.get("top_score"),
            "lexical_score": candidate.get("lexical_score") or payload.get("top_lexical_score"),
            "bm25_score": candidate.get("bm25_score") or payload.get("top_bm25_score"),
            "vector_score": candidate.get("vector_score") or payload.get("top_vector_score"),
            "rerank_score": candidate.get("rerank_score"),
            "embedding_backend": str(candidate.get("embedding_backend") or payload.get("embedding_backend") or ""),
            "reranker_backend": str(candidate.get("reranker_backend") or ""),
            "match_reasons": candidate.get("match_reasons") or [],
            "matched_terms": candidate.get("matched_terms") or [],
            "components": candidate.get("components") or [],
            "phases": candidate.get("phases") or [],
            "source_url": source_url,
        },
    )


def _coerce_hit(raw_hit: Any) -> dict[str, Any]:
    if isinstance(raw_hit, dict):
        return dict(raw_hit)
    if hasattr(raw_hit, "model_dump"):
        return raw_hit.model_dump(mode="json")
    return {}


def _coerce_hit_document(hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document")
    if isinstance(document, dict):
        return document
    if hasattr(document, "model_dump"):
        return document.model_dump(mode="json")
    return hit


def _lammps_request_constraints(*, scope_id: str, message: str, source_ref: str, run_id: str) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    material = _extract_material(message)
    if material:
        items.append(_constraint(scope_id, "LAMMPS request", "material", material, "", source_ref, run_id))
    temperature = _extract_temperature(message)
    if temperature is not None:
        value, unit = temperature
        items.append(_constraint(scope_id, "LAMMPS request", "target_temperature", value, unit or "K", source_ref, run_id))
    steps = _extract_steps(message)
    if steps is not None:
        items.append(_constraint(scope_id, "LAMMPS request", "steps", steps, "steps", source_ref, run_id))
    ensemble_match = _ENSEMBLE_PATTERN.search(message)
    if ensemble_match:
        items.append(_constraint(scope_id, "LAMMPS request", "ensemble", ensemble_match.group("ensemble").upper(), "", source_ref, run_id))
    task_match = _TASK_PATTERN.search(message)
    if task_match:
        task_value = task_match.group(0)
        items.append(_constraint(scope_id, "LAMMPS request", "task_type", task_value, "", source_ref, run_id))
    return items


def _phase_request_constraints(*, scope_id: str, request: AgentChatRequest, message: str, source_ref: str, run_id: str) -> list[MemoryItem]:
    system_name = request.system_name.strip()
    if not system_name:
        match = _PHASE_SYSTEM_PATTERN.search(message)
        system_name = match.group(1).replace("/", "-").replace(" ", "") if match else ""
    items: list[MemoryItem] = []
    if system_name:
        items.append(_constraint(scope_id, "phase diagram request", "system_name", system_name, "", source_ref, run_id))
    items.append(_constraint(scope_id, "phase diagram request", "temperature_min", request.temperature_min, "K", source_ref, run_id))
    items.append(_constraint(scope_id, "phase diagram request", "temperature_max", request.temperature_max, "K", source_ref, run_id))
    return items


def _constraint(scope_id: str, subject: str, predicate: str, value: Any, unit: str, source_ref: str, run_id: str) -> MemoryItem:
    return MemoryItem(
        scope_type="conversation",
        scope_id=scope_id,
        item_type="constraint",
        subject=subject,
        predicate=predicate,
        value=value,
        unit=unit,
        text=f"{subject} {predicate}={value} {unit}".strip(),
        authority="user",
        source_refs=[source_ref],
        metadata={"locked": True, "run_id": run_id},
    )


def _extract_material(message: str) -> str:
    match = _MATERIAL_PATTERN.search(message)
    if not match:
        return ""
    token = match.group(0).strip().lower()
    return _LAMMPS_MATERIAL_ALIASES.get(token, token.capitalize())


def _extract_temperature(message: str) -> tuple[float, str] | None:
    match = _TEMPERATURE_PATTERN.search(message)
    if not match:
        return None
    value = match.group("value") or match.group("zh_value")
    unit = match.group("unit") or "K"
    return (float(value), unit)


def _extract_steps(message: str) -> int | None:
    match = _STEPS_PATTERN.search(message)
    if not match:
        return None
    value = match.group("value") or match.group("zh_value")
    return int(value)
