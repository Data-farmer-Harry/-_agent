from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib import error, request as urllib_request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.thermo.accuracy import build_thermo_accuracy_report  # noqa: E402
from app.thermo.registry import get_thermo_database_card, load_thermo_database_cards  # noqa: E402


BASE_URL = os.environ.get("PHASE_AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT_SECONDS = int(os.environ.get("PHASE_AGENT_TIMEOUT_SECONDS", "240"))


@dataclass(frozen=True)
class ThermoCalculationCase:
    system_name: str
    family: str
    database_name: str
    prompt: str
    required_terms: tuple[str, ...]


@dataclass
class CaseResult:
    name: str
    family: str
    run_id: str
    route: str
    generation_source: str
    review_passed: bool
    termination_reason: str
    html_length: int
    accuracy_passed: bool
    accuracy_snapshot: dict
    checks: list[str]


def _build_supported_cases() -> tuple[ThermoCalculationCase, ...]:
    prompts = {
        "Al-Zn": "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。",
        "Al-Mg": "请生成一张 Al-Mg 二元相图，温度范围 300K-1000K，重点展示液相区、FCC_A1、HCP_A3，以及中间金属间化合物相。",
        "Al-Ni": "请生成一张 Al-Ni 二元相图，温度范围 300K-2000K，突出液相区和 Al3Ni1、Al3Ni2、Al3Ni5、FCC_L12 等主要相区。",
        "Pb-Sn": "请生成一张 Pb-Sn 二元相图，温度范围 300K-700K，突出共晶附近的液相线、FCC_A1 和 BCT_A5 两个主要固相区。",
        "Al-Fe": "请生成一张 Al-Fe 二元相图，温度范围 300K-2000K，突出液相区、FCC_A1、BCC_A2 以及主要金属间化合物相区。",
        "Cu-Ni": "请生成一张 Cu-Ni 二元相图，温度范围 300K-1850K，突出液相线以及 FCC_A1 固溶区。",
        "Nb-Re": "请生成一张 Nb-Re 二元相图，温度范围 300K-3600K，突出高温液相和主要固相区。",
        "Cr-Fe": "请生成一张 Cr-Fe 二元相图，温度范围 300K-2400K，突出液相区、BCC_A2、FCC_A1 和 SIGMA 相区。",
        "Fe-Nb": "请生成一张 Fe-Nb 二元相图，温度范围 300K-3000K，突出液相区、BCC_A2 和主要金属间化合物相区。",
        "Cr-Nb": "请生成一张 Cr-Nb 二元相图，温度范围 300K-3000K，突出液相区、BCC_A2 和 LAVES_C15 相区。",
        "Cr-Ti": "请生成一张 Cr-Ti 二元相图，温度范围 300K-2400K，突出液相区、BCC_A2、HCP_A3 和主要 Laves 相区。",
        "Cr-V": "请生成一张 Cr-V 二元相图，温度范围 300K-2500K，突出液相区和 BCC_A2 固溶区。",
        "Ti-V": "请生成一张 Ti-V 二元相图，温度范围 300K-2500K，突出液相区、BCC_A2 和 HCP_A3 相区。",
        "Fe-Co": "请生成一张 Fe-Co 二元相图，温度范围 300K-2300K，突出液相区、BCC_A2、FCC_A1 和 HCP_A3 相区。",
        "Co-Cr": "请生成一张 Co-Cr 二元相图，温度范围 300K-2400K，突出液相区、BCC_A2、FCC_A1 和 SIGMA 相区。",
        "Nb-Ti": "请生成一张 Nb-Ti 二元相图，温度范围 300K-3000K，突出液相区、BCC_A2 和 HCP_A3 相区。",
        "Al-Cr": "请生成一张 Al-Cr 二元相图，温度范围 300K-2400K，突出液相区、B2 和 L12_FCC 相区。",
        "Cr-Ni": "请生成一张 Cr-Ni 二元相图，温度范围 300K-2300K，突出液相区、B2、L12_FCC 和主要固相区。",
        "Al-Pt": "请生成一张 Al-Pt 二元相图，温度范围 300K-2400K，突出液相区和主要金属间化合物相区。",
        "Ni-Pt": "请生成一张 Ni-Pt 二元相图，温度范围 300K-2400K，突出液相区、FCC_A1 和 FCC_L12 相区。",
        "Fe-Ni": "请生成一张 Fe-Ni 二元相图，温度范围 300K-2300K，突出液相区、BCC_A2 和 FCC_A1 相区。",
        "Co-Ni": "请生成一张 Co-Ni 二元相图，温度范围 300K-2200K，突出液相区、FCC_A1 和 HCP_A3 相区。",
        "Al-Co": "请生成一张 Al-Co 二元相图，温度范围 300K-2200K，突出液相区、BCC_B2 和主要金属间化合物相区。",
        "Pd-Ru": "请生成一张 Pd-Ru 二元相图，温度范围 300K-3000K，突出 LIQN、FCCN 和 HCPN 相区。",
        "Pd-Tc": "请生成一张 Pd-Tc 二元相图，温度范围 300K-2800K，突出 LIQN、BCCN、FCCN 和 HCPN 相区。",
        "Pd-Mo": "请生成一张 Pd-Mo 二元相图，温度范围 300K-3200K，突出 LIQN、BCCN、FCCN 和 HCPN 相区。",
        "Ru-Tc": "请生成一张 Ru-Tc 二元相图，温度范围 300K-3000K，突出 LIQN 和 HCPN 相区。",
        "Ru-Mo": "请生成一张 Ru-Mo 二元相图，温度范围 300K-3200K，突出 LIQN、BCCN 和 HCPN 相区。",
        "Tc-Mo": "请生成一张 Tc-Mo 二元相图，温度范围 300K-3200K，突出 LIQN、BCCN、HCPN 和 SIGMA 相区。",
    }
    cases: list[ThermoCalculationCase] = []
    for card in load_thermo_database_cards():
        cases.append(
            ThermoCalculationCase(
                system_name=card.system_name,
                family=card.family,
                database_name=card.database_name,
                prompt=prompts.get(card.system_name, f"请生成一张 {card.system_name} 二元相图。"),
                required_terms=(card.system_name, "pycalphad_tdb_database", *card.phases[:5]),
            )
        )
    return tuple(cases)


SUPPORTED_THERMO_CASES = _build_supported_cases()


def default_thermo_cases(limit: int | None = None) -> tuple[ThermoCalculationCase, ...]:
    if limit is None:
        return SUPPORTED_THERMO_CASES
    return SUPPORTED_THERMO_CASES[:limit]


def verify_thermo_case_terms(case: ThermoCalculationCase, *, generated_code: str, html_content: str) -> list[str]:
    issues: list[str] = []
    combined = f"{generated_code}\n{html_content}"
    lowered = combined.lower()
    for term in case.required_terms:
        if term and term.lower() not in lowered:
            issues.append(f"{case.system_name}: missing expected term {term!r} in generated code or HTML")
    return issues


def request_json(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib_request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc


def request_text(path: str) -> str:
    req = urllib_request.Request(f"{BASE_URL}{path}", method="GET")
    try:
        with urllib_request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc


def make_payload(case: ThermoCalculationCase) -> dict:
    temperature_max = {
        "Al-Zn": 1000.0,
        "Al-Mg": 1000.0,
        "Al-Ni": 2000.0,
        "Pb-Sn": 700.0,
        "Al-Fe": 2000.0,
        "Cu-Ni": 1850.0,
        "Nb-Re": 3600.0,
        "Cr-Fe": 2400.0,
        "Fe-Nb": 3000.0,
        "Cr-Nb": 3000.0,
        "Cr-Ti": 2400.0,
        "Cr-V": 2500.0,
        "Ti-V": 2500.0,
        "Fe-Co": 2300.0,
        "Co-Cr": 2400.0,
        "Nb-Ti": 3000.0,
        "Al-Cr": 2400.0,
        "Cr-Ni": 2300.0,
        "Al-Pt": 2400.0,
        "Ni-Pt": 2400.0,
        "Fe-Ni": 2300.0,
        "Co-Ni": 2200.0,
        "Al-Co": 2200.0,
        "Pd-Ru": 3000.0,
        "Pd-Tc": 2800.0,
        "Pd-Mo": 3200.0,
        "Ru-Tc": 3000.0,
        "Ru-Mo": 3200.0,
        "Tc-Mo": 3200.0,
    }.get(case.system_name, 1800.0)
    return {
        "message": case.prompt,
        "system_name": case.system_name,
        "diagram_type": "binary",
        "temperature_min": 300.0,
        "temperature_max": temperature_max,
        "pressure": 101325.0,
        "step_size": 50.0,
        "notes": f"verification family={case.family} database={case.database_name}",
    }


def verify_standardized_html(case: ThermoCalculationCase, html: str) -> list[str]:
    errors: list[str] = []
    if "phase-diagram-agent-layout" not in html:
        errors.append(f"{case.system_name}: html is missing phase-diagram-agent-layout")
    if "phase-diagram-agent-result" not in html and "normalized-page-shell" not in html:
        errors.append(f"{case.system_name}: html is missing the standardized root container")
    return errors


def build_accuracy_snapshot(case: ThermoCalculationCase, *, temperature_min: float, temperature_max: float) -> dict:
    card = get_thermo_database_card(case.system_name)
    if card is None:
        raise RuntimeError(f"{case.system_name}: no thermodynamic registry card was found for accuracy verification")
    report = build_thermo_accuracy_report(card, temperature_min=temperature_min, temperature_max=temperature_max)
    snapshot = asdict(report)
    snapshot["passes"] = report.passes
    return snapshot


def run_case(case: ThermoCalculationCase) -> CaseResult:
    payload = make_payload(case)
    response = request_json("/api/agent/chat", payload)
    route_name = response.get("route", {}).get("name")
    if route_name != "phase_diagram.generate":
        raise RuntimeError(f"{case.system_name} was routed to {route_name} instead of phase_diagram.generate")
    if not response.get("success"):
        raise RuntimeError(f"{case.system_name} did not succeed: {response.get('final_message')}")

    generated_code = str(response.get("generated_code") or "")
    if not generated_code:
        raise RuntimeError(f"{case.system_name} did not return generated_code")

    generation_source = str(response.get("metadata", {}).get("generation_source") or "")
    if generation_source not in {
        "llm_codegen_calculated_wrapper",
        "llm_codegen_calculated_wrapper_repaired",
        "deterministic_codegen_fallback",
    }:
        raise RuntimeError(f"{case.system_name} used an unexpected generation source: {generation_source}")
    if "build_calculated_phase_diagram_report" not in generated_code or "app.thermo.engine" not in generated_code:
        raise RuntimeError(f"{case.system_name} did not return the pycalphad wrapper code path.")
    if case.system_name not in generated_code:
        raise RuntimeError(f"{case.system_name} did not appear in the generated wrapper code.")

    thermo_lookup = response.get("metadata", {}).get("thermo_lookup", {})
    if thermo_lookup.get("matched") is not True:
        raise RuntimeError(f"{case.system_name} did not report a successful thermodynamic registry lookup: {thermo_lookup}")
    if thermo_lookup.get("database_name") != case.database_name:
        raise RuntimeError(f"{case.system_name} did not use the expected database {case.database_name}: {thermo_lookup}")

    review = response.get("metadata", {}).get("review", {})
    if review.get("passed") is not True:
        raise RuntimeError(f"{case.system_name} did not pass review: {review}")

    run_id = str(response["run_id"])
    html = request_text(f"/api/runs/{run_id}/result")

    errors = []
    errors.extend(verify_standardized_html(case, html))
    errors.extend(verify_thermo_case_terms(case, generated_code=generated_code, html_content=html))
    if errors:
        raise RuntimeError("; ".join(errors))

    accuracy_snapshot = build_accuracy_snapshot(
        case,
        temperature_min=float(payload["temperature_min"]),
        temperature_max=float(payload["temperature_max"]),
    )
    if not accuracy_snapshot["passes"]:
        endpoint_descriptions = ", ".join(
            f"{item['side']}={item['midpoint_K']:.1f}K"
            for item in accuracy_snapshot.get("endpoint_estimates", [])
        )
        raise RuntimeError(
            f"{case.system_name} failed thermodynamic accuracy verification. "
            f"Missing phases: {accuracy_snapshot.get('missing_required_phases', [])}; "
            f"endpoint estimates: {endpoint_descriptions}"
        )

    return CaseResult(
        name=case.system_name,
        family=case.family,
        run_id=run_id,
        route=route_name,
        generation_source=generation_source,
        review_passed=True,
        termination_reason=str(response.get("termination_reason") or ""),
        html_length=len(html),
        accuracy_passed=True,
        accuracy_snapshot=accuracy_snapshot,
        checks=[
            "route=phase_diagram.generate",
            "success=true",
            "generated_code_present",
            "generation_source in {llm_codegen_calculated_wrapper, repaired, deterministic_codegen_fallback}",
            "pycalphad_wrapper_code",
            "thermo_registry_lookup.matched=true",
            "review.passed=true",
            "standardized_html_shell",
            "thermo_terms_present",
            "thermo_accuracy_gate_passed",
        ],
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify registry-backed thermodynamic phase-diagram cases against the running backend.")
    parser.add_argument("--all", action="store_true", help="Run the full thermodynamic registry suite.")
    parser.add_argument("--limit", type=int, default=None, help="Run the first N registry-backed cases.")
    parser.add_argument("--cases", nargs="*", default=None, help="Run only the named systems, for example --cases Al-Zn Al-Mg.")
    return parser.parse_args(argv)


def select_cases(args: argparse.Namespace) -> tuple[ThermoCalculationCase, ...]:
    if args.cases:
        requested = {item.lower() for item in args.cases}
        selected = tuple(case for case in SUPPORTED_THERMO_CASES if case.system_name.lower() in requested)
        missing = requested.difference({case.system_name.lower() for case in selected})
        if missing:
            raise RuntimeError(f"Unknown verification cases: {sorted(missing)}")
        return selected
    if args.limit is not None:
        return default_thermo_cases(args.limit)
    if args.all or os.environ.get("PHASE_AGENT_VERIFY_ALL", "").lower() in {"1", "true", "yes"}:
        return SUPPORTED_THERMO_CASES
    return default_thermo_cases()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    cases = select_cases(args)
    if not cases:
        raise RuntimeError("No verification cases selected.")

    results = [run_case(case) for case in cases]
    print(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
