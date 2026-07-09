from __future__ import annotations

import unittest
from dataclasses import replace

from benchmarks.materials_agent_bench import (
    MATERIALS_AGENT_BENCH_VERSION,
    MaterialsAgentBenchCase,
    build_materials_agent_manifest,
)
from benchmarks.freeze_materials_agent_bench import build_freeze_lock, validate_freeze_lock
from benchmarks.versioning import build_freeze_manifest, scan_case_data_leakage, validate_freeze_manifest


def _case(
    case_id: str = "unit.frozen",
    *,
    benchmark_version: str = MATERIALS_AGENT_BENCH_VERSION,
    prompt: str = "Explain why LAMMPS lost atoms can appear during heating.",
    split: str = "frozen_test",
    metadata: dict[str, object] | None = None,
    uploaded_assets: list[dict[str, object]] | None = None,
) -> MaterialsAgentBenchCase:
    return MaterialsAgentBenchCase(
        case_id=case_id,
        benchmark_version=benchmark_version,
        domain="materials_rag",
        difficulty="normal",
        mode="deterministic",
        prompt=prompt,
        source_dataset="unit",
        source_suite="unit",
        source_case_id=case_id,
        split=split,
        metadata=metadata or {},
        uploaded_assets=uploaded_assets or [],
    )


class MaterialsAgentBenchVersioningTests(unittest.TestCase):
    def test_freeze_manifest_hashes_frozen_cases_only(self) -> None:
        frozen = _case("unit.frozen")
        development = _case("unit.dev", split="development", prompt="local development fixture")

        manifest = build_freeze_manifest([frozen, development])

        self.assertEqual(manifest["schema_version"], "materials-agent-freeze/v1")
        self.assertEqual(manifest["case_count"], 1)
        self.assertIn("unit.frozen", manifest["case_hashes"])
        self.assertNotIn("unit.dev", manifest["case_hashes"])
        self.assertTrue(manifest["data_leakage"]["ok"])

    def test_frozen_case_modified_without_version_bump_fails(self) -> None:
        frozen = _case("unit.frozen")
        freeze_manifest = build_freeze_manifest([frozen])
        changed_same_version = replace(frozen, prompt="Changed prompt without version bump")

        validation = validate_freeze_manifest([changed_same_version], freeze_manifest)

        self.assertFalse(validation["ok"])
        self.assertEqual(validation["changes"]["changed"], ["unit.frozen"])
        self.assertTrue(any("changed without benchmark version bump" in error for error in validation["errors"]))

    def test_frozen_case_modified_with_version_bump_warns_not_fails(self) -> None:
        frozen = _case("unit.frozen")
        freeze_manifest = build_freeze_manifest([frozen])
        changed_next_version = replace(
            frozen,
            benchmark_version="materials-agent-bench/v2",
            prompt="Changed prompt with explicit version bump",
        )

        validation = validate_freeze_manifest([changed_next_version], freeze_manifest)

        self.assertTrue(validation["ok"])
        self.assertEqual(validation["changes"]["changed"], ["unit.frozen"])
        self.assertEqual(validation["warnings"], ["frozen case content changed with benchmark version bump"])

    def test_added_or_removed_frozen_case_requires_version_bump(self) -> None:
        original = [_case("unit.a"), _case("unit.b")]
        freeze_manifest = build_freeze_manifest(original)

        removed = validate_freeze_manifest([original[0]], freeze_manifest)
        added = validate_freeze_manifest([*original, _case("unit.c")], freeze_manifest)

        self.assertFalse(removed["ok"])
        self.assertFalse(added["ok"])
        self.assertTrue(any("removed without benchmark version bump" in error for error in removed["errors"]))
        self.assertTrue(any("added without benchmark version bump" in error for error in added["errors"]))

    def test_data_leakage_scans_frozen_private_paths_and_secret_keys(self) -> None:
        frozen = _case(
            "unit.leaky",
            metadata={"api_key": "or-this-looks-like-a-secret"},
            uploaded_assets=[{"kind": "image", "path": "/Users/harry/Desktop/private.png"}],
        )
        freeze_manifest = build_freeze_manifest([_case("unit.leaky")])

        issues = scan_case_data_leakage([frozen])
        validation = validate_freeze_manifest([frozen], freeze_manifest)

        self.assertGreaterEqual(len(issues), 2)
        self.assertIn("sensitive key name", {issue["reason"] for issue in issues})
        self.assertIn("private filesystem path", {issue["reason"] for issue in issues})
        self.assertFalse(validation["ok"])
        self.assertTrue(any("data leakage" in error for error in validation["errors"]))

    def test_development_private_path_is_not_blocked_by_default(self) -> None:
        development = _case(
            "unit.dev",
            split="development",
            uploaded_assets=[{"kind": "image", "path": "/Users/harry/Desktop/local-fixture.png"}],
        )

        self.assertEqual(scan_case_data_leakage([development]), [])
        self.assertEqual(len(scan_case_data_leakage([development], split=None)), 1)

    def test_materials_agent_manifest_includes_freeze_section(self) -> None:
        manifest = build_materials_agent_manifest([_case("unit.frozen"), _case("unit.dev", split="development")])

        self.assertIn("freeze", manifest)
        self.assertEqual(manifest["freeze"]["schema_version"], "materials-agent-freeze/v1")
        self.assertEqual(manifest["freeze"]["case_count"], 1)

    def test_freeze_lock_blocks_same_version_frozen_content_changes(self) -> None:
        frozen = _case("unit.frozen")
        development = _case("unit.dev", split="development")
        freeze_lock = build_freeze_lock([frozen, development], created_at="2026-07-08T00:00:00+08:00")

        clean = validate_freeze_lock([frozen, development], freeze_lock)
        changed = validate_freeze_lock([replace(frozen, prompt="Changed frozen prompt"), development], freeze_lock)

        self.assertTrue(clean["ok"])
        self.assertFalse(changed["ok"])
        self.assertTrue(any("changed without benchmark version bump" in error for error in changed["errors"]))


if __name__ == "__main__":
    unittest.main()
