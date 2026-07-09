from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.statistics import build_statistics_environment_manifest, paired_statistics_report


COMPARE_SCHEMA_VERSION = "materials-agent-version-comparison/v1"


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_benchmark_reports(
    old_report: dict[str, Any],
    new_report: dict[str, Any],
    *,
    old_label: str = "old",
    new_label: str = "new",
    n_resamples: int = 10_000,
    seed: int = 20260706,
    min_domain_ci_cases: int = 30,
) -> dict[str, Any]:
    old_cases = _case_pass_map(old_report)
    new_cases = _case_pass_map(new_report)
    domain_by_case = {**{case_id: suite for case_id, suite in _case_domain_map(old_report).items()}, **_case_domain_map(new_report)}
    case_statistics = paired_statistics_report(
        old_cases,
        new_cases,
        metric_name="case_pass",
        data_type="binary",
        domain_by_case=domain_by_case,
        n_resamples=n_resamples,
        seed=seed,
        min_domain_ci_cases=min_domain_ci_cases,
    )
    metric_deltas = _metric_deltas(old_report, new_report)
    threshold_comparison = _threshold_comparison(old_report, new_report)
    regressions = _regressions(old_report, new_report, threshold_comparison)
    return {
        "schema_version": COMPARE_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "old_label": old_label,
        "new_label": new_label,
        "old_manifest": old_report.get("benchmark_manifest", {}),
        "new_manifest": new_report.get("benchmark_manifest", {}),
        "selected_suites": {
            "old": old_report.get("selected_suites", []),
            "new": new_report.get("selected_suites", []),
        },
        "statistics": {
            "case_pass": case_statistics,
            "metric_deltas": metric_deltas,
        },
        "threshold_checks": threshold_comparison,
        "regressions": regressions,
        "passed": not regressions["critical_regressions"] and not regressions["threshold_regressions"],
        "seed": seed,
        "n_resamples": n_resamples,
    }


def write_comparison_outputs(comparison: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output_dir / "manifest.json",
        "environment": output_dir / "environment.json",
        "statistics": output_dir / "statistics.json",
        "threshold_checks": output_dir / "threshold_checks.json",
        "regressions": output_dir / "regressions.json",
        "report": output_dir / "report.md",
    }
    paths["manifest"].write_text(
        json.dumps(
            {
                "schema_version": comparison["schema_version"],
                "generated_at": comparison["generated_at"],
                "old_label": comparison["old_label"],
                "new_label": comparison["new_label"],
                "selected_suites": comparison["selected_suites"],
                "passed": comparison["passed"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["environment"].write_text(
        json.dumps(build_statistics_environment_manifest(extra={"comparison_schema": COMPARE_SCHEMA_VERSION}), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["statistics"].write_text(json.dumps(comparison["statistics"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["threshold_checks"].write_text(json.dumps(comparison["threshold_checks"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["regressions"].write_text(json.dumps(comparison["regressions"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["report"].write_text(render_markdown_report(comparison), encoding="utf-8")
    return {name: str(path) for name, path in paths.items()}


def render_markdown_report(comparison: dict[str, Any]) -> str:
    stats = comparison["statistics"]["case_pass"]["bootstrap"]
    binary = comparison["statistics"]["case_pass"].get("paired_binary", {})
    risk = binary.get("risk_difference", {})
    mcnemar = binary.get("mcnemar", {})
    regressions = comparison["regressions"]
    lines = [
        "# MaterialsAgentBench Version Comparison",
        "",
        f"- Schema: `{comparison['schema_version']}`",
        f"- Old: `{comparison['old_label']}`",
        f"- New: `{comparison['new_label']}`",
        f"- Passed: `{comparison['passed']}`",
        f"- Paired cases: `{comparison['statistics']['case_pass']['paired_case_count']}`",
        f"- Case pass delta: `{stats['delta']}`",
        f"- Case pass 95% CI: `[{stats['ci_low']}, {stats['ci_high']}]`",
        f"- Risk difference: `{risk.get('risk_difference')}`",
        f"- McNemar exact p-value: `{mcnemar.get('exact_p_value')}`",
        "",
        "## Regressions",
        "",
        f"- Case regressions: `{len(regressions['case_regressions'])}`",
        f"- Threshold regressions: `{len(regressions['threshold_regressions'])}`",
        f"- Critical regressions: `{len(regressions['critical_regressions'])}`",
        "",
        "## Threshold changes",
        "",
        "| Metric | Old | New | Threshold | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for item in comparison["threshold_checks"]:
        status = "regressed" if item.get("regressed") else ("improved" if item.get("improved") else "unchanged")
        lines.append(f"| `{item['metric']}` | {item.get('old_value')} | {item.get('new_value')} | {item.get('threshold')} | {status} |")
    lines.extend(["", "## Metric deltas", "", "| Metric | Old | New | Delta |", "|---|---:|---:|---:|"])
    for item in comparison["statistics"]["metric_deltas"]:
        lines.append(f"| `{item['metric']}` | {item['old_value']} | {item['new_value']} | {item['delta']} |")
    return "\n".join(lines) + "\n"


def _case_pass_map(report: dict[str, Any]) -> dict[str, bool]:
    cases: dict[str, bool] = {}
    for suite, suite_result in (report.get("raw_results") or {}).items():
        for row in suite_result.get("results", []) if isinstance(suite_result, dict) else []:
            case_id = f"{suite}/{row.get('case_id')}"
            cases[case_id] = bool(row.get("passed"))
    return cases


def _case_domain_map(report: dict[str, Any]) -> dict[str, str]:
    domains: dict[str, str] = {}
    for suite, suite_result in (report.get("raw_results") or {}).items():
        for row in suite_result.get("results", []) if isinstance(suite_result, dict) else []:
            domains[f"{suite}/{row.get('case_id')}"] = str(suite)
    return domains


def _flat_metrics(report: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {}
    for suite, metrics in (report.get("metrics") or {}).items():
        if not isinstance(metrics, dict):
            continue
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            flat[f"{suite}.{name}"] = float(value)
    return flat


def _metric_deltas(old_report: dict[str, Any], new_report: dict[str, Any]) -> list[dict[str, Any]]:
    old_metrics = _flat_metrics(old_report)
    new_metrics = _flat_metrics(new_report)
    deltas: list[dict[str, Any]] = []
    for metric in sorted(set(old_metrics) & set(new_metrics)):
        old_value = old_metrics[metric]
        new_value = new_metrics[metric]
        deltas.append(
            {
                "metric": metric,
                "old_value": old_value,
                "new_value": new_value,
                "delta": new_value - old_value,
                "relative_delta": ((new_value - old_value) / old_value) if old_value else None,
            }
        )
    return deltas


def _threshold_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("metric")): dict(item) for item in report.get("threshold_checks", []) if item.get("metric")}


def _threshold_comparison(old_report: dict[str, Any], new_report: dict[str, Any]) -> list[dict[str, Any]]:
    old_checks = _threshold_map(old_report)
    new_checks = _threshold_map(new_report)
    rows: list[dict[str, Any]] = []
    for metric in sorted(set(old_checks) | set(new_checks)):
        old_item = old_checks.get(metric, {})
        new_item = new_checks.get(metric, {})
        old_passed = old_item.get("passed")
        new_passed = new_item.get("passed")
        rows.append(
            {
                "metric": metric,
                "old_value": old_item.get("value"),
                "new_value": new_item.get("value"),
                "threshold": new_item.get("threshold", old_item.get("threshold")),
                "old_passed": old_passed,
                "new_passed": new_passed,
                "regressed": old_passed is True and new_passed is False,
                "improved": old_passed is False and new_passed is True,
            }
        )
    return rows


def _regressions(old_report: dict[str, Any], new_report: dict[str, Any], threshold_comparison: list[dict[str, Any]]) -> dict[str, Any]:
    old_cases = _case_pass_map(old_report)
    new_cases = _case_pass_map(new_report)
    case_regressions = [
        {"case_id": case_id, "old_passed": True, "new_passed": False}
        for case_id in sorted(set(old_cases) & set(new_cases))
        if old_cases[case_id] is True and new_cases[case_id] is False
    ]
    case_improvements = [
        {"case_id": case_id, "old_passed": False, "new_passed": True}
        for case_id in sorted(set(old_cases) & set(new_cases))
        if old_cases[case_id] is False and new_cases[case_id] is True
    ]
    threshold_regressions = [item for item in threshold_comparison if item.get("regressed")]
    critical_regressions = [*threshold_regressions]
    new_critical_failures = _new_critical_failures(new_report)
    critical_regressions.extend(new_critical_failures)
    critical_regressions.extend(case_regressions)
    return {
        "case_regressions": case_regressions,
        "case_improvements": case_improvements,
        "threshold_regressions": threshold_regressions,
        "critical_regressions": critical_regressions,
        "new_critical_failures": new_critical_failures,
    }


def _new_critical_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for suite, suite_result in (report.get("raw_results") or {}).items():
        for row in suite_result.get("results", []) if isinstance(suite_result, dict) else []:
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            evaluation = details.get("evaluation") if isinstance(details.get("evaluation"), dict) else {}
            critical = evaluation.get("critical_failures") if isinstance(evaluation, dict) else []
            if critical:
                failures.append({"case_id": f"{suite}/{row.get('case_id')}", "critical_failures": list(critical)})
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two benchmark reports and emit MaterialsAgentBench regression artifacts")
    parser.add_argument("--old", required=True, type=Path, help="Old benchmark report JSON")
    parser.add_argument("--new", required=True, type=Path, help="New benchmark report JSON")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--old-label", default="old")
    parser.add_argument("--new-label", default="new")
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--min-domain-ci-cases", type=int, default=30)
    args = parser.parse_args()

    comparison = compare_benchmark_reports(
        load_report(args.old),
        load_report(args.new),
        old_label=args.old_label,
        new_label=args.new_label,
        n_resamples=args.resamples,
        seed=args.seed,
        min_domain_ci_cases=args.min_domain_ci_cases,
    )
    outputs = write_comparison_outputs(comparison, args.output_dir)
    print(json.dumps({"passed": comparison["passed"], "outputs": outputs}, ensure_ascii=False, indent=2))
    return 0 if comparison["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
