from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EvidenceSourceType = Literal["user", "registry", "config", "artifact", "log", "rag", "llm_inference", "validation", "quality_report", "script", "execution"]
EvidenceAuthority = Literal["primary", "secondary", "advisory"]
FindingDimension = Literal["parameter", "script", "consistency", "evidence", "physics", "artifact"]
FindingSeverity = Literal["info", "warning", "blocking"]
SuggestedAction = Literal["add", "delete", "modify", "verify", "clarify", "retry", "terminate", "none"]
ReviewPhase = Literal["pre_execution", "post_execution"]
PatchOperationName = Literal["add", "delete", "modify", "verify"]
PatchRisk = Literal["low", "medium", "high"]


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def stable_hash(payload: str) -> str:
    return sha256(payload.encode("utf-8")).hexdigest()


class EvidenceRef(BaseModel):
    evidence_id: str = Field(default_factory=lambda: _short_id("ev"))
    source_type: EvidenceSourceType
    source_ref: str
    claim: str
    authority: EvidenceAuthority = "primary"
    content_hash: str = ""
    supports: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_hash(self) -> "EvidenceRef":
        if not self.content_hash:
            self.content_hash = stable_hash(f"{self.source_type}|{self.source_ref}|{self.claim}")
        return self


class Finding(BaseModel):
    finding_id: str = Field(default_factory=lambda: _short_id("finding"))
    dimension: FindingDimension
    severity: FindingSeverity
    message: str
    evidence_refs: list[str] = Field(default_factory=list)
    repairable: bool = False
    suggested_action: SuggestedAction = "none"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _message_not_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("finding message cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _blocking_requires_evidence(self) -> "Finding":
        if self.severity == "blocking" and not self.evidence_refs:
            raise ValueError("blocking finding requires evidence_refs")
        return self


class ReviewScore(BaseModel):
    factual_correctness: float = 100.0
    logical_consistency: float = 100.0
    script_safety: float = 100.0
    physical_validity: float = 100.0
    evidence_quality: float = 100.0
    overall_score: float = 100.0
    blocking_findings: int = 0
    locked_constraint_violations: int = 0
    hard_gate_passed: bool = True
    score_source: str = "deterministic_rules"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewReport(BaseModel):
    schema_version: str = "lammps-red-review/v1"
    report_id: str = Field(default_factory=lambda: _short_id("review"))
    phase: ReviewPhase = "post_execution"
    passed: bool = True
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    score: ReviewScore = Field(default_factory=ReviewScore)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def blocking_findings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "blocking"]

    def warning_findings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]


class PatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: PatchOperationName
    path: str
    before: Any = None
    after: Any = None
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class RepairPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "lammps-blue-patch/v1"
    patch_id: str = Field(default_factory=lambda: _short_id("patch"))
    operations: list[PatchOperation] = Field(default_factory=list)
    requires_user_confirmation: bool = False
    risk: PatchRisk = "low"
    source: str = "deterministic_or_llm"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMReviewAdvisory(BaseModel):
    summary: str = ""
    confidence: float | None = None
    passed: bool | None = None
    blocking_issues: list[Any] = Field(default_factory=list)
    advisory_issues: list[Any] = Field(default_factory=list)
