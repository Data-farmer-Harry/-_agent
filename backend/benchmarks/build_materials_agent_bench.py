from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.materials_agent_bench import (
    BENCHMARK_DATASET_DIR,
    DEFAULT_MATERIALS_AGENT_BENCH_DIR,
    build_materials_agent_cases,
    load_source_datasets,
    validate_materials_agent_cases,
    write_materials_agent_bench,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MaterialsAgentBench v1 adapter datasets")
    parser.add_argument("--dataset-dir", type=Path, default=BENCHMARK_DATASET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_MATERIALS_AGENT_BENCH_DIR)
    parser.add_argument("--summary-only", action="store_true", help="Print a compact manifest summary instead of full frozen case hashes.")
    args = parser.parse_args()

    datasets = load_source_datasets(args.dataset_dir)
    cases = build_materials_agent_cases(datasets)
    errors = validate_materials_agent_cases(cases)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    manifest = write_materials_agent_bench(cases, args.output_dir)
    response_manifest = _summarize_manifest(manifest) if args.summary_only else manifest
    print(json.dumps({"ok": True, "manifest": response_manifest, "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))
    return 0


def _summarize_manifest(manifest: dict[str, object]) -> dict[str, object]:
    summary = dict(manifest)
    freeze = summary.get("freeze")
    if isinstance(freeze, dict):
        summary["freeze"] = {
            "schema_version": freeze.get("schema_version"),
            "benchmark_version": freeze.get("benchmark_version"),
            "split": freeze.get("split"),
            "case_count": freeze.get("case_count"),
            "split_hash": freeze.get("split_hash"),
            "data_leakage": freeze.get("data_leakage"),
            "hash_excludes": freeze.get("hash_excludes"),
        }
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
