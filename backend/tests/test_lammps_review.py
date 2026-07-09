from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.core.artifacts import ArtifactService
from app.lammps.review import (
    PatchOperation,
    RepairPatch,
    ReviewReport,
    build_deterministic_review_report,
    build_patch_from_request_payload,
    parse_review_payload,
    verify_and_apply_patch,
)
from app.runtimes.lammps import LammpsRuntime
from app.state import ArtifactRef, LammpsRequest


def _request() -> LammpsRequest:
    return LammpsRequest(material="Cu", task_type="heating", temperature=800, steps=1000, potential_family="eam")


def _artifacts(*names: str) -> list[ArtifactRef]:
    return [ArtifactRef(kind="json" if name.endswith(".json") else "text", name=name, path=f"/tmp/{name}") for name in names]


class _FakeRepairLLM:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_system_prompt = ""
        self.last_user_prompt = ""

    def is_configured(self) -> bool:
        return True

    def chat_json(self, **_: object) -> dict[str, object]:
        self.calls += 1
        return self.payload

    def chat_text(self, **kwargs: object) -> str:
        self.calls += 1
        self.last_system_prompt = str(kwargs.get("system_prompt") or "")
        self.last_user_prompt = str(kwargs.get("user_prompt") or "")
        return json.dumps(self.payload, ensure_ascii=False)


def _accepted_repair_history(
    before: LammpsRequest,
    after: LammpsRequest,
    *,
    stage: str = "review",
    score_before_repair: float | None = None,
) -> dict[str, object]:
    return {
        "entry_type": "repair_attempt",
        "stage": stage,
        "issues": ["previous issue"],
        "raw_payload": after.model_dump(mode="json"),
        "policy_report": {
            "accepted": True,
            "before_request": before.model_dump(mode="json"),
            "after_request": after.model_dump(mode="json"),
        },
        "convergence_report": {
            "allow_repair": True,
            "score_before_repair": score_before_repair,
        },
    }


def _shared_memory_context() -> dict[str, object]:
    return {
        "available": True,
        "retrieval_backend": "bm25_query_rewrite_lexical_fallback",
        "selected_item_ids": ["mem-locked-temperature", "mem-rag-eam"],
        "candidates": [
            {
                "score": 10001.0,
                "reasons": ["forced_locked_fact", "subject"],
                "item": {
                    "memory_id": "mem-locked-temperature",
                    "scope_type": "conversation",
                    "scope_id": "review-test",
                    "item_type": "constraint",
                    "subject": "LAMMPS request",
                    "predicate": "target_temperature",
                    "value": 800,
                    "unit": "K",
                    "text": "User locked the LAMMPS target temperature at 800 K.",
                    "authority": "user",
                    "confidence": 1.0,
                    "status": "active",
                    "source_refs": ["run:test:user_request"],
                    "content_hash": "hash-temp",
                    "normalized_hash": "norm-temp",
                    "metadata": {"locked": True},
                },
            },
            {
                "score": 3.2,
                "reasons": ["bm25:2.100", "query_rewrite"],
                "item": {
                    "memory_id": "mem-rag-eam",
                    "scope_type": "conversation",
                    "scope_id": "review-test",
                    "item_type": "evidence",
                    "subject": "materials_rag:lammps.potential.eam",
                    "predicate": "supports_query",
                    "value": {"title": "EAM potential for Cu", "score": 2.5},
                    "unit": "",
                    "text": "Use EAM potentials for Cu metallic simulations.",
                    "authority": "rag",
                    "confidence": 1.0,
                    "status": "active",
                    "source_refs": ["https://example.org/cu-eam"],
                    "content_hash": "hash-eam",
                    "normalized_hash": "norm-eam",
                    "metadata": {"stage": "lammps_planning_materials_rag", "rank": 1},
                },
            },
        ],
    }


def _materials_rag_context() -> dict[str, object]:
    return {
        "planning": {
            "query": "Run Cu heating with EAM potential at 800 K.",
            "material": "Cu",
            "hits": [
                {
                    "title": "Cu EAM potential guidance",
                    "doc_type": "lammps_potential",
                    "score": 3.7,
                    "lexical_score": 1.2,
                    "bm25_score": 2.1,
                    "vector_score": 0.4,
                    "embedding_backend": "local_hash",
                    "matched_fields": ["material", "potential_family"],
                    "source": "materials_rag_seed",
                    "source_url": "https://example.org/cu-eam",
                }
            ],
            "source": "legacy_preflight",
        }
    }


def _complete_script(*, target_temp: int = 800, run_steps: int = 1000, time_step: float = 0.001, dump_file: str = "dump.atom") -> str:
    return "\n".join(
        [
            "units metal",
            f"variable targetTemp equal {target_temp}",
            f"variable runSteps equal {run_steps}",
            f"timestep {time_step}",
            "thermo 100",
            "thermo_style custom step temp pe ke etotal press",
            f"dump 1 all custom 100 {dump_file} id type x y z",
            f"fix 1 all nvt temp 300 {target_temp} 0.1",
            f"run {run_steps}",
        ]
    )


class LammpsReviewTests(unittest.TestCase):
    def test_deterministic_red_review_blocks_missing_artifact_with_primary_evidence(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="real",
            artifacts=_artifacts("in.lammps", "report.md"),
            metrics={},
            validation={"is_reasonable": True, "errors": []},
            error="",
            input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\n",
            quality_report={"passed": True, "scientific_result_passed": True, "synthetic_thermo": False},
        )

        self.assertFalse(report.passed)
        self.assertLessEqual(report.score.overall_score, 59.0)
        self.assertTrue(report.metadata["primary_evidence_ok"])
        blocking = report.blocking_findings()
        self.assertTrue(any("thermo.csv" in finding.message for finding in blocking))
        evidence_by_id = {item.evidence_id: item for item in report.evidence_refs}
        for finding in blocking:
            self.assertTrue(any(evidence_by_id[ref].authority == "primary" for ref in finding.evidence_refs))

    def test_deterministic_red_review_allows_mock_only_as_advisory(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="mock",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={"synthetic_thermo": True},
            validation={"is_reasonable": True, "errors": []},
            error="LAMMPS executable not found.",
            input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\ndump 1 all atom 100 dump.atom\n",
            quality_report={"passed": True, "scientific_result_passed": False, "synthetic_thermo": True},
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.score.blocking_findings, 0)
        self.assertTrue(any("mock fallback" in finding.message.lower() for finding in report.warning_findings()))

    def test_deterministic_red_review_blocks_real_synthetic_thermo(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="real",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={"synthetic_thermo": True},
            validation={"is_reasonable": True, "errors": []},
            error="",
            input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\ndump 1 all atom 100 dump.atom\n",
            quality_report={
                "passed": False,
                "scientific_result_passed": False,
                "synthetic_thermo": True,
                "issues": ["Real run is marked with synthetic thermo; refusing scientific success."],
            },
        )

        self.assertFalse(report.passed)
        self.assertTrue(any("synthetic thermo" in finding.message.lower() for finding in report.blocking_findings()))

    def test_deterministic_red_review_attaches_controlled_shared_memory_evidence(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="mock",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={"synthetic_thermo": True},
            validation={"is_reasonable": True, "errors": []},
            error="LAMMPS executable not found.",
            input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\ndump 1 all atom 100 dump.atom\n",
            quality_report={"passed": True, "scientific_result_passed": False, "synthetic_thermo": True},
            shared_memory_context=_shared_memory_context(),
        )

        memory_refs = [item for item in report.evidence_refs if item.source_ref.startswith("shared_memory:")]
        self.assertEqual(len(memory_refs), 2)
        locked_ref = next(item for item in memory_refs if item.metadata["memory_id"] == "mem-locked-temperature")
        rag_ref = next(item for item in memory_refs if item.metadata["memory_id"] == "mem-rag-eam")
        self.assertEqual(locked_ref.source_type, "user")
        self.assertEqual(locked_ref.authority, "primary")
        self.assertTrue(locked_ref.metadata["l1"]["locked"])
        self.assertEqual(locked_ref.metadata["l3_pointer"]["content_hash"], "hash-temp")
        self.assertEqual(rag_ref.source_type, "rag")
        self.assertEqual(rag_ref.authority, "secondary")
        self.assertTrue(report.metadata["shared_memory"]["used"])
        self.assertEqual(report.metadata["shared_memory"]["selected_item_ids"], ["mem-locked-temperature", "mem-rag-eam"])

    def test_deterministic_red_review_attaches_materials_rag_as_secondary_evidence(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="mock",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={"synthetic_thermo": True},
            validation={"is_reasonable": True, "errors": []},
            error="LAMMPS executable not found.",
            input_script=_complete_script(),
            quality_report={
                "run_mode": "mock",
                "passed": True,
                "scientific_result_passed": False,
                "synthetic_thermo": True,
                "requested_steps": 1000,
            },
            materials_rag_context=_materials_rag_context(),
        )

        rag_refs = [item for item in report.evidence_refs if item.source_type == "rag"]
        self.assertEqual(len(rag_refs), 1)
        self.assertEqual(rag_refs[0].authority, "secondary")
        self.assertEqual(rag_refs[0].source_ref, "https://example.org/cu-eam")
        self.assertEqual(rag_refs[0].metadata["rank"], 1)
        self.assertEqual(rag_refs[0].metadata["material"], "Cu")
        self.assertTrue(report.metadata["materials_rag"]["used"])
        self.assertEqual(report.metadata["materials_rag"]["authority"], "secondary")

    def test_deterministic_red_review_blocks_request_script_temperature_mismatch(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="real",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={"synthetic_thermo": False},
            validation={"is_reasonable": True, "errors": []},
            error="",
            input_script=_complete_script(target_temp=900),
            quality_report={
                "run_mode": "real",
                "passed": True,
                "scientific_result_passed": True,
                "synthetic_thermo": False,
                "requested_steps": 1000,
            },
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.score.locked_constraint_violations, 1)
        self.assertTrue(any("target temperature" in finding.message for finding in report.blocking_findings()))
        self.assertTrue(report.metadata["primary_evidence_ok"])

    def test_llm_red_review_blocking_candidates_remain_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=_FakeRepairLLM(
                    {
                        "summary": "LLM thinks this should fail.",
                        "confidence": 0.1,
                        "passed": False,
                        "blocking_issues": ["LLM-only speculative blocker"],
                        "advisory_issues": ["LLM-only advisory"],
                    }
                ),  # type: ignore[arg-type]
            )
            review = runtime._review_result(
                request=_request(),
                mode="mock",
                artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
                metrics={"synthetic_thermo": True},
                validation={"is_reasonable": True, "errors": []},
                error="LAMMPS executable not found.",
                input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\ndump 1 all atom 100 dump.atom\n",
                quality_report={"passed": True, "scientific_result_passed": False, "synthetic_thermo": True},
            )

        self.assertTrue(review["passed"])
        self.assertNotIn("LLM-only speculative blocker", review["issues"])
        self.assertIn("LLM-only speculative blocker", review["llm_blocking_candidates"])
        self.assertTrue(any("advisory only" in issue for issue in review["advisory_issues"]))
        self.assertTrue(review["llm_review_parse_audit"]["success"])
        self.assertEqual(review["llm_review_parse_audit"]["payload_type"], "red_review_advisory")

    def test_runtime_review_prompt_uses_materials_rag_secondary_context(self) -> None:
        fake_llm = _FakeRepairLLM(
            {
                "summary": "RAG evidence supports Cu EAM context.",
                "confidence": 0.8,
                "passed": True,
                "blocking_issues": [],
                "advisory_issues": [],
            }
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=fake_llm,  # type: ignore[arg-type]
            )
            review = runtime._review_result(
                request=_request(),
                mode="mock",
                artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
                metrics={"synthetic_thermo": True},
                validation={"is_reasonable": True, "errors": []},
                error="LAMMPS executable not found.",
                input_script=_complete_script(),
                quality_report={
                    "run_mode": "mock",
                    "passed": True,
                    "scientific_result_passed": False,
                    "synthetic_thermo": True,
                    "requested_steps": 1000,
                },
                materials_rag_context=_materials_rag_context(),
            )

        self.assertTrue(review["passed"])
        self.assertIn("Materials RAG controlled evidence", fake_llm.last_user_prompt)
        self.assertIn("secondary context only", fake_llm.last_user_prompt)
        self.assertIn("Cu EAM potential guidance", fake_llm.last_user_prompt)
        self.assertTrue(any(item["source_type"] == "rag" for item in review["evidence_refs"]))

    def test_review_json_parser_accepts_strict_payload(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="mock",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={},
            validation={"is_reasonable": True, "errors": []},
            error="",
            input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\ndump 1 all atom 100 dump.atom\n",
            quality_report={"passed": True, "scientific_result_passed": False, "synthetic_thermo": True},
        )

        parsed = parse_review_payload(report.model_dump_json(), schema=ReviewReport, payload_type="red_review")

        self.assertTrue(parsed.success)
        self.assertEqual(parsed.parse_mode, "strict")
        self.assertEqual(parsed.payload["schema_version"], "lammps-red-review/v1")

    def test_review_json_parser_normalizes_code_fence_alias_and_trailing_commas(self) -> None:
        report = build_deterministic_review_report(
            request=_request(),
            mode="mock",
            artifacts=_artifacts("in.lammps", "thermo.csv", "plot.png", "report.md"),
            metrics={},
            validation={"is_reasonable": True, "errors": []},
            error="",
            input_script="units metal\nthermo 100\nthermo_style custom step temp pe ke etotal press\ndump 1 all atom 100 dump.atom\n",
            quality_report={"passed": True, "scientific_result_passed": False, "synthetic_thermo": True},
        )
        payload = report.model_dump(mode="json")
        payload["findings_list"] = payload.pop("findings")
        raw = "Here is the review:\n```json\n" + json.dumps(payload, ensure_ascii=False)[:-1] + ",}\n```"

        parsed = parse_review_payload(raw, schema=ReviewReport, payload_type="red_review")

        self.assertTrue(parsed.success)
        self.assertEqual(parsed.parse_mode, "normalized")
        self.assertIn("normalized_findings_list_alias", parsed.normalizations)

    def test_blue_patch_parser_rejects_invalid_patch_without_fallback(self) -> None:
        raw = '{"operations": [{"op": "EXECUTE", "path": "shell", "after": "rm -rf /"}]}'

        parsed = parse_review_payload(raw, schema=RepairPatch, payload_type="blue_patch")

        self.assertFalse(parsed.success)
        self.assertEqual(parsed.parse_mode, "rejected")
        self.assertIn("normalized", " ".join(parsed.errors))

    def test_blue_patch_policy_applies_allowed_request_patch(self) -> None:
        request = _request()
        patch = build_patch_from_request_payload(
            request,
            {"time_step": 0.002, "box_size": 5},
            stage="validation",
            issues=["time step too small for smoke run"],
        )

        repaired, report = verify_and_apply_patch(request, patch)

        self.assertIsNotNone(repaired)
        assert repaired is not None
        self.assertTrue(report.accepted)
        self.assertTrue(report.request_changed)
        self.assertEqual(repaired.time_step, 0.002)
        self.assertEqual(repaired.box_size, 5)
        self.assertIn("lammps_validation", report.verification_steps)
        self.assertIn("red_review_required_on_retry", report.verification_steps)
        self.assertEqual(report.locked_constraint_violations, [])

    def test_blue_patch_policy_rejects_locked_material_change(self) -> None:
        request = _request()
        patch = build_patch_from_request_payload(
            request,
            {"material": "Al", "time_step": 0.002},
            stage="review",
            issues=["review suggested switching material"],
        )

        repaired, report = verify_and_apply_patch(request, patch)

        self.assertIsNone(repaired)
        self.assertFalse(report.accepted)
        self.assertTrue(report.requires_user_confirmation)
        self.assertEqual(report.termination_reason, "patch_requires_user_confirmation")
        self.assertIn("material", report.locked_constraint_violations)
        self.assertEqual(report.risk, "high")

    def test_blue_patch_policy_rejects_unknown_patch_path(self) -> None:
        patch = RepairPatch(
            operations=[
                PatchOperation(
                    op="modify",
                    path="shell",
                    before=None,
                    after="rm -rf /",
                    reason="malicious or malformed path",
                )
            ]
        )

        repaired, report = verify_and_apply_patch(_request(), patch)

        self.assertIsNone(repaired)
        self.assertFalse(report.accepted)
        self.assertEqual(report.termination_reason, "patch_policy_rejected")
        self.assertTrue(any(item["normalized_path"] == "shell" for item in report.rejected_operations))

    def test_runtime_repair_records_policy_report_and_blocks_locked_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=_FakeRepairLLM({"material": "Al", "time_step": 0.002}),  # type: ignore[arg-type]
            )
            history: list[dict[str, object]] = []

            repaired = runtime._repair_request(request=_request(), issues=["bad review"], stage="review", repair_history=history)

        self.assertIsNone(repaired)
        self.assertEqual(len(history), 1)
        policy_report = history[0]["policy_report"]
        assert isinstance(policy_report, dict)
        self.assertFalse(policy_report["accepted"])
        self.assertIn("material", policy_report["locked_constraint_violations"])

    def test_runtime_repair_accepts_safe_blue_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=_FakeRepairLLM({"time_step": 0.002}),  # type: ignore[arg-type]
            )
            history: list[dict[str, object]] = []

            repaired = runtime._repair_request(request=_request(), issues=["minor validator warning"], stage="validation", repair_history=history)

        self.assertIsNotNone(repaired)
        assert repaired is not None
        self.assertEqual(repaired.time_step, 0.002)
        policy_report = history[0]["policy_report"]
        assert isinstance(policy_report, dict)
        self.assertTrue(policy_report["accepted"])
        parse_audit = history[0]["blue_parse_audit"]
        assert isinstance(parse_audit, dict)
        self.assertEqual(parse_audit["source"], "request_delta_fallback")
        self.assertTrue(parse_audit["fallback_used"])
        self.assertIn("time_step", parse_audit["legacy_payload_keys"])

    def test_runtime_repair_prompt_uses_controlled_shared_memory_l1_l2_l3_context(self) -> None:
        fake_llm = _FakeRepairLLM({"time_step": 0.002})
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=fake_llm,  # type: ignore[arg-type]
            )
            history: list[dict[str, object]] = []

            repaired = runtime._repair_request(
                request=_request(),
                issues=["review suggested a safer time step"],
                stage="review",
                repair_history=history,
                shared_memory_context=_shared_memory_context(),
            )

        self.assertIsNotNone(repaired)
        self.assertIn("Shared memory controlled context", fake_llm.last_user_prompt)
        self.assertIn("mem-locked-temperature", fake_llm.last_user_prompt)
        self.assertIn("locked=True", fake_llm.last_user_prompt)
        self.assertIn("content_hash=hash-temp", fake_llm.last_user_prompt)
        self.assertIn("never change locked L1", fake_llm.last_user_prompt)

    def test_runtime_repair_accepts_native_blue_patch_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=_FakeRepairLLM(
                    {
                        "schema_version": "lammps-blue-patch/v1",
                        "operations": [
                            {
                                "op": "modify",
                                "path": "time_step",
                                "before": 0.001,
                                "after": 0.002,
                                "reason": "Use safer timestep.",
                            },
                            {
                                "op": "verify",
                                "path": "lammps_request",
                                "reason": "Revalidate before retry.",
                            },
                        ],
                        "risk": "low",
                        "source": "llm_blue_patch",
                    }
                ),  # type: ignore[arg-type]
            )
            history: list[dict[str, object]] = []

            repaired = runtime._repair_request(request=_request(), issues=["minor validator warning"], stage="validation", repair_history=history)

        self.assertIsNotNone(repaired)
        assert repaired is not None
        self.assertEqual(repaired.time_step, 0.002)
        parse_audit = history[0]["blue_parse_audit"]
        assert isinstance(parse_audit, dict)
        self.assertEqual(parse_audit["source"], "native_blue_patch")
        self.assertFalse(parse_audit["fallback_used"])
        self.assertEqual(parse_audit["parse_mode"], "strict")
        patch_payload = history[0]["patch"]
        assert isinstance(patch_payload, dict)
        self.assertEqual(patch_payload["source"], "llm_blue_patch")

    def test_runtime_repair_blocks_stagnant_review_score_before_llm(self) -> None:
        original = _request()
        previous_repaired = original.model_copy(update={"time_step": 0.002})
        history: list[dict[str, object]] = [
            _accepted_repair_history(original, previous_repaired, score_before_repair=50.0)
        ]
        fake_llm = _FakeRepairLLM({"box_size": 5})
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=fake_llm,  # type: ignore[arg-type]
            )

            repaired = runtime._repair_request(
                request=previous_repaired,
                issues=["review still blocks"],
                stage="review",
                repair_history=history,
                repair_budget=2,
                current_score=50.2,
            )

        self.assertIsNone(repaired)
        self.assertEqual(fake_llm.calls, 0)
        guard_report = history[-1]["convergence_report"]
        assert isinstance(guard_report, dict)
        self.assertEqual(guard_report["termination_reason"], "repair_stagnation_detected")
        self.assertTrue(guard_report["stagnation_detected"])

    def test_runtime_repair_blocks_request_oscillation_after_patch(self) -> None:
        original = _request()
        previous_repaired = original.model_copy(update={"time_step": 0.002})
        history: list[dict[str, object]] = [
            _accepted_repair_history(original, previous_repaired, score_before_repair=50.0)
        ]
        fake_llm = _FakeRepairLLM({"time_step": original.time_step})
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=fake_llm,  # type: ignore[arg-type]
            )

            repaired = runtime._repair_request(
                request=previous_repaired,
                issues=["try reverting"],
                stage="validation",
                repair_history=history,
                repair_budget=2,
            )

        self.assertIsNone(repaired)
        self.assertEqual(fake_llm.calls, 1)
        policy_report = history[-1]["policy_report"]
        convergence_report = history[-1]["convergence_report"]
        assert isinstance(policy_report, dict)
        assert isinstance(convergence_report, dict)
        self.assertTrue(policy_report["accepted"])
        self.assertFalse(convergence_report["allow_repair"])
        self.assertEqual(convergence_report["termination_reason"], "repair_oscillation_detected")
        self.assertTrue(convergence_report["oscillation_detected"])

    def test_runtime_repair_blocks_budget_exhaustion_before_llm(self) -> None:
        original = _request()
        previous_repaired = original.model_copy(update={"time_step": 0.002})
        history: list[dict[str, object]] = [
            _accepted_repair_history(original, previous_repaired, stage="validation")
        ]
        fake_llm = _FakeRepairLLM({"box_size": 5})
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp_dir)),
                llm_client=fake_llm,  # type: ignore[arg-type]
            )

            repaired = runtime._repair_request(
                request=previous_repaired,
                issues=["another validation issue"],
                stage="validation",
                repair_history=history,
                repair_budget=1,
            )

        self.assertIsNone(repaired)
        self.assertEqual(fake_llm.calls, 0)
        guard_report = history[-1]["convergence_report"]
        assert isinstance(guard_report, dict)
        self.assertEqual(guard_report["termination_reason"], "repair_budget_exhausted")
        self.assertTrue(guard_report["budget_exhausted"])

    def test_runtime_collects_red_and_blue_parse_audits(self) -> None:
        audits = LammpsRuntime._collect_llm_parse_audits(
            {
                "review": {
                    "llm_review_parse_audit": {
                        "success": True,
                        "parse_mode": "strict",
                        "payload_type": "red_review_advisory",
                    }
                },
                "repair_history": [
                    {
                        "stage": "validation",
                        "blue_parse_audit": {
                            "success": True,
                            "parse_mode": "strict",
                            "payload_type": "blue_patch",
                            "source": "native_blue_patch",
                        },
                    }
                ],
            }
        )

        self.assertEqual([item["audit_type"] for item in audits], ["red_review_advisory", "blue_patch"])
        self.assertEqual(audits[1]["stage"], "validation")
        self.assertEqual(audits[1]["source"], "native_blue_patch")


if __name__ == "__main__":
    unittest.main()
