from __future__ import annotations

import hashlib
import json
import re
from typing import Any


FREEZE_SCHEMA_VERSION = "materials-agent-freeze/v1"
SENSITIVE_KEY_MARKERS = ("api_key", "apikey", "secret", "token", "password", "credential")
PRIVATE_PATH_PATTERNS = (
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/private/var/"),
    re.compile(r"/var/folders/"),
    re.compile(r"[A-Za-z]:\\Users\\"),
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|or|ak)-[A-Za-z0-9_\-]{12,}", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET)\s*=\s*[^,\s]+", re.IGNORECASE),
)


def build_freeze_manifest(cases: list[Any], *, split: str = "frozen_test") -> dict[str, Any]:
    selected = [_case_dict(case) for case in cases if _case_dict(case).get("split") == split]
    case_hashes = {str(case["case_id"]): _case_content_hash(case) for case in selected}
    benchmark_versions = sorted({str(case.get("benchmark_version")) for case in selected if case.get("benchmark_version")})
    leakage = scan_case_data_leakage(cases, split=split)
    return {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "benchmark_version": benchmark_versions[0] if len(benchmark_versions) == 1 else None,
        "benchmark_versions": benchmark_versions,
        "split": split,
        "case_count": len(selected),
        "case_hashes": dict(sorted(case_hashes.items())),
        "split_hash": _hash_payload(dict(sorted(case_hashes.items()))),
        "data_leakage": {
            "ok": not leakage,
            "issue_count": len(leakage),
            "issues": leakage,
        },
        "hash_excludes": ["benchmark_version"],
    }


def validate_freeze_manifest(cases: list[Any], freeze_manifest: dict[str, Any], *, split: str = "frozen_test") -> dict[str, Any]:
    selected = [_case_dict(case) for case in cases if _case_dict(case).get("split") == split]
    current_hashes = {str(case["case_id"]): _case_content_hash(case) for case in selected}
    expected_hashes = {str(case_id): str(case_hash) for case_id, case_hash in (freeze_manifest.get("case_hashes") or {}).items()}
    current_versions = sorted({str(case.get("benchmark_version")) for case in selected if case.get("benchmark_version")})
    expected_version = freeze_manifest.get("benchmark_version")
    same_version = len(current_versions) == 1 and current_versions[0] == expected_version
    errors: list[str] = []
    changes = {
        "added": sorted(set(current_hashes) - set(expected_hashes)),
        "removed": sorted(set(expected_hashes) - set(current_hashes)),
        "changed": sorted(case_id for case_id in set(current_hashes) & set(expected_hashes) if current_hashes[case_id] != expected_hashes[case_id]),
    }
    if same_version:
        for case_id in changes["added"]:
            errors.append(f"frozen case added without benchmark version bump: {case_id}")
        for case_id in changes["removed"]:
            errors.append(f"frozen case removed without benchmark version bump: {case_id}")
        for case_id in changes["changed"]:
            errors.append(f"frozen case changed without benchmark version bump: {case_id}")
    leakage = scan_case_data_leakage(cases, split=split)
    errors.extend(f"data leakage in {issue['case_id']} at {issue['path']}: {issue['reason']}" for issue in leakage)
    warnings: list[str] = []
    if not same_version and any(changes.values()):
        warnings.append("frozen case content changed with benchmark version bump")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "changes": changes,
        "expected_version": expected_version,
        "current_versions": current_versions,
        "same_version": same_version,
        "data_leakage": leakage,
        "current_split_hash": _hash_payload(dict(sorted(current_hashes.items()))),
        "expected_split_hash": freeze_manifest.get("split_hash"),
    }


def scan_case_data_leakage(cases: list[Any], *, split: str | None = "frozen_test") -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for case in cases:
        payload = _case_dict(case)
        if split is not None and payload.get("split") != split:
            continue
        issues.extend(_scan_value(payload, path="$", case_id=str(payload.get("case_id") or "<unknown>")))
    return issues


def _scan_value(value: Any, *, path: str, case_id: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            key_lower = str(key).lower()
            if any(marker in key_lower for marker in SENSITIVE_KEY_MARKERS):
                issues.append(_issue(case_id, child_path, "sensitive key name", child))
            issues.extend(_scan_value(child, path=child_path, case_id=case_id))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_scan_value(child, path=f"{path}[{index}]", case_id=case_id))
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in PRIVATE_PATH_PATTERNS):
            issues.append(_issue(case_id, path, "private filesystem path", value))
        if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
            issues.append(_issue(case_id, path, "secret-looking value", value))
    return issues


def _issue(case_id: str, path: str, reason: str, value: Any) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "path": path,
        "reason": reason,
        "value_preview": _preview(value),
    }


def _preview(value: Any) -> str:
    text = str(value)
    if len(text) <= 12:
        return "<redacted>"
    return f"{text[:4]}…{text[-4:]}"


def _case_content_hash(case_payload: dict[str, Any]) -> str:
    payload = dict(case_payload)
    payload.pop("benchmark_version", None)
    return _hash_payload(payload)


def _hash_payload(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case_dict(case: Any) -> dict[str, Any]:
    if hasattr(case, "to_dict"):
        return dict(case.to_dict())
    if isinstance(case, dict):
        return dict(case)
    raise TypeError(f"unsupported case type: {type(case)!r}")
