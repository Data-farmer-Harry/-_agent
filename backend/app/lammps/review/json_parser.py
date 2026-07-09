from __future__ import annotations

import json
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from app.lammps.review.models import stable_hash


PayloadType = Literal["red_review", "red_review_advisory", "blue_patch"]
ParseMode = Literal["strict", "normalized", "deterministic_fallback", "rejected"]
T = TypeVar("T", bound=BaseModel)


class ParsedPayload(BaseModel):
    success: bool
    parse_mode: ParseMode
    payload: dict[str, Any] = Field(default_factory=dict)
    normalizations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    raw_content_hash: str = ""
    payload_type: PayloadType


def parse_review_payload(
    raw_text: str,
    *,
    schema: type[T],
    payload_type: PayloadType,
    deterministic_payload: T | None = None,
) -> ParsedPayload:
    raw_hash = stable_hash(raw_text)
    errors: list[str] = []
    try:
        parsed = json.loads(raw_text)
        payload = schema.model_validate(parsed)
        return ParsedPayload(
            success=True,
            parse_mode="strict",
            payload=payload.model_dump(mode="json"),
            raw_content_hash=raw_hash,
            payload_type=payload_type,
        )
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
        errors.append(f"strict: {exc}")

    normalized_text, normalizations = _normalize_json_text(raw_text, payload_type=payload_type)
    try:
        parsed = json.loads(normalized_text)
        parsed = _normalize_payload_aliases(parsed, payload_type=payload_type, normalizations=normalizations)
        payload = schema.model_validate(parsed)
        return ParsedPayload(
            success=True,
            parse_mode="normalized",
            payload=payload.model_dump(mode="json"),
            normalizations=normalizations,
            errors=errors,
            raw_content_hash=raw_hash,
            payload_type=payload_type,
        )
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
        errors.append(f"normalized: {exc}")

    if deterministic_payload is not None:
        return ParsedPayload(
            success=True,
            parse_mode="deterministic_fallback",
            payload=deterministic_payload.model_dump(mode="json"),
            normalizations=normalizations,
            errors=errors,
            raw_content_hash=raw_hash,
            payload_type=payload_type,
        )
    return ParsedPayload(
        success=False,
        parse_mode="rejected",
        normalizations=normalizations,
        errors=errors,
        raw_content_hash=raw_hash,
        payload_type=payload_type,
    )


def _normalize_json_text(raw_text: str, *, payload_type: PayloadType) -> tuple[str, list[str]]:
    text = raw_text.strip()
    normalizations: list[str] = []
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
        normalizations.append("removed_markdown_code_fence")

    extracted = _extract_balanced_json_object(text)
    if extracted and extracted != text:
        text = extracted
        normalizations.append("extracted_first_balanced_json_object")

    without_trailing = re.sub(r",(\s*[}\]])", r"\1", text)
    if without_trailing != text:
        text = without_trailing
        normalizations.append("removed_trailing_commas")

    if payload_type == "blue_patch":
        lowered_ops = re.sub(
            r'"op"\s*:\s*"([A-Z]+)"',
            lambda match: f'"op": "{match.group(1).lower()}"',
            text,
        )
        if lowered_ops != text:
            text = lowered_ops
            normalizations.append("normalized_operation_case")
    return text, normalizations


def _extract_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text


def _normalize_payload_aliases(payload: Any, *, payload_type: PayloadType, normalizations: list[str]) -> Any:
    if not isinstance(payload, dict):
        return payload
    if payload_type == "red_review" and "findings" not in payload and "findings_list" in payload:
        payload = {**payload, "findings": payload["findings_list"]}
        normalizations.append("normalized_findings_list_alias")
    if payload_type == "blue_patch" and "operations" in payload and isinstance(payload["operations"], list):
        operations = []
        changed = False
        for operation in payload["operations"]:
            if isinstance(operation, dict) and isinstance(operation.get("op"), str):
                lowered = operation["op"].lower()
                if lowered != operation["op"]:
                    operation = {**operation, "op": lowered}
                    changed = True
            operations.append(operation)
        if changed:
            payload = {**payload, "operations": operations}
            normalizations.append("normalized_operation_case")
    return payload
