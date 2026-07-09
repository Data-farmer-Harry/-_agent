from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.materials_agent_bench import (  # noqa: E402
    BENCHMARK_DATASET_DIR,
    MATERIALS_AGENT_BENCH_VERSION,
    build_materials_agent_cases,
    build_materials_agent_manifest,
    load_source_datasets,
    validate_materials_agent_cases,
)
from benchmarks.versioning import validate_freeze_manifest  # noqa: E402


FREEZE_LOCK_SCHEMA_VERSION = "materials-agent-bench-freeze-lock/v1"
DEFAULT_FREEZE_LOCK_PATH = BENCHMARK_DATASET_DIR / "materials_agent_bench.freeze.json"


def load_current_materials_agent_bench(dataset_dir: Path = BENCHMARK_DATASET_DIR) -> tuple[list[Any], dict[str, Any]]:
    datasets = load_source_datasets(dataset_dir)
    cases = build_materials_agent_cases(datasets)
    errors = validate_materials_agent_cases(cases)
    if errors:
        raise ValueError(f"MaterialsAgentBench case validation failed: {errors}")
    return cases, build_materials_agent_manifest(cases)


def build_freeze_lock(
    cases: list[Any],
    *,
    manifest: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    manifest = manifest or build_materials_agent_manifest(cases)
    freeze = manifest["freeze"]
    return {
        "schema_version": FREEZE_LOCK_SCHEMA_VERSION,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "materials_agent_bench_version": MATERIALS_AGENT_BENCH_VERSION,
        "case_count": manifest["case_count"],
        "splits": manifest["splits"],
        "source_datasets": manifest["source_datasets"],
        "freeze": freeze,
        "policy": {
            "frozen_split": freeze["split"],
            "same_version_content_changes": "fail",
            "version_bump_content_changes": "warn",
            "data_leakage": "fail",
            "hash_excludes": freeze["hash_excludes"],
        },
    }


def validate_freeze_lock(cases: list[Any], freeze_lock: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if freeze_lock.get("schema_version") != FREEZE_LOCK_SCHEMA_VERSION:
        errors.append("freeze lock schema_version mismatch")
    if freeze_lock.get("materials_agent_bench_version") != MATERIALS_AGENT_BENCH_VERSION:
        errors.append("materials_agent_bench_version mismatch")

    current_manifest = build_materials_agent_manifest(cases)
    if freeze_lock.get("case_count") != current_manifest["case_count"]:
        errors.append(
            f"case_count mismatch: expected {freeze_lock.get('case_count')}, current {current_manifest['case_count']}"
        )
    if freeze_lock.get("splits") != current_manifest["splits"]:
        errors.append(f"splits mismatch: expected {freeze_lock.get('splits')}, current {current_manifest['splits']}")

    freeze_manifest = freeze_lock.get("freeze")
    if not isinstance(freeze_manifest, dict):
        errors.append("freeze lock missing freeze manifest")
        freeze_validation: dict[str, Any] = {
            "ok": False,
            "errors": ["freeze lock missing freeze manifest"],
            "warnings": [],
            "changes": {},
        }
    else:
        freeze_validation = validate_freeze_manifest(cases, freeze_manifest)
        errors.extend(freeze_validation.get("errors", []))
        warnings.extend(freeze_validation.get("warnings", []))
        current_split_hash = current_manifest["freeze"]["split_hash"]
        if freeze_manifest.get("split_hash") != current_split_hash:
            errors.append(
                f"split_hash mismatch: expected {freeze_manifest.get('split_hash')}, current {current_split_hash}"
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "freeze_validation": freeze_validation,
        "current": {
            "case_count": current_manifest["case_count"],
            "splits": current_manifest["splits"],
            "freeze": {
                "case_count": current_manifest["freeze"]["case_count"],
                "split_hash": current_manifest["freeze"]["split_hash"],
                "data_leakage": current_manifest["freeze"]["data_leakage"],
            },
        },
        "locked": {
            "case_count": freeze_lock.get("case_count"),
            "splits": freeze_lock.get("splits"),
            "freeze": {
                "case_count": (freeze_lock.get("freeze") or {}).get("case_count")
                if isinstance(freeze_lock.get("freeze"), dict)
                else None,
                "split_hash": (freeze_lock.get("freeze") or {}).get("split_hash")
                if isinstance(freeze_lock.get("freeze"), dict)
                else None,
            },
        },
    }


def write_freeze_lock(freeze_lock: dict[str, Any], output_path: Path, *, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"freeze lock already exists: {output_path}. Pass --force to overwrite.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(freeze_lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_freeze_lock(path: Path = DEFAULT_FREEZE_LOCK_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or validate MaterialsAgentBench v1 frozen split.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_parser = subparsers.add_parser("write", help="Write a freeze lock JSON file for the current frozen split.")
    write_parser.add_argument("--dataset-dir", type=Path, default=BENCHMARK_DATASET_DIR)
    write_parser.add_argument("--output", type=Path, default=DEFAULT_FREEZE_LOCK_PATH)
    write_parser.add_argument("--created-at", default=None)
    write_parser.add_argument("--force", action="store_true")

    check_parser = subparsers.add_parser("check", help="Validate current frozen split against a freeze lock JSON file.")
    check_parser.add_argument("--dataset-dir", type=Path, default=BENCHMARK_DATASET_DIR)
    check_parser.add_argument("--lock", type=Path, default=DEFAULT_FREEZE_LOCK_PATH)

    args = parser.parse_args()
    cases, manifest = load_current_materials_agent_bench(args.dataset_dir)

    if args.command == "write":
        freeze_lock = build_freeze_lock(cases, manifest=manifest, created_at=args.created_at)
        try:
            write_freeze_lock(freeze_lock, args.output, force=args.force)
        except FileExistsError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(args.output),
                    "case_count": freeze_lock["case_count"],
                    "splits": freeze_lock["splits"],
                    "frozen_case_count": freeze_lock["freeze"]["case_count"],
                    "split_hash": freeze_lock["freeze"]["split_hash"],
                    "data_leakage": freeze_lock["freeze"]["data_leakage"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    freeze_lock = load_freeze_lock(args.lock)
    validation = validate_freeze_lock(cases, freeze_lock)
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if validation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
