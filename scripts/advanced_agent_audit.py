#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROADMAP = REPO_ROOT / "docs" / "ADVANCED_AGENT_MIGRATION_ROADMAP.md"
DEFAULT_BENCHMARK_REPORT = REPO_ROOT / "backend" / "outputs" / "benchmarks" / "latest.json"
DEFAULT_BENCHMARK_DATASET_MANIFEST = REPO_ROOT / "backend" / "benchmarks" / "datasets" / "manifest.json"
DEFAULT_MATERIALS_MANIFEST = REPO_ROOT / "backend" / "benchmarks" / "datasets" / "materials_agent_bench" / "manifest.json"
DEFAULT_MATERIALS_FREEZE_LOCK = REPO_ROOT / "backend" / "benchmarks" / "datasets" / "materials_agent_bench.freeze.json"

UNCHECKED_PATTERN = re.compile(r"^\s*-\s+\[\s\]\s+(.+?)\s*$")
REQUIRED_ADVANCED_SUITES = (
    "lammps_quality",
    "lammps_red_blue",
    "review_json_fallback",
    "orchestration",
    "judge_calibration",
    "lammps_recovery",
    "shared_memory",
    "memory_conflict",
    "context_compression",
    "materials_multihop",
)
REQUIRED_ADVANCED_DATASETS = {
    "lammps_quality_cases": 6,
    "lammps_red_blue_cases": 7,
    "review_json_fallback_cases": 5,
    "orchestration_cases": 5,
    "judge_calibration_cases": 31,
    "lammps_recovery_cases": 4,
    "shared_memory_cases": 4,
    "memory_conflict_cases": 4,
    "context_compression_cases": 3,
    "materials_multihop_cases": 3,
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _check(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, **details}


def _capability_marker_check(capability: str, name: str, relative_path: str, markers: list[str]) -> dict[str, Any]:
    path = REPO_ROOT / relative_path
    if not path.exists():
        return {
            "capability": capability,
            "name": name,
            "passed": False,
            "path": relative_path,
            "issue": "file_missing",
            "missing_markers": markers,
        }
    text = _read_text(path)
    missing_markers = [marker for marker in markers if marker not in text]
    return {
        "capability": capability,
        "name": name,
        "passed": not missing_markers,
        "path": relative_path,
        "marker_count": len(markers),
        "missing_markers": missing_markers,
    }


def _capability_file_check(capability: str, name: str, relative_path: str) -> dict[str, Any]:
    path = REPO_ROOT / relative_path
    return {
        "capability": capability,
        "name": name,
        "passed": path.exists(),
        "path": relative_path,
        "issue": "" if path.exists() else "file_missing",
    }


def audit_roadmap(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _check("roadmap_checklist", False, path=_rel(path), issue="roadmap_missing")
    unchecked: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = UNCHECKED_PATTERN.match(line)
        if match:
            unchecked.append({"line": line_no, "item": match.group(1)})
    return _check(
        "roadmap_checklist",
        not unchecked,
        path=_rel(path),
        unchecked_count=len(unchecked),
        unchecked_items=unchecked,
    )


def audit_benchmark_report(path: Path, *, min_suites: int, min_threshold_checks: int) -> dict[str, Any]:
    if not path.exists():
        return _check("deterministic_benchmark_report", False, path=_rel(path), issue="benchmark_report_missing")
    report = _load_json(path)
    threshold_checks = report.get("threshold_checks") if isinstance(report.get("threshold_checks"), list) else []
    failed_thresholds = [item for item in threshold_checks if not isinstance(item, dict) or item.get("passed") is not True]
    selected_suites = report.get("selected_suites") if isinstance(report.get("selected_suites"), list) else []
    missing_required_suites = [suite for suite in REQUIRED_ADVANCED_SUITES if suite not in selected_suites]
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    raw_results = report.get("raw_results") if isinstance(report.get("raw_results"), dict) else {}
    passed = (
        report.get("schema_version") == "agent-benchmark-report/v1"
        and report.get("passed") is True
        and len(selected_suites) >= min_suites
        and len(threshold_checks) >= min_threshold_checks
        and not failed_thresholds
        and not missing_required_suites
    )
    return _check(
        "deterministic_benchmark_report",
        passed,
        path=_rel(path),
        generated_at=report.get("generated_at"),
        report_passed=report.get("passed") is True,
        include_live=bool(report.get("include_live")),
        real_lammps=bool(report.get("real_lammps")),
        live_backends=bool(report.get("live_backends")),
        suite_count=len(selected_suites),
        metric_suite_count=len(metrics),
        raw_result_count=len(raw_results),
        threshold_check_count=len(threshold_checks),
        failed_threshold_count=len(failed_thresholds),
        failed_thresholds=failed_thresholds[:12],
        required_advanced_suites=list(REQUIRED_ADVANCED_SUITES),
        missing_required_suites=missing_required_suites,
        elapsed_seconds=report.get("elapsed_seconds"),
    )


def audit_materials_agent_bench(
    manifest_path: Path,
    freeze_lock_path: Path,
    *,
    expected_case_count: int,
    expected_development_count: int,
    expected_frozen_count: int,
) -> dict[str, Any]:
    if not manifest_path.exists():
        return _check("materials_agent_bench_freeze", False, manifest_path=_rel(manifest_path), issue="manifest_missing")
    if not freeze_lock_path.exists():
        return _check("materials_agent_bench_freeze", False, freeze_lock_path=_rel(freeze_lock_path), issue="freeze_lock_missing")
    manifest = _load_json(manifest_path)
    lock = _load_json(freeze_lock_path)
    manifest_freeze = manifest.get("freeze") if isinstance(manifest.get("freeze"), dict) else {}
    lock_freeze = lock.get("freeze") if isinstance(lock.get("freeze"), dict) else {}
    manifest_splits = manifest.get("splits") if isinstance(manifest.get("splits"), dict) else {}
    lock_splits = lock.get("splits") if isinstance(lock.get("splits"), dict) else {}
    manifest_leakage = manifest_freeze.get("data_leakage") if isinstance(manifest_freeze.get("data_leakage"), dict) else {}
    manifest_hashes = manifest_freeze.get("case_hashes") if isinstance(manifest_freeze.get("case_hashes"), dict) else {}
    lock_hashes = lock_freeze.get("case_hashes") if isinstance(lock_freeze.get("case_hashes"), dict) else {}
    split_hash_match = manifest_freeze.get("split_hash") == lock_freeze.get("split_hash")
    case_hashes_match = manifest_hashes == lock_hashes
    passed = (
        manifest.get("benchmark_version") == lock.get("materials_agent_bench_version")
        and manifest.get("case_count") == expected_case_count
        and lock.get("case_count") == expected_case_count
        and manifest_splits.get("development") == expected_development_count
        and manifest_splits.get("frozen_test") == expected_frozen_count
        and lock_splits.get("development") == expected_development_count
        and lock_splits.get("frozen_test") == expected_frozen_count
        and manifest_freeze.get("case_count") == expected_frozen_count
        and lock_freeze.get("case_count") == expected_frozen_count
        and len(manifest_hashes) == expected_frozen_count
        and split_hash_match
        and case_hashes_match
        and manifest_leakage.get("ok") is True
        and int(manifest_leakage.get("issue_count") or 0) == 0
    )
    return _check(
        "materials_agent_bench_freeze",
        passed,
        manifest_path=_rel(manifest_path),
        freeze_lock_path=_rel(freeze_lock_path),
        benchmark_version=manifest.get("benchmark_version"),
        case_count=manifest.get("case_count"),
        lock_case_count=lock.get("case_count"),
        splits=manifest_splits,
        lock_splits=lock_splits,
        frozen_case_hash_count=len(manifest_hashes),
        split_hash_match=split_hash_match,
        case_hashes_match=case_hashes_match,
        data_leakage_ok=manifest_leakage.get("ok") is True,
        data_leakage_issue_count=int(manifest_leakage.get("issue_count") or 0),
    )


def audit_advanced_capability_surface(dataset_manifest_path: Path) -> dict[str, Any]:
    """Check that the advanced-agent capability surface is still wired in.

    This is intentionally a repository-surface audit rather than a behavior
    benchmark. The benchmark report proves recent behavior; these checks make
    accidental deletion of the core advanced-agent modules, feature flags,
    datasets, tests, Makefile targets and CI workflows visible immediately.
    """

    checks: list[dict[str, Any]] = [
        _capability_marker_check(
            "orchestration",
            "dag_executor_async_semaphore",
            "backend/app/orchestration/executor.py",
            ["class DAGExecutor", "asyncio.Semaphore", "asyncio.wait", "_mark_global_timeout"],
        ),
        _capability_marker_check(
            "orchestration",
            "replan_three_level_degradation",
            "backend/app/orchestration/replan.py",
            ["def decide_degradation", "level_1_fallback", "level_2_replan", "level_3_partial_report"],
        ),
        _capability_marker_check(
            "orchestration",
            "nine_state_lifecycle_checkpointing",
            "backend/app/orchestration/lifecycle.py",
            ["LifecycleState", "TaskLifecycleController", "CheckpointRecord", "ALLOWED_TRANSITIONS"],
        ),
        _capability_marker_check(
            "orchestration",
            "lammps_runtime_dag_integration",
            "backend/app/runtimes/lammps.py",
            ["DAGExecutor", "decide_degradation", "TaskLifecycleController", "lammps_preflight_dag_enabled"],
        ),
        _capability_marker_check(
            "orchestration",
            "lammps_preflight_plan",
            "backend/app/lammps/preflight.py",
            ["LAMMPS_PREFLIGHT_NODE_IDS", "build_lammps_preflight_plan", "deterministic_fallback"],
        ),
        _capability_marker_check(
            "feature_flags",
            "runtime_config_flags",
            "backend/app/lammps/config.py",
            [
                "LAMMPS_PREFLIGHT_DAG_ENABLED",
                "LAMMPS_RED_BLUE_REVIEW_ENABLED",
                "lammps_preflight_dag_enabled",
                "lammps_red_blue_review_enabled",
            ],
        ),
        _capability_marker_check(
            "feature_flags",
            "global_config_key_map",
            "backend/app/config.py",
            ["lammps_preflight_dag_enabled", "lammps_red_blue_review_enabled"],
        ),
        _capability_marker_check(
            "lammps_quality",
            "physical_quality_gate",
            "backend/app/lammps/quality/physics_gate.py",
            ["build_physical_quality_report", "scientific_result_passed", "synthetic_thermo", "thermo_parse_failed"],
        ),
        _capability_marker_check(
            "red_blue",
            "json_fallback_parser",
            "backend/app/lammps/review/json_parser.py",
            ["parse_review_payload", "strict", "normalized", "deterministic_fallback", "rejected"],
        ),
        _capability_marker_check(
            "red_blue",
            "blue_patch_policy",
            "backend/app/lammps/review/policy.py",
            ["build_patch_from_llm_payload", "verify_and_apply_patch", "LOCKED_PATCH_PATHS", "PatchPolicyReport"],
        ),
        _capability_marker_check(
            "red_blue",
            "runtime_review_audit_and_rollback",
            "backend/app/runtimes/lammps.py",
            ["lammps_red_blue_review_enabled", "llm_parse_audit.json", "_legacy_review_result", "_review_result"],
        ),
        _capability_marker_check(
            "shared_memory",
            "sqlite_memory_store_embedding_cache",
            "backend/app/shared_memory/store.py",
            ["shared_memory_embeddings", "get_or_create_embedding_vector", "shared_memory_conflicts"],
        ),
        _capability_marker_check(
            "shared_memory",
            "sqlite_vec_dense_retrieval",
            "backend/app/shared_memory/service.py",
            ["SqliteVectorStore", "shared_memory_vectors.sqlite3", "get_or_create_embedding_vector", "retrieve_from_items"],
        ),
        _capability_marker_check(
            "shared_memory",
            "query_rewrite_compression_rerank",
            "backend/app/shared_memory/retrieval.py",
            ["rewrite_memory_query", "deterministic_text_vector", "textrank", "forced_retention_ids"],
        ),
        _capability_marker_check(
            "shared_memory",
            "api_integration",
            "backend/app/api.py",
            ["SharedMemoryService", "shared_memory_service"],
        ),
        _capability_marker_check(
            "benchmark",
            "deterministic_suite_registration",
            "backend/benchmarks/run_benchmarks.py",
            [f'"{suite}"' for suite in REQUIRED_ADVANCED_SUITES],
        ),
        _capability_marker_check(
            "benchmark",
            "makefile_gates",
            "Makefile",
            [
                "test-quick:",
                "test-full:",
                "test-benchmark-gate:",
                "audit-advanced-agent:",
                "test-lammps-real:",
                "test-live-backends:",
            ],
        ),
        _capability_marker_check(
            "ci",
            "quick_ci_workflow",
            ".github/workflows/quick-ci.yml",
            ["make test-quick"],
        ),
        _capability_marker_check(
            "ci",
            "nightly_benchmark_workflow",
            ".github/workflows/nightly-benchmark.yml",
            ["make test-full", "make test-lammps-real"],
        ),
        _capability_marker_check(
            "ci",
            "live_backends_workflow",
            ".github/workflows/live-backends.yml",
            ["make test-live-backends", "OPENROUTER_API_KEY", "DASHSCOPE_API_KEY"],
        ),
    ]
    checks.extend(
        _capability_file_check("tests", test_path.removesuffix(".py"), f"backend/tests/{test_path}")
        for test_path in [
            "test_dag_executor.py",
            "test_replan_policy.py",
            "test_lifecycle_state_machine.py",
            "test_lammps_quality.py",
            "test_lammps_review.py",
            "test_memory_retrieval_pipeline.py",
            "test_shared_memory_store.py",
            "test_llm_judge_contract.py",
            "test_benchmark_gate.py",
        ]
    )
    checks.append(_audit_required_benchmark_datasets(dataset_manifest_path))

    failed = [check for check in checks if not check.get("passed")]
    capability_summary: dict[str, dict[str, int]] = {}
    for check in checks:
        capability = str(check.get("capability") or "unknown")
        summary = capability_summary.setdefault(capability, {"passed": 0, "failed": 0, "total": 0})
        summary["total"] += 1
        if check.get("passed"):
            summary["passed"] += 1
        else:
            summary["failed"] += 1

    return _check(
        "advanced_capability_surface",
        not failed,
        capability_check_count=len(checks),
        failed_capability_check_count=len(failed),
        capability_summary=capability_summary,
        failed_capability_checks=failed[:24],
    )


def _audit_required_benchmark_datasets(dataset_manifest_path: Path) -> dict[str, Any]:
    if not dataset_manifest_path.exists():
        return {
            "capability": "benchmark",
            "name": "advanced_dataset_manifest",
            "passed": False,
            "path": _rel(dataset_manifest_path),
            "issue": "dataset_manifest_missing",
        }
    manifest = _load_json(dataset_manifest_path)
    datasets = manifest.get("datasets") if isinstance(manifest.get("datasets"), dict) else {}
    dataset_dir = dataset_manifest_path.parent
    missing_or_small: list[dict[str, Any]] = []
    missing_files: list[str] = []
    for dataset_name, minimum_count in REQUIRED_ADVANCED_DATASETS.items():
        raw_count = datasets.get(dataset_name, 0)
        try:
            actual_count = int(raw_count)
        except (TypeError, ValueError):
            actual_count = 0
        if actual_count < minimum_count:
            missing_or_small.append({"dataset": dataset_name, "actual": actual_count, "minimum": minimum_count})
        dataset_file = dataset_dir / f"{dataset_name}.jsonl"
        if not dataset_file.exists():
            missing_files.append(_rel(dataset_file))
    return {
        "capability": "benchmark",
        "name": "advanced_dataset_manifest",
        "passed": not missing_or_small and not missing_files,
        "path": _rel(dataset_manifest_path),
        "required_datasets": REQUIRED_ADVANCED_DATASETS,
        "missing_or_small": missing_or_small,
        "missing_files": missing_files,
        "dataset_count": manifest.get("dataset_count"),
        "cases_total": manifest.get("cases_total"),
    }


def build_audit(args: argparse.Namespace) -> dict[str, Any]:
    checks = [
        audit_roadmap(args.roadmap),
        audit_benchmark_report(
            args.benchmark_report,
            min_suites=args.min_suites,
            min_threshold_checks=args.min_threshold_checks,
        ),
        audit_advanced_capability_surface(args.benchmark_dataset_manifest),
        audit_materials_agent_bench(
            args.materials_manifest,
            args.materials_freeze_lock,
            expected_case_count=args.expected_materials_cases,
            expected_development_count=args.expected_development_cases,
            expected_frozen_count=args.expected_frozen_cases,
        ),
    ]
    return {
        "schema_version": "advanced-agent-migration-audit/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit advanced agent migration evidence without mutating local state.")
    parser.add_argument("--roadmap", type=Path, default=DEFAULT_ROADMAP)
    parser.add_argument("--benchmark-report", type=Path, default=DEFAULT_BENCHMARK_REPORT)
    parser.add_argument("--benchmark-dataset-manifest", type=Path, default=DEFAULT_BENCHMARK_DATASET_MANIFEST)
    parser.add_argument("--materials-manifest", type=Path, default=DEFAULT_MATERIALS_MANIFEST)
    parser.add_argument("--materials-freeze-lock", type=Path, default=DEFAULT_MATERIALS_FREEZE_LOCK)
    parser.add_argument("--min-suites", type=int, default=19)
    parser.add_argument("--min-threshold-checks", type=int, default=57)
    parser.add_argument("--expected-materials-cases", type=int, default=390)
    parser.add_argument("--expected-development-cases", type=int, default=140)
    parser.add_argument("--expected-frozen-cases", type=int, default=250)
    args = parser.parse_args()
    audit = build_audit(args)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
