from __future__ import annotations

import re
from typing import Any

from app.lammps.review.evidence import (
    EvidenceBuilder,
    add_materials_rag_evidence_refs,
    add_shared_memory_evidence_refs,
    blocking_findings_have_primary_evidence,
)
from app.lammps.review.models import EvidenceRef, Finding, ReviewReport, ReviewScore
from app.state import ArtifactRef, LammpsRequest


REQUIRED_POST_ARTIFACTS = ("in.lammps", "thermo.csv", "plot.png", "report.md")


def build_deterministic_review_report(
    *,
    request: LammpsRequest,
    mode: str,
    artifacts: list[ArtifactRef],
    metrics: dict[str, Any],
    validation: dict[str, Any],
    error: str,
    input_script: str,
    quality_report: dict[str, Any] | None = None,
    phase: str = "post_execution",
    shared_memory_context: dict[str, Any] | None = None,
    materials_rag_context: dict[str, Any] | None = None,
) -> ReviewReport:
    evidence = EvidenceBuilder()
    findings: list[Finding] = []
    artifact_names = [artifact.name for artifact in artifacts]

    request_evidence = evidence.add(
        source_type="user",
        source_ref="lammps_request",
        claim=(
            "Structured LAMMPS request: "
            f"material={request.material}, task_type={request.task_type}, "
            f"temperature={request.temperature} K, steps={request.steps}, "
            f"ensemble={request.ensemble}, time_step={request.time_step}, dump_file={request.dump_file}."
        ),
        metadata={"request": request.model_dump(mode="json")},
    )
    artifact_evidence = evidence.add(
        source_type="artifact",
        source_ref="artifact_manifest",
        claim=f"Available artifacts: {', '.join(sorted(artifact_names)) or '(none)'}",
        metadata={"artifact_names": artifact_names},
    )
    validation_evidence = evidence.add(
        source_type="validation",
        source_ref="lammps_validation",
        claim=f"Validation output: {validation}",
        metadata={"validation": validation},
    )
    script_evidence = evidence.add(
        source_type="script",
        source_ref="in.lammps",
        claim=input_script[:4000],
        metadata={"script_length": len(input_script)},
    )
    execution_evidence = evidence.add(
        source_type="execution",
        source_ref="lammps_execute",
        claim=f"mode={mode}; error={error or '(none)'}; metrics={metrics}",
        metadata={"mode": mode, "error": error, "metrics": metrics},
    )
    shared_memory_evidence = add_shared_memory_evidence_refs(evidence, shared_memory_context)
    materials_rag_evidence = add_materials_rag_evidence_refs(evidence, materials_rag_context)

    for required in REQUIRED_POST_ARTIFACTS:
        if required not in artifact_names:
            findings.append(
                Finding(
                    dimension="artifact",
                    severity="blocking",
                    message=f"Missing required artifact: {required}.",
                    evidence_refs=[artifact_evidence.evidence_id],
                    repairable=True,
                    suggested_action="verify",
                )
            )

    for item in validation.get("errors", []) if isinstance(validation.get("errors"), list) else []:
        findings.append(
            Finding(
                dimension="parameter",
                severity="blocking",
                message=str(item),
                evidence_refs=[validation_evidence.evidence_id],
                repairable=True,
                suggested_action="modify",
            )
        )

    if error and mode == "real":
        findings.append(
            Finding(
                dimension="physics",
                severity="blocking",
                message=str(error),
                evidence_refs=[execution_evidence.evidence_id],
                repairable=True,
                suggested_action="retry",
            )
        )

    if mode == "mock":
        findings.append(
            Finding(
                dimension="consistency",
                severity="warning",
                message="This LAMMPS run used mock fallback instead of a real local executable.",
                evidence_refs=[execution_evidence.evidence_id],
                repairable=False,
                suggested_action="none",
            )
        )

    lowered_script = input_script.lower()
    if input_script and ("thermo_style" not in lowered_script or "thermo " not in lowered_script):
        findings.append(
            Finding(
                dimension="script",
                severity="blocking",
                message="LAMMPS input script does not contain required thermo output commands.",
                evidence_refs=[script_evidence.evidence_id],
                repairable=True,
                suggested_action="add",
            )
        )
    if input_script and "dump" not in lowered_script:
        findings.append(
            Finding(
                dimension="script",
                severity="warning",
                message="LAMMPS input script does not appear to configure a dump/trajectory output.",
                evidence_refs=[script_evidence.evidence_id],
                repairable=True,
                suggested_action="add",
            )
        )

    quality = quality_report or {}
    findings.extend(
        _request_script_result_consistency_findings(
            request=request,
            input_script=input_script,
            metrics=metrics,
            quality_report=quality,
            mode=mode,
            request_evidence=request_evidence,
            script_evidence=script_evidence,
            execution_evidence=execution_evidence,
        )
    )

    if quality:
        quality_evidence = evidence.add(
            source_type="quality_report",
            source_ref="quality_report.json",
            claim=f"Physical quality report: passed={quality.get('passed')}; scientific={quality.get('scientific_result_passed')}; synthetic={quality.get('synthetic_thermo')}",
            metadata={"quality_report": quality},
        )
        if quality.get("passed") is False:
            issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
            messages = [str(item).strip() for item in issues if str(item).strip()] or ["Physical quality gate failed."]
            for message in messages:
                findings.append(
                    Finding(
                        dimension="physics",
                        severity="blocking",
                        message=message,
                        evidence_refs=[quality_evidence.evidence_id],
                        repairable=True,
                        suggested_action="verify",
                    )
                )
        if mode == "real" and quality.get("synthetic_thermo") is True:
            findings.append(
                Finding(
                    dimension="physics",
                    severity="blocking",
                    message="Real run is marked with synthetic thermo; refusing scientific success.",
                    evidence_refs=[quality_evidence.evidence_id],
                    repairable=False,
                    suggested_action="terminate",
                )
            )
        if mode == "mock" and quality.get("scientific_result_passed") is True:
            findings.append(
                Finding(
                    dimension="physics",
                    severity="blocking",
                    message="Mock mode cannot be marked as scientific_result_passed.",
                    evidence_refs=[quality_evidence.evidence_id],
                    repairable=True,
                    suggested_action="verify",
                )
            )
        if mode == "mock" and quality.get("scientific_result_passed") is False:
            findings.append(
                Finding(
                    dimension="physics",
                    severity="warning",
                    message="Mock output is workflow evidence only; scientific_result_passed is false.",
                    evidence_refs=[quality_evidence.evidence_id],
                    repairable=False,
                    suggested_action="none",
                )
            )
    elif phase == "post_execution":
        findings.append(
            Finding(
                dimension="physics",
                severity="blocking",
                message="Post-execution review is missing physical quality report.",
                evidence_refs=[artifact_evidence.evidence_id],
                repairable=True,
                suggested_action="verify",
            )
        )

    score = score_review(findings, evidence.refs, mode=mode, quality_report=quality, validation=validation)
    blocking = [finding for finding in findings if finding.severity == "blocking"]
    passed = not blocking and score.hard_gate_passed
    summary = (
        "Deterministic Red review passed. No blocking findings were detected."
        if passed
        else f"Deterministic Red review found {len(blocking)} blocking finding(s)."
    )
    return ReviewReport(
        phase="post_execution" if phase not in {"pre_execution", "post_execution"} else phase,  # type: ignore[arg-type]
        passed=passed,
        summary=summary,
        findings=findings,
        score=score,
        evidence_refs=evidence.refs,
        metadata={
            "mode": mode,
            "artifact_names": artifact_names,
            "request": request.model_dump(mode="json"),
            "primary_evidence_ok": blocking_findings_have_primary_evidence(findings, evidence.refs),
            "shared_memory": {
                "used": bool(shared_memory_evidence),
                "evidence_count": len(shared_memory_evidence),
                "selected_item_ids": [
                    str(ref.metadata.get("memory_id") or "")
                    for ref in shared_memory_evidence
                    if str(ref.metadata.get("memory_id") or "")
                ],
                "controlled_context_layers": ["L1_structured", "L2_digest", "L3_pointer"],
            },
            "materials_rag": {
                "used": bool(materials_rag_evidence),
                "evidence_count": len(materials_rag_evidence),
                "source_refs": [ref.source_ref for ref in materials_rag_evidence],
                "authority": "secondary",
            },
        },
    )


def score_review(
    findings: list[Finding],
    evidence_refs: list[Any],
    *,
    mode: str,
    quality_report: dict[str, Any],
    validation: dict[str, Any],
) -> ReviewScore:
    dimension_scores = {
        "parameter": 100.0,
        "script": 100.0,
        "consistency": 100.0,
        "evidence": 100.0,
        "physics": 100.0,
        "artifact": 100.0,
    }
    for finding in findings:
        penalty = 35.0 if finding.severity == "blocking" else 10.0 if finding.severity == "warning" else 2.0
        dimension_scores[finding.dimension] = max(0.0, dimension_scores[finding.dimension] - penalty)

    blocking_count = sum(1 for finding in findings if finding.severity == "blocking")
    locked_constraint_violations = sum(1 for finding in findings if finding.metadata.get("locked_constraint_violation") is True)
    hard_gate_passed = blocking_count == 0
    if validation.get("errors"):
        hard_gate_passed = False
    if quality_report and quality_report.get("passed") is False:
        hard_gate_passed = False
    if mode == "real" and quality_report and quality_report.get("scientific_result_passed") is False:
        hard_gate_passed = False
    if not blocking_findings_have_primary_evidence(findings, evidence_refs):
        hard_gate_passed = False
        dimension_scores["evidence"] = min(dimension_scores["evidence"], 59.0)

    overall = (
        dimension_scores["parameter"] * 0.25
        + dimension_scores["consistency"] * 0.20
        + dimension_scores["script"] * 0.20
        + dimension_scores["physics"] * 0.25
        + dimension_scores["evidence"] * 0.10
    )
    if blocking_count:
        overall = min(overall, 59.0)
    return ReviewScore(
        factual_correctness=round(dimension_scores["parameter"], 3),
        logical_consistency=round(dimension_scores["consistency"], 3),
        script_safety=round(dimension_scores["script"], 3),
        physical_validity=round(dimension_scores["physics"], 3),
        evidence_quality=round(dimension_scores["evidence"], 3),
        overall_score=round(overall, 3),
        blocking_findings=blocking_count,
        locked_constraint_violations=locked_constraint_violations,
        hard_gate_passed=hard_gate_passed,
        metadata={
            "mode": mode,
            "quality_passed": quality_report.get("passed") if quality_report else None,
            "scientific_result_passed": quality_report.get("scientific_result_passed") if quality_report else None,
        },
    )


def _request_script_result_consistency_findings(
    *,
    request: LammpsRequest,
    input_script: str,
    metrics: dict[str, Any],
    quality_report: dict[str, Any],
    mode: str,
    request_evidence: EvidenceRef,
    script_evidence: EvidenceRef,
    execution_evidence: EvidenceRef,
) -> list[Finding]:
    findings: list[Finding] = []
    parsed_script = _parse_lammps_script_contract(input_script)
    script_request_refs = [request_evidence.evidence_id, script_evidence.evidence_id]
    request_result_refs = [request_evidence.evidence_id, execution_evidence.evidence_id]

    script_target_temp = parsed_script.get("target_temperature")
    if script_target_temp is not None and not _near_equal(script_target_temp, float(request.temperature), tolerance=0.5):
        findings.append(
            Finding(
                dimension="consistency",
                severity="blocking",
                message=(
                    "LAMMPS script target temperature does not match the structured request: "
                    f"script={_format_number(script_target_temp)} K, request={request.temperature} K."
                ),
                evidence_refs=script_request_refs,
                repairable=True,
                suggested_action="modify",
                metadata={
                    "field": "temperature",
                    "script_value": script_target_temp,
                    "request_value": request.temperature,
                    "locked_constraint_violation": True,
                },
            )
        )

    script_run_steps = parsed_script.get("run_steps")
    if script_run_steps is not None and int(round(script_run_steps)) != int(request.steps):
        findings.append(
            Finding(
                dimension="consistency",
                severity="blocking",
                message=(
                    "LAMMPS script run steps do not match the structured request: "
                    f"script={int(round(script_run_steps))}, request={request.steps}."
                ),
                evidence_refs=script_request_refs,
                repairable=True,
                suggested_action="modify",
                metadata={
                    "field": "steps",
                    "script_value": script_run_steps,
                    "request_value": request.steps,
                    "locked_constraint_violation": True,
                },
            )
        )

    script_time_step = parsed_script.get("time_step")
    if script_time_step is not None and not _near_equal(script_time_step, float(request.time_step), tolerance=1e-9):
        findings.append(
            Finding(
                dimension="consistency",
                severity="blocking",
                message=(
                    "LAMMPS script timestep does not match the structured request: "
                    f"script={_format_number(script_time_step)} ps, request={request.time_step} ps."
                ),
                evidence_refs=script_request_refs,
                repairable=True,
                suggested_action="modify",
                metadata={"field": "time_step", "script_value": script_time_step, "request_value": request.time_step},
            )
        )

    script_dump_file = str(parsed_script.get("dump_file") or "").strip()
    request_dump_file = str(request.dump_file or "dump.atom").strip()
    if script_dump_file and request_dump_file and script_dump_file != request_dump_file:
        findings.append(
            Finding(
                dimension="consistency",
                severity="blocking",
                message=(
                    "LAMMPS script dump file does not match the structured request: "
                    f"script={script_dump_file}, request={request_dump_file}."
                ),
                evidence_refs=script_request_refs,
                repairable=True,
                suggested_action="modify",
                metadata={"field": "dump_file", "script_value": script_dump_file, "request_value": request_dump_file},
            )
        )

    if quality_report:
        requested_steps = _float_or_none(quality_report.get("requested_steps"))
        if requested_steps is not None and int(round(requested_steps)) != int(request.steps):
            findings.append(
                Finding(
                    dimension="consistency",
                    severity="blocking",
                    message=(
                        "Physical quality report requested_steps does not match the structured request: "
                        f"quality={int(round(requested_steps))}, request={request.steps}."
                    ),
                    evidence_refs=request_result_refs,
                    repairable=True,
                    suggested_action="verify",
                    metadata={
                        "field": "requested_steps",
                        "quality_value": requested_steps,
                        "request_value": request.steps,
                        "locked_constraint_violation": True,
                    },
                )
            )
        run_mode = str(quality_report.get("run_mode") or "").strip()
        if run_mode and run_mode != mode:
            findings.append(
                Finding(
                    dimension="consistency",
                    severity="blocking",
                    message=f"Runtime mode contradicts physical quality report: runtime={mode}, quality={run_mode}.",
                    evidence_refs=request_result_refs,
                    repairable=True,
                    suggested_action="verify",
                    metadata={"field": "run_mode", "runtime_value": mode, "quality_value": run_mode},
                )
            )
        metrics_synthetic = metrics.get("synthetic_thermo")
        quality_synthetic = quality_report.get("synthetic_thermo")
        if metrics_synthetic is not None and quality_synthetic is not None and bool(metrics_synthetic) != bool(quality_synthetic):
            findings.append(
                Finding(
                    dimension="consistency",
                    severity="blocking",
                    message=(
                        "Metrics synthetic_thermo contradicts physical quality report: "
                        f"metrics={bool(metrics_synthetic)}, quality={bool(quality_synthetic)}."
                    ),
                    evidence_refs=request_result_refs,
                    repairable=True,
                    suggested_action="verify",
                    metadata={
                        "field": "synthetic_thermo",
                        "metrics_value": bool(metrics_synthetic),
                        "quality_value": bool(quality_synthetic),
                    },
                )
            )
    return findings


def _parse_lammps_script_contract(input_script: str) -> dict[str, float | str]:
    if not input_script:
        return {}
    parsed: dict[str, float | str] = {}
    target_temp = _first_number(
        input_script,
        (
            r"^\s*variable\s+targetTemp\s+equal\s+([-+]?\d+(?:\.\d+)?)\s*$",
            r"^\s*fix\s+\S+\s+all\s+\S+\s+temp\s+\S+\s+([-+]?\d+(?:\.\d+)?)\s+\S+",
        ),
    )
    if target_temp is not None:
        parsed["target_temperature"] = target_temp
    run_steps = _first_number(
        input_script,
        (
            r"^\s*variable\s+runSteps\s+equal\s+([-+]?\d+(?:\.\d+)?)\s*$",
            r"^\s*run\s+([-+]?\d+(?:\.\d+)?)\s*$",
        ),
    )
    if run_steps is not None:
        parsed["run_steps"] = run_steps
    time_step = _first_number(input_script, (r"^\s*timestep\s+([-+]?\d+(?:\.\d+)?)\s*$",))
    if time_step is not None:
        parsed["time_step"] = time_step
    dump_match = re.search(r"^\s*dump\s+\S+\s+\S+\s+\S+\s+\S+\s+([^\s]+)", input_script, flags=re.MULTILINE)
    if dump_match:
        parsed["dump_file"] = dump_match.group(1)
    return parsed


def _first_number(input_script: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, input_script, flags=re.MULTILINE | re.IGNORECASE)
        if not match:
            continue
        value = _float_or_none(match.group(1))
        if value is not None:
            return value
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _near_equal(left: float, right: float, *, tolerance: float) -> bool:
    return abs(left - right) <= tolerance


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"
