from app.lammps.review.convergence import RepairConvergenceReport, evaluate_repair_convergence, request_signature
from app.lammps.review.deterministic import build_deterministic_review_report
from app.lammps.review.evidence import (
    EvidenceBuilder,
    add_materials_rag_evidence_refs,
    blocking_findings_have_primary_evidence,
    format_materials_rag_l2_context,
)
from app.lammps.review.json_parser import ParsedPayload, parse_review_payload
from app.lammps.review.models import EvidenceRef, Finding, LLMReviewAdvisory, PatchOperation, RepairPatch, ReviewReport, ReviewScore
from app.lammps.review.policy import (
    BluePatchResolution,
    PatchPolicyReport,
    build_patch_from_llm_payload,
    build_patch_from_request_payload,
    verify_and_apply_patch,
)

__all__ = [
    "EvidenceBuilder",
    "EvidenceRef",
    "Finding",
    "LLMReviewAdvisory",
    "ParsedPayload",
    "BluePatchResolution",
    "PatchOperation",
    "PatchPolicyReport",
    "RepairConvergenceReport",
    "RepairPatch",
    "ReviewReport",
    "ReviewScore",
    "add_materials_rag_evidence_refs",
    "blocking_findings_have_primary_evidence",
    "build_deterministic_review_report",
    "build_patch_from_llm_payload",
    "build_patch_from_request_payload",
    "evaluate_repair_convergence",
    "format_materials_rag_l2_context",
    "parse_review_payload",
    "request_signature",
    "verify_and_apply_patch",
]
