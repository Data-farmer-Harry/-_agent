from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.orchestration.dag import DAGExecutionContext, DAGNode, DAGNodeResult


REUSABLE_STATUSES = {"completed", "completed_with_fallback"}
REUSE_CACHE_KEYS = ("reuse_node_results", "checkpoint_node_results", "reusable_node_results")


class NodeFingerprint(BaseModel):
    schema_version: str = "dag-node-fingerprint/v1"
    node_id: str
    node_type: str
    input_hash: str
    config_signature: str = ""
    config_signature_hash: str
    dependency_result_hashes: dict[str, str] = Field(default_factory=dict)
    node_fingerprint: str


def stable_content_hash(value: Any) -> str:
    """Return a deterministic SHA-256 hash for JSON-like content."""

    return sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def stable_json_dumps(value: Any) -> str:
    """Canonical JSON serialization used by checkpoint/replan fingerprints."""

    return _json_dumps(_normalize(value))


def build_node_fingerprint(
    node: DAGNode,
    context: DAGExecutionContext,
    dependency_results: Mapping[str, DAGNodeResult] | None = None,
) -> NodeFingerprint:
    dependency_results = dependency_results or {}
    input_hash = stable_content_hash(_node_input_payload(node, context))
    config_signature = context.config_signature or ""
    dependency_hashes = {
        dependency: str(result.metadata.get("result_hash") or stable_content_hash(result.model_dump(mode="json")))
        for dependency, result in sorted(dependency_results.items())
    }
    fingerprint_payload = {
        "schema_version": "dag-node-fingerprint/v1",
        "node_id": node.node_id,
        "node_type": node.node_type,
        "input_hash": input_hash,
        "config_signature_hash": stable_content_hash(config_signature),
        "dependency_result_hashes": dependency_hashes,
        "static_fingerprint": node.metadata.get("static_fingerprint", ""),
        "fingerprint_parameters": node.metadata.get("fingerprint_parameters", {}),
    }
    return NodeFingerprint(
        node_id=node.node_id,
        node_type=node.node_type,
        input_hash=input_hash,
        config_signature=config_signature,
        config_signature_hash=fingerprint_payload["config_signature_hash"],
        dependency_result_hashes=dependency_hashes,
        node_fingerprint=stable_content_hash(fingerprint_payload),
    )


def build_result_fingerprint_metadata(
    *,
    node: DAGNode,
    context: DAGExecutionContext,
    dependency_results: Mapping[str, DAGNodeResult] | None,
    status: str,
    output: Mapping[str, Any] | None = None,
    error: str = "",
    failure_category: str = "",
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    node_fingerprint = build_node_fingerprint(node, context, dependency_results or {})
    output_hash = stable_content_hash(output or {})
    result_hash = stable_content_hash(
        {
            "schema_version": "dag-node-result/v1",
            "node_id": node.node_id,
            "status": status,
            "output_hash": output_hash,
            "error": error,
            "failure_category": failure_category,
            "evidence_refs": evidence_refs or [],
        }
    )
    return {
        **node_fingerprint.model_dump(mode="json"),
        "content_hash": result_hash,
        "output_hash": output_hash,
        "result_hash": result_hash,
        "reuse_safe": is_node_reuse_safe(node),
        "reuse_policy": str(node.metadata.get("reuse_policy") or _default_reuse_policy(node)),
    }


def is_node_reuse_safe(node: DAGNode) -> bool:
    """Return whether a completed node may be reused from checkpoint when hashes match."""

    if node.metadata.get("reuse_safe") is False or node.metadata.get("reusable") is False:
        return False
    if node.metadata.get("reuse_safe") is True or node.metadata.get("reusable") is True:
        return True
    return _default_reuse_policy(node) != "disabled"


def result_has_reuse_fingerprint(result: DAGNodeResult) -> bool:
    metadata = result.metadata or {}
    required_text = ("input_hash", "config_signature_hash", "node_fingerprint", "result_hash")
    if not all(str(metadata.get(key) or "").strip() for key in required_text):
        return False
    return isinstance(metadata.get("dependency_result_hashes"), dict)


def can_reuse_cached_result(
    *,
    node: DAGNode,
    context: DAGExecutionContext,
    dependency_results: Mapping[str, DAGNodeResult] | None,
    cached_result: DAGNodeResult,
) -> tuple[bool, str]:
    """Validate checkpoint reuse using input/config/dependency fingerprints."""

    if node.metadata.get("replan_status") != "reuse_checkpoint" and node.metadata.get("reuse_from_checkpoint") is not True:
        return False, "node_not_marked_for_checkpoint_reuse"
    if cached_result.status not in REUSABLE_STATUSES:
        return False, f"cached_status_not_reusable:{cached_result.status}"
    if not is_node_reuse_safe(node):
        return False, "node_not_reuse_safe"
    if not result_has_reuse_fingerprint(cached_result):
        return False, "cached_result_missing_reuse_fingerprint"

    current = build_node_fingerprint(node, context, dependency_results or {})
    metadata = cached_result.metadata or {}
    for key in ("input_hash", "config_signature_hash", "node_fingerprint"):
        if str(metadata.get(key) or "") != getattr(current, key):
            return False, f"{key}_mismatch"
    if dict(metadata.get("dependency_result_hashes") or {}) != current.dependency_result_hashes:
        return False, "dependency_result_hashes_mismatch"
    return True, "hash_match"


def load_reuse_cache(metadata: Mapping[str, Any] | None) -> dict[str, DAGNodeResult]:
    metadata = metadata or {}
    cache: dict[str, DAGNodeResult] = {}
    for key in REUSE_CACHE_KEYS:
        value = metadata.get(key)
        if not isinstance(value, Mapping):
            continue
        for node_id, raw_result in value.items():
            parsed = _coerce_node_result(raw_result)
            if parsed is not None:
                cache[str(node_id)] = parsed
    return cache


def reusable_node_ids(
    plan: Any,
    result: Any,
    *,
    invalidated_nodes: set[str] | list[str] | tuple[str, ...] | None = None,
) -> tuple[list[str], list[str]]:
    """Return `(reusable, non_reusable)` for non-invalidated completed nodes."""

    invalidated = set(invalidated_nodes or [])
    reusable: list[str] = []
    non_reusable: list[str] = []
    node_by_id = plan.node_map()
    for node_id in plan.topological_order():
        if node_id in invalidated:
            continue
        node = node_by_id[node_id]
        node_result = result.results.get(node_id)
        if node_result is None or node_result.status not in REUSABLE_STATUSES:
            non_reusable.append(node_id)
            continue
        dependency_hashes = node_result.metadata.get("dependency_result_hashes") if isinstance(node_result.metadata, dict) else {}
        dependency_hashes = dependency_hashes if isinstance(dependency_hashes, dict) else {}
        has_dependency_hashes = all(dependency in dependency_hashes for dependency in node.dependencies)
        if is_node_reuse_safe(node) and result_has_reuse_fingerprint(node_result) and has_dependency_hashes:
            reusable.append(node_id)
        else:
            non_reusable.append(node_id)
    return reusable, non_reusable


def _node_input_payload(node: DAGNode, context: DAGExecutionContext) -> dict[str, Any]:
    if not node.input_keys:
        selected_input: Any = context.input_payload
    else:
        selected_input = {key: _get_path(context.input_payload, key) for key in node.input_keys}
    return {
        "selected_input": selected_input,
        "static_fingerprint": node.metadata.get("static_fingerprint", ""),
        "fingerprint_parameters": node.metadata.get("fingerprint_parameters", {}),
    }


def _get_path(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _coerce_node_result(value: Any) -> DAGNodeResult | None:
    if isinstance(value, DAGNodeResult):
        return value
    if isinstance(value, Mapping):
        try:
            return DAGNodeResult.model_validate(value)
        except Exception:  # noqa: BLE001 - untrusted checkpoint cache entry; ignore.
            return None
    return None


def _default_reuse_policy(node: DAGNode) -> str:
    if node.resource_class == "simulation":
        return "disabled"
    return "safe_completed"


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalize(item) for item in value), key=_json_dumps)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, float) and not isfinite(value):
        return str(value)
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
