#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.compare_versions import compare_benchmark_reports, write_comparison_outputs  # noqa: E402


GATE_SCHEMA_VERSION = "materials-agent-benchmark-gate/v1"


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_current_report(report: dict[str, Any], *, label: str = "current") -> dict[str, Any]:
    threshold_failures = [
        {
            "metric": item.get("metric"),
            "value": item.get("value"),
            "threshold": item.get("threshold"),
            "passed": item.get("passed"),
        }
        for item in report.get("threshold_checks", [])
        if item.get("passed") is not True
    ]
    critical_failures = _critical_failures(report)
    missing_threshold_checks = [] if report.get("threshold_checks") else ["threshold_checks_missing"]
    report_passed_flag = report.get("passed") is True
    issues: list[dict[str, Any]] = []
    if not report_passed_flag:
        issues.append({"kind": "report_passed_flag_false", "label": label})
    if missing_threshold_checks:
        issues.append({"kind": "missing_threshold_checks", "items": missing_threshold_checks})
    if threshold_failures:
        issues.append({"kind": "threshold_failures", "items": threshold_failures})
    if critical_failures:
        issues.append({"kind": "critical_failures", "items": critical_failures})
    return {
        "schema_version": "materials-agent-current-report-gate/v1",
        "label": label,
        "passed": not issues,
        "report_passed_flag": report_passed_flag,
        "threshold_failure_count": len(threshold_failures),
        "critical_failure_count": len(critical_failures),
        "missing_threshold_checks": missing_threshold_checks,
        "threshold_failures": threshold_failures,
        "critical_failures": critical_failures,
        "issues": issues,
    }


def evaluate_benchmark_gate(
    report: dict[str, Any],
    *,
    baseline_report: dict[str, Any] | None = None,
    label: str = "current",
    baseline_label: str = "baseline",
    n_resamples: int = 10_000,
    seed: int = 20260706,
    min_domain_ci_cases: int = 30,
) -> dict[str, Any]:
    current_gate = evaluate_current_report(report, label=label)
    comparison = None
    if baseline_report is not None:
        comparison = compare_benchmark_reports(
            baseline_report,
            report,
            old_label=baseline_label,
            new_label=label,
            n_resamples=n_resamples,
            seed=seed,
            min_domain_ci_cases=min_domain_ci_cases,
        )
    passed = current_gate["passed"] and (comparison is None or comparison["passed"])
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "label": label,
        "baseline_label": baseline_label if baseline_report is not None else None,
        "passed": passed,
        "current": current_gate,
        "comparison": comparison,
    }


def write_gate_outputs(gate: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gate_json = output_dir / "gate.json"
    gate_report = output_dir / "report.md"
    gate_json.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate_report.write_text(render_gate_markdown(gate), encoding="utf-8")
    outputs = {"gate": str(gate_json), "report": str(gate_report)}
    comparison = gate.get("comparison")
    if isinstance(comparison, dict):
        comparison_outputs = write_comparison_outputs(comparison, output_dir / "comparison")
        outputs.update({f"comparison_{name}": path for name, path in comparison_outputs.items()})
    return outputs


def render_gate_markdown(gate: dict[str, Any]) -> str:
    current = gate["current"]
    lines = [
        "# MaterialsAgentBench Benchmark Gate",
        "",
        f"- Schema: `{gate['schema_version']}`",
        f"- Label: `{gate['label']}`",
        f"- Passed: `{gate['passed']}`",
        "",
        "## Current report gate",
        "",
        f"- Report passed flag: `{current['report_passed_flag']}`",
        f"- Threshold failures: `{current['threshold_failure_count']}`",
        f"- Critical failures: `{current['critical_failure_count']}`",
    ]
    if current["threshold_failures"]:
        lines.extend(["", "### Threshold failures", "", "| Metric | Value | Threshold |", "|---|---:|---:|"])
        for item in current["threshold_failures"]:
            lines.append(f"| `{item.get('metric')}` | {item.get('value')} | {item.get('threshold')} |")
    if current["critical_failures"]:
        lines.extend(["", "### Critical failures", ""])
        for item in current["critical_failures"]:
            lines.append(f"- `{item.get('case_id')}`: {item.get('critical_failures')}")
    comparison = gate.get("comparison")
    if isinstance(comparison, dict):
        regressions = comparison["regressions"]
        lines.extend(
            [
                "",
                "## Baseline comparison",
                "",
                f"- Baseline: `{gate['baseline_label']}`",
                f"- Comparison passed: `{comparison['passed']}`",
                f"- Case regressions: `{len(regressions['case_regressions'])}`",
                f"- Threshold regressions: `{len(regressions['threshold_regressions'])}`",
                f"- Critical regressions: `{len(regressions['critical_regressions'])}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _critical_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for suite, suite_result in (report.get("raw_results") or {}).items():
        if not isinstance(suite_result, dict):
            continue
        for row in suite_result.get("results", []):
            if not isinstance(row, dict):
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            evaluation = details.get("evaluation") if isinstance(details.get("evaluation"), dict) else {}
            critical = evaluation.get("critical_failures") if isinstance(evaluation, dict) else []
            if critical:
                failures.append({"case_id": f"{suite}/{row.get('case_id')}", "critical_failures": list(critical)})
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail CI/local gates on benchmark threshold, critical, or baseline regressions")
    parser.add_argument("--report", required=True, type=Path, help="Current benchmark report JSON")
    parser.add_argument("--baseline", type=Path, default=None, help="Optional baseline benchmark report JSON for paired regression checks")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--label", default="current")
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--min-domain-ci-cases", type=int, default=30)
    args = parser.parse_args()

    gate = evaluate_benchmark_gate(
        load_report(args.report),
        baseline_report=load_report(args.baseline) if args.baseline else None,
        label=args.label,
        baseline_label=args.baseline_label,
        n_resamples=args.resamples,
        seed=args.seed,
        min_domain_ci_cases=args.min_domain_ci_cases,
    )
    outputs = write_gate_outputs(gate, args.output_dir)
    print(json.dumps({"passed": gate["passed"], "outputs": outputs}, ensure_ascii=False, indent=2))
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
