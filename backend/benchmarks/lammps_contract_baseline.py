from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.run_benchmarks import load_datasets, run_all_benchmarks


SCHEMA_VERSION = "lammps-contract-baseline/v1"
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "outputs" / "baselines" / "lammps_contract"


def build_lammps_contract_baseline(
    suite_result: dict[str, Any],
    *,
    suite_metrics: dict[str, Any] | None = None,
    threshold_checks: list[dict[str, Any]] | None = None,
    benchmark_manifest: dict[str, Any] | None = None,
    generated_at: str | None = None,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    case_rows = suite_result.get("results") if isinstance(suite_result.get("results"), list) else []
    cases = [_case_baseline(row) for row in case_rows if isinstance(row, dict)]
    run_modes = Counter(str(case.get("run_mode") or "unknown") for case in cases)
    artifact_universe = sorted({artifact for case in cases for artifact in case["artifact_names"]})
    required_artifacts = sorted({artifact for case in cases for artifact in case["required_artifacts"]})
    durations = [float(case["elapsed_seconds"]) for case in cases if case.get("elapsed_seconds") is not None]
    artifact_counts = [int(case["artifact_count"]) for case in cases]
    threshold_checks = threshold_checks or []
    threshold_ok = all(bool(item.get("passed")) for item in threshold_checks) if threshold_checks else True
    passed_cases = sum(1 for case in cases if case["passed"])
    slowest_case = max(cases, key=lambda case: float(case.get("elapsed_seconds") or 0.0), default=None)
    summary = {
        "cases": len(cases),
        "passed": passed_cases,
        "pass_rate": round(passed_cases / len(cases), 4) if cases else 0.0,
        "all_cases_passed": passed_cases == len(cases) and bool(cases),
        "all_required_artifacts_present": all(not case["missing_required_artifacts"] for case in cases),
        "all_required_plan_steps_present": all(not case["missing_plan_steps"] for case in cases),
        "run_modes": dict(sorted(run_modes.items())),
        "artifact_universe": artifact_universe,
        "required_artifacts": required_artifacts,
        "artifact_count_min": min(artifact_counts) if artifact_counts else 0,
        "artifact_count_max": max(artifact_counts) if artifact_counts else 0,
        "avg_case_elapsed_seconds": round(sum(durations) / len(durations), 3) if durations else None,
        "slowest_case": {
            "case_id": slowest_case["case_id"],
            "elapsed_seconds": slowest_case["elapsed_seconds"],
        }
        if slowest_case
        else None,
        "threshold_checks_passed": threshold_ok,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "suite": "lammps_contract",
        "benchmark_manifest": benchmark_manifest or {},
        "metrics": suite_metrics or {},
        "threshold_checks": threshold_checks,
        "summary": summary,
        "passed": summary["all_cases_passed"] and summary["all_required_artifacts_present"] and threshold_ok,
        "elapsed_seconds": round(float(elapsed_seconds), 3) if elapsed_seconds is not None else None,
        "cases": cases,
        "notes": [
            "run_mode is recorded per case. Treat mock/draft baselines as infrastructure baselines, not real scientific LAMMPS baselines.",
            "Generated files are intended for backend/outputs or another ignored artifact directory unless explicitly freezing a reviewed baseline.",
        ],
    }


def write_lammps_contract_baseline(baseline: dict[str, Any], output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "baseline.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_lammps_contract_markdown(baseline), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def render_lammps_contract_markdown(baseline: dict[str, Any]) -> str:
    summary = baseline.get("summary", {}) if isinstance(baseline.get("summary"), dict) else {}
    lines = [
        "# LAMMPS Contract Baseline",
        "",
        f"- Schema: `{baseline.get('schema_version')}`",
        f"- Generated at: `{baseline.get('generated_at') or 'unknown'}`",
        f"- Passed: `{baseline.get('passed')}`",
        f"- Cases: `{summary.get('passed', 0)}/{summary.get('cases', 0)}`",
        f"- Pass rate: `{summary.get('pass_rate', 0.0)}`",
        f"- Run modes: `{json.dumps(summary.get('run_modes', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Avg case elapsed seconds: `{summary.get('avg_case_elapsed_seconds')}`",
        f"- Required artifacts: `{', '.join(summary.get('required_artifacts', []))}`",
        "",
        "| Case | Passed | Mode | Elapsed (s) | Artifacts | Missing required artifacts | Missing plan steps | Run ID |",
        "|---|---:|---|---:|---:|---|---|---|",
    ]
    for case in baseline.get("cases", []):
        if not isinstance(case, dict):
            continue
        lines.append(
            "| {case_id} | {passed} | {mode} | {elapsed} | {artifact_count} | {missing_artifacts} | {missing_steps} | {run_id} |".format(
                case_id=_md(case.get("case_id")),
                passed="✅" if case.get("passed") else "❌",
                mode=_md(case.get("run_mode") or "unknown"),
                elapsed=case.get("elapsed_seconds"),
                artifact_count=case.get("artifact_count"),
                missing_artifacts=_md(", ".join(case.get("missing_required_artifacts") or []) or "none"),
                missing_steps=_md(", ".join(case.get("missing_plan_steps") or []) or "none"),
                run_id=_md(case.get("run_id") or ""),
            )
        )
    lines.extend(["", "## Artifact universe", ""])
    for artifact in summary.get("artifact_universe", []):
        lines.append(f"- `{artifact}`")
    lines.append("")
    return "\n".join(lines)


def _case_baseline(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    artifact_names = sorted(str(item) for item in details.get("artifact_names", []) if item)
    required_artifacts = sorted(str(item) for item in details.get("required_artifacts", []) if item)
    plan_steps = [str(item) for item in details.get("plan_steps", []) if item]
    required_plan_steps = [str(item) for item in details.get("required_plan_steps", []) if item]
    missing_required_artifacts = sorted(set(required_artifacts) - set(artifact_names))
    missing_plan_steps = sorted(set(required_plan_steps) - set(plan_steps))
    return {
        "case_id": str(row.get("case_id") or ""),
        "passed": bool(row.get("passed")),
        "status_code": details.get("status_code"),
        "run_id": details.get("run_id"),
        "run_status": details.get("run_status"),
        "success": details.get("success"),
        "termination_reason": details.get("termination_reason"),
        "elapsed_seconds": details.get("elapsed_seconds"),
        "route": details.get("route") if isinstance(details.get("route"), dict) else {},
        "run_mode": details.get("run_mode") or "unknown",
        "runtime_profile": details.get("runtime_profile") if isinstance(details.get("runtime_profile"), dict) else {},
        "result_profile": details.get("result_profile") if isinstance(details.get("result_profile"), dict) else {},
        "artifact_count": int(details.get("artifact_count") or len(artifact_names)),
        "artifact_names": artifact_names,
        "required_artifacts": required_artifacts,
        "missing_required_artifacts": sorted(str(item) for item in details.get("missing_required_artifacts", missing_required_artifacts)),
        "plan_steps": plan_steps,
        "required_plan_steps": required_plan_steps,
        "missing_plan_steps": sorted(str(item) for item in details.get("missing_plan_steps", missing_plan_steps)),
    }


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and record the LAMMPS contract baseline report.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--real-lammps", action="store_true", help="Record a baseline using the configured real local LAMMPS executable.")
    args = parser.parse_args()

    datasets = load_datasets()
    report = run_all_benchmarks(datasets, suites=["lammps_contract"], limit=args.limit, real_lammps=args.real_lammps)
    suite_result = report["raw_results"]["lammps_contract"]
    suite_metrics = report["metrics"]["lammps_contract"]
    threshold_checks = [item for item in report.get("threshold_checks", []) if str(item.get("metric", "")).startswith("lammps_contract.")]
    baseline = build_lammps_contract_baseline(
        suite_result,
        suite_metrics=suite_metrics,
        threshold_checks=threshold_checks,
        benchmark_manifest=report.get("benchmark_manifest"),
        generated_at=report.get("generated_at"),
        elapsed_seconds=report.get("elapsed_seconds"),
    )
    paths = write_lammps_contract_baseline(baseline, args.output_dir)
    print(json.dumps({"ok": bool(baseline["passed"]), "paths": paths, "summary": baseline["summary"]}, ensure_ascii=False, indent=2))
    return 0 if baseline["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
