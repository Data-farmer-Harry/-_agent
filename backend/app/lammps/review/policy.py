from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.lammps.review.json_parser import ParsedPayload, parse_review_payload
from app.lammps.review.models import PatchOperation, RepairPatch
from app.lammps.validator import validate_request
from app.state import LammpsRequest


LOCKED_PATCH_PATHS = {
    "material",
    "task_type",
    "temperature",
    "steps",
    "custom_potential_path",
    "custom_structure_path",
    "custom_structure_format",
}
ALLOWED_PATCH_PATHS = {
    "time_step",
    "box_size",
    "dump_file",
    "potential_family",
    "ensemble",
    "initial_temp",
    "notes",
}
DELETABLE_PATCH_PATHS = {"initial_temp", "notes"}
VERIFY_PATHS = {"*", "lammps_request", "validation", "codegen", "red_review", "physical_quality"}


class PatchPolicyReport(BaseModel):
    schema_version: str = "lammps-patch-policy/v1"
    patch_id: str
    accepted: bool
    request_changed: bool = False
    requires_user_confirmation: bool = False
    risk: str = "low"
    applied_operations: list[dict[str, Any]] = Field(default_factory=list)
    rejected_operations: list[dict[str, Any]] = Field(default_factory=list)
    locked_constraint_violations: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    validation_report: dict[str, Any] = Field(default_factory=dict)
    before_request: dict[str, Any] = Field(default_factory=dict)
    after_request: dict[str, Any] = Field(default_factory=dict)
    termination_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BluePatchResolution(BaseModel):
    patch: RepairPatch
    parse_audit: ParsedPayload
    source: str
    fallback_used: bool = False
    legacy_payload_keys: list[str] = Field(default_factory=list)


def build_patch_from_llm_payload(
    request: LammpsRequest,
    *,
    raw_text: str,
    parsed_payload: dict[str, Any] | None,
    stage: str,
    issues: list[str],
) -> BluePatchResolution:
    parsed_patch = parse_review_payload(raw_text, schema=RepairPatch, payload_type="blue_patch")
    if parsed_patch.success and parsed_patch.payload.get("operations"):
        patch = RepairPatch.model_validate(parsed_patch.payload)
        patch.metadata = {
            **patch.metadata,
            "stage": stage,
            "issues": issues,
            "blue_parse_mode": parsed_patch.parse_mode,
            "blue_payload_source": "native_blue_patch",
        }
        return BluePatchResolution(
            patch=patch,
            parse_audit=parsed_patch,
            source="native_blue_patch",
            fallback_used=False,
        )

    if parsed_payload:
        patch = build_patch_from_request_payload(
            request,
            parsed_payload,
            stage=stage,
            issues=issues,
            source="llm_request_delta_fallback",
        )
        patch.metadata = {
            **patch.metadata,
            "blue_parse_mode": parsed_patch.parse_mode,
            "blue_payload_source": "request_delta_fallback",
        }
        return BluePatchResolution(
            patch=patch,
            parse_audit=parsed_patch,
            source="request_delta_fallback",
            fallback_used=True,
            legacy_payload_keys=sorted(str(key) for key in parsed_payload),
        )

    patch = RepairPatch(
        operations=[],
        source="unparseable_blue_patch",
        metadata={
            "stage": stage,
            "issues": issues,
            "blue_parse_mode": parsed_patch.parse_mode,
            "blue_payload_source": "rejected",
        },
    )
    return BluePatchResolution(
        patch=patch,
        parse_audit=parsed_patch,
        source="rejected",
        fallback_used=False,
    )


def build_patch_from_request_payload(
    request: LammpsRequest,
    payload: dict[str, Any],
    *,
    stage: str,
    issues: list[str],
    source: str = "llm_request_repair",
) -> RepairPatch:
    current = request.model_dump(mode="json")
    operations: list[PatchOperation] = []
    request_fields = set(LammpsRequest.model_fields)
    for key, value in payload.items():
        if key not in request_fields or value in (None, ""):
            continue
        before = current.get(key)
        if before == value:
            continue
        operations.append(
            PatchOperation(
                op="modify",
                path=key,
                before=before,
                after=value,
                reason=f"Repair suggestion from {stage}: {'; '.join(issues[:3])}",
            )
        )
    if operations:
        operations.append(
            PatchOperation(
                op="verify",
                path="lammps_request",
                reason="Every Blue patch must be revalidated before retry.",
            )
        )
    return RepairPatch(
        operations=operations,
        requires_user_confirmation=False,
        risk=_risk_for_paths([operation.path for operation in operations]),
        source=source,
        metadata={"stage": stage, "issues": issues},
    )


def verify_and_apply_patch(request: LammpsRequest, patch: RepairPatch) -> tuple[LammpsRequest | None, PatchPolicyReport]:
    before = request.model_dump(mode="json")
    candidate = dict(before)
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    locked_violations: list[str] = []
    verification_steps = ["pydantic_schema", "locked_constraints", "patch_policy"]
    requires_user_confirmation = patch.requires_user_confirmation

    for operation in patch.operations:
        normalized_path = operation.path.strip().lstrip("/")
        operation_payload = operation.model_dump(mode="json")
        operation_payload["normalized_path"] = normalized_path
        if operation.op == "verify":
            if normalized_path not in VERIFY_PATHS:
                operation_payload["reason"] = "Unknown VERIFY path."
                rejected.append(operation_payload)
            else:
                applied.append(operation_payload)
            continue
        if normalized_path in LOCKED_PATCH_PATHS:
            operation_payload["reason"] = "Locked user/scientific constraint requires user confirmation."
            locked_violations.append(normalized_path)
            rejected.append(operation_payload)
            requires_user_confirmation = True
            continue
        if normalized_path not in ALLOWED_PATCH_PATHS:
            operation_payload["reason"] = "Patch path is not in the Blue policy allow-list."
            rejected.append(operation_payload)
            continue
        if operation.op == "delete" and normalized_path not in DELETABLE_PATCH_PATHS:
            operation_payload["reason"] = "DELETE is only allowed for optional resettable fields."
            rejected.append(operation_payload)
            continue
        if operation.before not in (None, before.get(normalized_path)):
            operation_payload["reason"] = "Patch before-value does not match current request."
            rejected.append(operation_payload)
            continue
        if operation.op in {"add", "modify"}:
            candidate[normalized_path] = operation.after
            applied.append(operation_payload)
            continue
        if operation.op == "delete":
            candidate[normalized_path] = _field_default(normalized_path)
            applied.append(operation_payload)

    mutation_applied = any(item.get("op") in {"add", "modify", "delete"} for item in applied)
    validation_report: dict[str, Any] = {}
    after_request: dict[str, Any] = {}
    accepted = mutation_applied and not rejected and not requires_user_confirmation
    if mutation_applied:
        try:
            applied_request = LammpsRequest.model_validate(candidate)
            after_request = applied_request.model_dump(mode="json")
            validation_report = validate_request(after_request)
            verification_steps.append("lammps_validation")
            if not validation_report.get("is_reasonable"):
                accepted = False
                rejected.append(
                    {
                        "op": "verify",
                        "path": "lammps_validation",
                        "reason": "Patched request failed deterministic LAMMPS validation.",
                        "validation": validation_report,
                    }
                )
            if accepted:
                verification_steps.extend(["codegen_required_on_retry", "red_review_required_on_retry"])
                report = PatchPolicyReport(
                    patch_id=patch.patch_id,
                    accepted=True,
                    request_changed=after_request != before,
                    requires_user_confirmation=False,
                    risk=patch.risk,
                    applied_operations=applied,
                    rejected_operations=rejected,
                    locked_constraint_violations=locked_violations,
                    verification_steps=verification_steps,
                    validation_report=validation_report,
                    before_request=before,
                    after_request=after_request,
                    metadata=patch.metadata,
                )
                return applied_request, report
        except Exception as exc:  # noqa: BLE001 - report all schema/type failures as rejected patch verification.
            accepted = False
            rejected.append({"op": "verify", "path": "pydantic_schema", "reason": str(exc)})

    termination_reason = ""
    if requires_user_confirmation:
        termination_reason = "patch_requires_user_confirmation"
    elif rejected:
        termination_reason = "patch_policy_rejected"
    elif not mutation_applied:
        termination_reason = "patch_no_effect"
    report = PatchPolicyReport(
        patch_id=patch.patch_id,
        accepted=False,
        request_changed=False,
        requires_user_confirmation=requires_user_confirmation,
        risk="high" if locked_violations else patch.risk,
        applied_operations=applied,
        rejected_operations=rejected,
        locked_constraint_violations=locked_violations,
        verification_steps=verification_steps,
        validation_report=validation_report,
        before_request=before,
        after_request=after_request,
        termination_reason=termination_reason,
        metadata=patch.metadata,
    )
    return None, report


def _risk_for_paths(paths: list[str]) -> str:
    path_set = {path.strip().lstrip("/") for path in paths}
    if path_set & LOCKED_PATCH_PATHS:
        return "high"
    if path_set & {"potential_family", "ensemble"}:
        return "medium"
    return "low"


def _field_default(path: str) -> Any:
    field = LammpsRequest.model_fields[path]
    if field.default is not None:
        return field.default
    return None
