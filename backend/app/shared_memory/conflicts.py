from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.orchestration.fingerprint import stable_content_hash
from app.shared_memory.canonicalization import canonicalize_key_text, canonicalize_scalar_measurement
from app.shared_memory.models import ConflictRecord, MemoryItem


_AUTHORITY_RANK = {
    "user": 100,
    "execution": 85,
    "registry": 75,
    "validated_document": 72,
    "rag": 50,
    "llm_inference": 20,
}
_VERSION_KEYS = ("version", "revision", "plan_version", "attempt", "attempt_id", "run_attempt")
_SUPERSEDES_KEYS = ("supersedes_memory_id", "supersedes", "replaces_memory_id", "replaces")
_CONTEXT_KEYS = (
    "material",
    "system",
    "system_name",
    "composition",
    "phase",
    "temperature",
    "target_temperature",
    "pressure",
    "ensemble",
    "run_mode",
    "condition",
)
_NEGATION_MARKERS = (
    " not ",
    " no ",
    " without ",
    " failed to ",
    " unable to ",
    " cannot ",
    " can't ",
    "禁止",
    "没有",
    "未",
    "无法",
    "不能",
    "不支持",
    "失败",
)
_ANTONYM_GROUPS = (
    ("stable", "unstable"),
    ("supported", "unsupported"),
    ("real", "mock", "synthetic"),
    ("passed", "failed"),
    ("pass", "fail"),
    ("increase", "decrease"),
    ("present", "missing", "absent"),
    ("finite", "nan", "inf", "infinite"),
    ("真实", "模拟", "合成", "伪造"),
    ("存在", "缺失", "没有"),
    ("通过", "失败"),
    ("支持", "不支持"),
)


@dataclass(frozen=True)
class ConflictDecision:
    conflict_type: str
    detection_mode: str = "structured"
    reason: str = ""
    metadata: dict[str, Any] | None = None


def deterministic_conflict_id(left_id: str, right_id: str, conflict_type: str) -> str:
    ordered = sorted([left_id, right_id])
    digest = stable_content_hash({"left": ordered[0], "right": ordered[1], "type": conflict_type})
    return f"conf-{digest[:16]}"


def detect_structured_conflicts(*, incoming: MemoryItem, existing_items: list[MemoryItem]) -> list[ConflictRecord]:
    """Detect conservative same-key conflicts before memory is used by agents.

    This detector intentionally stays local to a scope. Exact-key conflicts can
    be structured/heuristic; similar-key conflicts only produce
    `semantic_candidate` records and must not be auto-resolved.
    """

    conflicts: list[ConflictRecord] = []
    incoming_key = _key(incoming)
    incoming_value = _value_unit(incoming)
    for existing in existing_items:
        if existing.memory_id == incoming.memory_id:
            continue
        if existing.status != "active":
            continue
        if existing.scope_type != incoming.scope_type or existing.scope_id != incoming.scope_id:
            continue
        if existing.item_type != incoming.item_type:
            continue
        same_key = _key(existing) == incoming_key
        if same_key:
            decision = _conflict_decision(existing=existing, incoming=incoming, incoming_value=incoming_value)
        else:
            decision = _semantic_candidate_decision(existing=existing, incoming=incoming)
        if decision is None:
            continue
        hint = resolution_hint(existing=existing, incoming=incoming)
        conflicts.append(
            ConflictRecord(
                conflict_id=deterministic_conflict_id(existing.memory_id, incoming.memory_id, decision.conflict_type),
                left_memory_id=existing.memory_id,
                right_memory_id=incoming.memory_id,
                conflict_type=decision.conflict_type,
                detection_mode=decision.detection_mode,
                status=_default_conflict_status(resolution=hint),
                evidence_refs=_merge_refs(existing.source_refs, incoming.source_refs),
                metadata={
                    "left": _debug_signature(existing),
                    "right": _debug_signature(incoming),
                    "reason": decision.reason,
                    "resolution_hint": hint,
                    **(decision.metadata or {}),
                },
            )
        )
    return conflicts


def _key(item: MemoryItem) -> tuple[str, str]:
    return (canonicalize_key_text(item.subject), canonicalize_key_text(item.predicate))


def _value_unit(item: MemoryItem) -> tuple[str, str]:
    value, unit = canonicalize_scalar_measurement(item.value, item.unit)
    return (value, canonicalize_key_text(unit))


def _conflict_decision(
    *,
    existing: MemoryItem,
    incoming: MemoryItem,
    incoming_value: tuple[str, str],
) -> ConflictDecision | None:
    version_reason = _version_conflict_reason(existing=existing, incoming=incoming)
    if version_reason:
        return ConflictDecision("version", "structured", version_reason)
    existing_value = _value_unit(existing)
    if existing_value[1] != incoming_value[1]:
        return ConflictDecision("unit", "structured", "canonical_unit_mismatch")
    context_reason = _context_conflict_reason(existing=existing, incoming=incoming)
    if context_reason and existing_value[0] != incoming_value[0]:
        return ConflictDecision("context", "structured", context_reason)
    heuristic_reason = _heuristic_polarity_reason(existing=existing, incoming=incoming)
    if heuristic_reason:
        return ConflictDecision("polarity", "heuristic", heuristic_reason)
    if existing_value[0] != incoming_value[0]:
        return ConflictDecision("value", "structured", "canonical_value_mismatch")
    if existing.polarity != incoming.polarity and "unknown" not in {existing.polarity, incoming.polarity}:
        return ConflictDecision("polarity", "structured", "explicit_polarity_mismatch")
    return None


def _semantic_candidate_decision(*, existing: MemoryItem, incoming: MemoryItem) -> ConflictDecision | None:
    """Generate possible conflicts for similar-but-not-identical keys.

    This deliberately returns `detection_mode='semantic_candidate'`: it is a
    review signal, not proof. The service must not automatically resolve or
    quarantine based on this mode alone.
    """

    key_similarity = _key_similarity(existing, incoming)
    if key_similarity < 0.5:
        return None
    existing_text = _semantic_text(existing)
    incoming_text = _semantic_text(incoming)
    topic_similarity = _topic_overlap(existing_text, incoming_text)
    if topic_similarity < 0.42:
        return None
    existing_value = _value_unit(existing)
    incoming_value = _value_unit(incoming)
    polarity_reason = _heuristic_polarity_reason(existing=existing, incoming=incoming)
    metadata = {
        "semantic_candidate": True,
        "key_similarity": round(key_similarity, 4),
        "topic_similarity": round(topic_similarity, 4),
    }
    if polarity_reason:
        return ConflictDecision(
            "polarity",
            "semantic_candidate",
            f"similar_key_{polarity_reason}",
            metadata,
        )
    if existing_value != incoming_value:
        return ConflictDecision(
            "context",
            "semantic_candidate",
            "similar_key_value_mismatch",
            metadata,
        )
    return None


def _version_conflict_reason(*, existing: MemoryItem, incoming: MemoryItem) -> str:
    incoming_supersedes = _supersedes_ids(incoming)
    existing_supersedes = _supersedes_ids(existing)
    if existing.memory_id in incoming_supersedes and existing.status == "active":
        return "incoming_supersedes_existing_but_existing_still_active"
    if incoming.memory_id in existing_supersedes and incoming.status == "active":
        return "incoming_marked_superseded_by_existing_but_is_active"
    existing_version = _version_marker(existing)
    incoming_version = _version_marker(incoming)
    if existing.status == "active" and incoming.status == "active" and existing_version and incoming_version:
        if existing_version != incoming_version and _value_unit(existing) == _value_unit(incoming):
            return "parallel_active_versions_for_same_fact"
    return ""


def _heuristic_polarity_reason(*, existing: MemoryItem, incoming: MemoryItem) -> str:
    existing_text = _semantic_text(existing)
    incoming_text = _semantic_text(incoming)
    if not existing_text or not incoming_text:
        return ""
    if _topic_overlap(existing_text, incoming_text) < 0.35:
        return ""
    if _has_opposing_antonyms(existing_text, incoming_text):
        return "domain_antonym_pair"
    if _has_negation(existing_text) != _has_negation(incoming_text):
        return "negation_flip"
    return ""


def _context_conflict_reason(*, existing: MemoryItem, incoming: MemoryItem) -> str:
    existing_context = _context_signature(existing)
    incoming_context = _context_signature(incoming)
    if not existing_context or not incoming_context:
        return ""
    shared_keys = set(existing_context).intersection(incoming_context)
    differing = [
        key
        for key in sorted(shared_keys)
        if existing_context.get(key) != incoming_context.get(key)
    ]
    if differing:
        return f"context_signature_mismatch:{','.join(differing)}"
    return ""


def _default_conflict_status(*, resolution: dict[str, Any]) -> str:
    if resolution.get("action") == "needs_user_confirmation":
        return "needs_user"
    return "open"


def resolution_hint(*, existing: MemoryItem, incoming: MemoryItem) -> dict[str, Any]:
    """Return an advisory conflict-resolution hint without mutating memory.

    The roadmap calls for authority/context/recency/user strategies, but the
    safe invariant is still "never auto-overwrite high-authority memory".
    Therefore this hint is stored for Blue/UX review while conflict status
    remains open unless a caller explicitly resolves it.
    """

    left_locked = _is_locked(existing)
    right_locked = _is_locked(incoming)
    if left_locked or right_locked:
        return {
            "strategy": "user_confirmation",
            "action": "needs_user_confirmation",
            "winner_memory_id": "",
            "rationale": "locked_memory_involved",
        }
    if existing.authority == "user" or incoming.authority == "user":
        return {
            "strategy": "user_confirmation",
            "action": "needs_user_confirmation",
            "winner_memory_id": "",
            "rationale": "user_memory_involved",
        }
    left_rank = _authority_rank(existing)
    right_rank = _authority_rank(incoming)
    if left_rank != right_rank:
        winner = existing if left_rank > right_rank else incoming
        loser = incoming if winner is existing else existing
        return {
            "strategy": "authority",
            "action": "prefer_higher_authority",
            "winner_memory_id": winner.memory_id,
            "loser_memory_id": loser.memory_id,
            "rationale": f"{winner.authority}>{loser.authority}",
        }
    left_time = _timestamp(existing.updated_at)
    right_time = _timestamp(incoming.updated_at)
    if left_time != right_time:
        winner = existing if left_time > right_time else incoming
        return {
            "strategy": "recency",
            "action": "prefer_newer_same_authority",
            "winner_memory_id": winner.memory_id,
            "rationale": "same_authority_newer_memory",
        }
    if existing.confidence != incoming.confidence:
        winner = existing if existing.confidence > incoming.confidence else incoming
        return {
            "strategy": "confidence",
            "action": "prefer_higher_confidence",
            "winner_memory_id": winner.memory_id,
            "rationale": "same_authority_higher_confidence",
        }
    return {
        "strategy": "manual_review",
        "action": "keep_open",
        "winner_memory_id": "",
        "rationale": "no_safe_automatic_resolution",
    }


def _debug_signature(item: MemoryItem) -> dict[str, str]:
    value, unit = _value_unit(item)
    return {
        "memory_id": item.memory_id,
        "subject": canonicalize_key_text(item.subject),
        "predicate": canonicalize_key_text(item.predicate),
        "value": value,
        "unit": unit,
        "polarity": item.polarity,
        "authority": item.authority,
        "status": item.status,
        "version": _version_marker(item),
        "context": str(_context_signature(item)),
    }


def _merge_refs(left: list[str], right: list[str]) -> list[str]:
    result: list[str] = []
    for value in [*left, *right]:
        normalized = value.strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _version_marker(item: MemoryItem) -> str:
    metadata = item.metadata or {}
    for key in _VERSION_KEYS:
        value = metadata.get(key)
        if value not in (None, ""):
            return f"{key}:{str(value).strip()}"
    return ""


def _supersedes_ids(item: MemoryItem) -> set[str]:
    metadata = item.metadata or {}
    values: list[Any] = []
    for key in _SUPERSEDES_KEYS:
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value not in (None, ""):
            values.append(value)
    return {str(value).strip() for value in values if str(value).strip()}


def _context_signature(item: MemoryItem) -> dict[str, str]:
    metadata = item.metadata or {}
    signature: dict[str, str] = {}
    for key in _CONTEXT_KEYS:
        value = metadata.get(key)
        if value in (None, ""):
            continue
        signature[key] = canonicalize_key_text(str(value))
    return signature


def _key_similarity(left: MemoryItem, right: MemoryItem) -> float:
    left_tokens = _semantic_tokens(f"{canonicalize_key_text(left.subject)} {canonicalize_key_text(left.predicate)}")
    right_tokens = _semantic_tokens(f"{canonicalize_key_text(right.subject)} {canonicalize_key_text(right.predicate)}")
    return _jaccard(left_tokens, right_tokens)


def _semantic_text(item: MemoryItem) -> str:
    parts = [
        item.subject,
        item.predicate,
        str(item.value),
        item.unit,
        item.text,
        item.normalized_text,
    ]
    return f" {canonicalize_key_text(' '.join(part for part in parts if part))} "


def _semantic_tokens(text: str) -> set[str]:
    return {token for token in text.split() if token and len(token) >= 2}


def _topic_overlap(left_text: str, right_text: str) -> float:
    left = _semantic_tokens(left_text)
    right = _semantic_tokens(right_text)
    if not left or not right:
        return 0.0
    return _jaccard(left, right)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = left.intersection(right)
    if not intersection:
        return 0.0
    return len(intersection) / len(left.union(right))


def _has_opposing_antonyms(left_text: str, right_text: str) -> bool:
    for group in _ANTONYM_GROUPS:
        left_hits = {term for term in group if _contains_term(left_text, term)}
        right_hits = {term for term in group if _contains_term(right_text, term)}
        if left_hits and right_hits and left_hits.isdisjoint(right_hits):
            return True
    return False


def _contains_term(text: str, term: str) -> bool:
    if any("\u4e00" <= character <= "\u9fff" for character in term):
        return term in text
    return f" {term.casefold()} " in text


def _has_negation(text: str) -> bool:
    return any(marker in text for marker in _NEGATION_MARKERS)


def _is_locked(item: MemoryItem) -> bool:
    metadata = item.metadata or {}
    return bool(metadata.get("locked") is True or metadata.get("locked_fact") is True)


def _authority_rank(item: MemoryItem) -> int:
    return _AUTHORITY_RANK.get(item.authority, 0)


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
