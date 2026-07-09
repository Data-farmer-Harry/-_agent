from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.lammps.registry import get_lammps_registry_payload
from examples.verify_phase_diagram_cases import default_thermo_cases, make_payload


DATASET_DIR = Path(__file__).resolve().parent / "datasets"
ASSET_DIR = Path(__file__).resolve().parent / "assets" / "external_phase_diagrams"
ASSET_REL_DIR = Path("benchmarks") / "assets" / "external_phase_diagrams"


def _external_asset_path(filename: str) -> str:
    return str(ASSET_REL_DIR / filename)


def _slug(text: str) -> str:
    return text.lower().replace(" ", "_").replace("/", "_").replace("-", "_")


def build_routing_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "case_id": "route.chat.eutectic_definition",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "什么是共析点，它和包晶反应有什么区别？",
            "expected": {"route_name": "conversation.answer", "compute_domain": "none"},
            "tags": ["chat", "materials"],
        },
        {
            "case_id": "route.phase.al_zn_generate",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K。",
            "expected": {"route_name": "phase_diagram.generate", "compute_domain": "phase_diagram"},
            "tags": ["phase_diagram", "generate"],
        },
        {
            "case_id": "route.phase.pb_sn_generate",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请生成一张 Pb-Sn 二元相图，温度范围 300K-700K。",
            "expected": {"route_name": "phase_diagram.generate", "compute_domain": "phase_diagram"},
            "tags": ["phase_diagram", "generate"],
        },
        {
            "case_id": "route.phase.registry_miss",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请生成一张 Fe-Cu 二元相图，温度范围 300K-1800K。",
            "expected": {"route_name": "phase_diagram.generate", "compute_domain": "phase_diagram"},
            "tags": ["phase_diagram", "registry_miss"],
        },
        {
            "case_id": "route.recognition.basic",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请识别这张相图截图，并提取体系和坐标轴。",
            "upload_image": True,
            "expected": {"route_name": "recognition.analyze", "compute_domain": "none"},
            "tags": ["recognition", "image"],
        },
        {
            "case_id": "route.mixed.image_then_generate",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请先识别这张相图截图，再生成对应的二元相图。",
            "upload_image": True,
            "expected": {"route_name": "mixed.request", "compute_domain": "phase_diagram"},
            "tags": ["mixed", "recognition", "phase_diagram"],
        },
        {
            "case_id": "route.lammps.cu_heating",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps。",
            "expected": {"route_name": "lammps.generate", "compute_domain": "lammps"},
            "tags": ["lammps", "heating"],
        },
        {
            "case_id": "route.lammps.al_equilibration",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "请用 LAMMPS 做一个 Al 的 equilibration 模拟，700K，5000 steps，NVT 系综。",
            "expected": {"route_name": "lammps.generate", "compute_domain": "lammps"},
            "tags": ["lammps", "equilibration"],
        },
        {
            "case_id": "route.followup.code",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "你刚刚生成了什么代码？",
            "expected": {"route_name": "conversation.answer", "compute_domain": "none"},
            "tags": ["followup", "memory"],
        },
        {
            "case_id": "route.followup.accuracy",
            "suite": "routing",
            "mode": "deterministic",
            "prompt": "这张图准确吗？",
            "expected": {"route_name": "conversation.answer", "compute_domain": "none"},
            "tags": ["followup", "memory"],
        },
    ]
    return cases



def build_phase_parsing_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "case_id": "phase_parse.al_zn_range",
            "suite": "phase_parsing",
            "mode": "gold",
            "prompt": "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K。",
            "expected": {"system_name": "Al-Zn", "diagram_type": "binary", "temperature_min": 300.0, "temperature_max": 1000.0},
            "tags": ["phase_diagram", "binary"],
        },
        {
            "case_id": "phase_parse.pb_sn_eutectic",
            "suite": "phase_parsing",
            "mode": "gold",
            "prompt": "请生成一张 Pb-Sn 二元相图，温度范围 300K-700K，并关注共晶附近的液相线。",
            "expected": {"system_name": "Pb-Sn", "diagram_type": "binary", "temperature_min": 300.0, "temperature_max": 700.0},
            "tags": ["phase_diagram", "eutectic"],
        },
        {
            "case_id": "phase_parse.fe_ni_high_temp",
            "suite": "phase_parsing",
            "mode": "gold",
            "prompt": "请生成一张 Fe-Ni 二元相图，温度范围 300K-2300K。",
            "expected": {"system_name": "Fe-Ni", "diagram_type": "binary", "temperature_min": 300.0, "temperature_max": 2300.0},
            "tags": ["phase_diagram", "binary"],
        },
        {
            "case_id": "phase_parse.co_ni_range",
            "suite": "phase_parsing",
            "mode": "gold",
            "prompt": "请画一张 Co-Ni 二元相图，温区 300K 到 2200K。",
            "expected": {"system_name": "Co-Ni", "diagram_type": "binary", "temperature_min": 300.0, "temperature_max": 2200.0},
            "tags": ["phase_diagram", "binary"],
        },
        {
            "case_id": "phase_parse.cr_ti_compounds",
            "suite": "phase_parsing",
            "mode": "gold",
            "prompt": "请生成 Cr-Ti 二元相图，温度范围 300K-2400K，关注 Laves 相。",
            "expected": {"system_name": "Cr-Ti", "diagram_type": "binary", "temperature_min": 300.0, "temperature_max": 2400.0},
            "tags": ["phase_diagram", "intermetallic"],
        },
        {
            "case_id": "phase_parse.ru_mo_high_temp",
            "suite": "phase_parsing",
            "mode": "gold",
            "prompt": "请生成一张 Ru-Mo 二元相图，温度范围 300K-3200K。",
            "expected": {"system_name": "Ru-Mo", "diagram_type": "binary", "temperature_min": 300.0, "temperature_max": 3200.0},
            "tags": ["phase_diagram", "high_temperature"],
        },
    ]
    return cases



def build_lammps_parsing_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "lammps_parse.cu_heating",
            "suite": "lammps_parsing",
            "mode": "gold",
            "prompt": "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps。",
            "expected": {"material": "Cu", "task_type": "heating", "temperature": 800, "steps": 4000, "ensemble": "NVT", "potential_family": "eam"},
            "tags": ["lammps", "heating"],
        },
        {
            "case_id": "lammps_parse.al_equilibration",
            "suite": "lammps_parsing",
            "mode": "gold",
            "prompt": "请用 LAMMPS 做一个 Al 的 equilibration 模拟，700K，5000 steps，NVT 系综。",
            "expected": {"material": "Al", "task_type": "equilibration", "temperature": 700, "steps": 5000, "ensemble": "NVT", "potential_family": "eam"},
            "tags": ["lammps", "equilibration"],
        },
        {
            "case_id": "lammps_parse.ni_heating",
            "suite": "lammps_parsing",
            "mode": "gold",
            "prompt": "请用 LAMMPS 做一个 Ni 的 heating 模拟，从 300K 升到 900K，6000 steps。",
            "expected": {"material": "Ni", "task_type": "heating", "temperature": 900, "steps": 6000, "ensemble": "NVT", "potential_family": "eam"},
            "tags": ["lammps", "heating"],
        },
        {
            "case_id": "lammps_parse.cu_lj",
            "suite": "lammps_parsing",
            "mode": "gold",
            "prompt": "请用 LAMMPS 对 Cu 做一个 700K、3000 steps 的 LJ equilibration。",
            "expected": {"material": "Cu", "task_type": "equilibration", "temperature": 700, "steps": 3000, "ensemble": "NVT", "potential_family": "lj"},
            "tags": ["lammps", "lj"],
        },
    ]


def build_phase_execution_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in default_thermo_cases():
        payload = make_payload(case)
        rows.append(
            {
                "case_id": f"phase_execution.{_slug(case.system_name)}",
                "suite": "phase_execution",
                "mode": "deterministic_real_pycalphad",
                "prompt": case.prompt,
                "request_overrides": {
                    "system_name": payload["system_name"],
                    "temperature_min": payload["temperature_min"],
                    "temperature_max": payload["temperature_max"],
                    "pressure": payload["pressure"],
                    "step_size": payload["step_size"],
                },
                "expected": {
                    "route_name": "phase_diagram.generate",
                    "system_name": case.system_name,
                    "database_name": case.database_name,
                    "family": case.family,
                    "required_terms": list(case.required_terms),
                    "accuracy_required": True,
                },
                "tags": ["phase_diagram", case.family, *case.required_terms[:3]],
            }
        )
    return rows


def build_lammps_contract_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "lammps_contract.cu_heating",
            "suite": "lammps_contract",
            "mode": "deterministic_contract",
            "prompt": "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，并返回热力学图和轨迹结果。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "required_artifacts": ["report.md", "plot.png", "thermo.csv"],
                "plan_steps": ["lammps_request_interpreter", "lammps_input_codegen", "lammps_result_review"],
            },
            "tags": ["lammps", "contract"],
        },
        {
            "case_id": "lammps_contract.al_equilibration",
            "suite": "lammps_contract",
            "mode": "deterministic_contract",
            "prompt": "请用 LAMMPS 做一个 Al 的 equilibration 模拟，700K，5000 steps，NVT 系综。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "required_artifacts": ["report.md", "plot.png", "thermo.csv"],
                "plan_steps": ["lammps_request_interpreter", "lammps_input_codegen", "lammps_result_review"],
            },
            "tags": ["lammps", "contract"],
        },
        {
            "case_id": "lammps_contract.ni_heating",
            "suite": "lammps_contract",
            "mode": "deterministic_contract",
            "prompt": "请用 LAMMPS 做一个 Ni 的 heating 模拟，从 300K 升到 900K，6000 steps。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "required_artifacts": ["report.md", "plot.png", "thermo.csv"],
                "plan_steps": ["lammps_request_interpreter", "lammps_input_codegen", "lammps_result_review"],
            },
            "tags": ["lammps", "contract"],
        },
    ]


def build_lammps_e2e_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "lammps_e2e.cu_heating_full_chain",
            "suite": "lammps_e2e",
            "mode": "deterministic_agent_e2e",
            "prompt": "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，NVT，EAM 势函数，并返回热力学图和轨迹。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "request": {"material": "Cu", "task_type": "heating", "temperature": 800, "steps": 4000, "potential_family": "eam"},
                "required_artifacts": ["in.lammps", "request.json", "report.md", "plot.png", "thermo.csv", "trace.json"],
                "required_steps": [
                    "materials_rag_search",
                    "lammps_request_interpreter",
                    "lammps_registry_lookup",
                    "lammps_validation",
                    "lammps_input_codegen",
                    "lammps_execute",
                    "lammps_postprocess",
                    "lammps_result_review",
                ],
                "requires_materials_rag": True,
                "requires_review": True,
            },
            "tags": ["lammps", "e2e", "heating", "rag"],
        },
        {
            "case_id": "lammps_e2e.al_equilibration_full_chain",
            "suite": "lammps_e2e",
            "mode": "deterministic_agent_e2e",
            "prompt": "请用 LAMMPS 做一个 Al 的 equilibration 模拟，700K，5000 steps，NVT 系综，使用 EAM。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "request": {"material": "Al", "task_type": "equilibration", "temperature": 700, "steps": 5000, "potential_family": "eam"},
                "required_artifacts": ["in.lammps", "request.json", "report.md", "plot.png", "thermo.csv", "trace.json"],
                "required_steps": [
                    "materials_rag_search",
                    "lammps_request_interpreter",
                    "lammps_registry_lookup",
                    "lammps_validation",
                    "lammps_input_codegen",
                    "lammps_execute",
                    "lammps_postprocess",
                    "lammps_result_review",
                ],
                "requires_materials_rag": True,
                "requires_review": True,
            },
            "tags": ["lammps", "e2e", "equilibration", "rag"],
        },
        {
            "case_id": "lammps_e2e.clarify_missing_slots",
            "suite": "lammps_e2e",
            "mode": "deterministic_agent_e2e",
            "prompt": "帮我用 LAMMPS 跑一下模拟。",
            "expected": {
                "route_name": "conversation.answer",
                "compute_domain": "none",
                "intent": "clarify_lammps_request",
                "required_final_terms": ["补充", "材料", "温度", "步数"],
                "forbidden_steps": ["lammps_input_codegen", "lammps_execute"],
            },
            "tags": ["lammps", "e2e", "clarification"],
        },
    ]


def build_lammps_quality_cases() -> list[dict[str, Any]]:
    base_request = {
        "material": "Cu",
        "task_type": "heating",
        "temperature": 800,
        "steps": 1000,
        "dump_file": "dump.atom",
    }
    valid_rows = [
        {"step": 0, "temp": 300, "pe": -3.36, "ke": 7.5, "etotal": 4.14, "press": 100},
        {"step": 500, "temp": 550, "pe": -3.31, "ke": 13.75, "etotal": 10.44, "press": 120},
        {"step": 1000, "temp": 800, "pe": -3.26, "ke": 20.0, "etotal": 16.74, "press": 140},
    ]
    return [
        {
            "case_id": "lammps_quality.valid_real_heating",
            "suite": "lammps_quality",
            "mode": "deterministic_fixture",
            "fixture": {
                "run_mode": "real",
                "request": base_request,
                "thermo_rows": valid_rows,
                "run_log": "LAMMPS run completed normally.\nLoop time of 1.0 on 1 procs for 1000 steps\n",
                "dump_atom_count": 4,
            },
            "expected": {
                "passed": True,
                "scientific_result_passed": True,
                "synthetic_thermo": False,
                "fatal_anomaly": False,
                "valid_run": True,
            },
            "tags": ["lammps", "quality", "valid"],
        },
        {
            "case_id": "lammps_quality.mock_synthetic_not_scientific",
            "suite": "lammps_quality",
            "mode": "deterministic_fixture",
            "fixture": {
                "run_mode": "mock",
                "request": base_request,
                "thermo_rows": valid_rows,
                "run_log": "Mock fallback enabled.\nOriginal error: LAMMPS executable not found.\n",
                "dump_atom_count": 4,
                "synthetic_thermo": True,
            },
            "expected": {
                "passed": True,
                "scientific_result_passed": False,
                "synthetic_thermo": True,
                "fatal_anomaly": False,
                "valid_run": False,
            },
            "tags": ["lammps", "quality", "mock"],
        },
        {
            "case_id": "lammps_quality.real_synthetic_blocked",
            "suite": "lammps_quality",
            "mode": "deterministic_fixture",
            "fixture": {
                "run_mode": "real",
                "request": base_request,
                "thermo_rows": valid_rows,
                "run_log": "LAMMPS run completed normally but thermo was marked synthetic.\n",
                "dump_atom_count": 4,
                "synthetic_thermo": True,
            },
            "expected": {
                "passed": False,
                "scientific_result_passed": False,
                "synthetic_thermo": True,
                "fatal_anomaly": True,
                "valid_run": False,
                "required_issue_terms": ["synthetic thermo"],
                "real_synthetic_guard": True,
            },
            "tags": ["lammps", "quality", "synthetic_guard"],
        },
        {
            "case_id": "lammps_quality.nan_thermo_blocked",
            "suite": "lammps_quality",
            "mode": "deterministic_fixture",
            "fixture": {
                "run_mode": "real",
                "request": base_request,
                "thermo_rows": [
                    {"step": 0, "temp": 300, "pe": -3.36, "ke": 7.5, "etotal": 4.14, "press": 100},
                    {"step": 1000, "temp": "nan", "pe": -3.26, "ke": 20.0, "etotal": 16.74, "press": 140},
                ],
                "run_log": "LAMMPS warning: thermo includes nan temperature.\n",
                "dump_atom_count": 4,
            },
            "expected": {
                "passed": False,
                "scientific_result_passed": False,
                "fatal_anomaly": True,
                "valid_run": False,
                "required_issue_terms": ["nan", "inf"],
            },
            "tags": ["lammps", "quality", "fatal"],
        },
        {
            "case_id": "lammps_quality.lost_atoms_log_blocked",
            "suite": "lammps_quality",
            "mode": "deterministic_fixture",
            "fixture": {
                "run_mode": "real",
                "request": base_request,
                "thermo_rows": valid_rows,
                "run_log": "ERROR: Lost atoms: original 256 current 250\n",
                "dump_atom_count": 4,
            },
            "expected": {
                "passed": False,
                "scientific_result_passed": False,
                "fatal_anomaly": True,
                "valid_run": False,
                "required_issue_terms": ["lost atoms"],
            },
            "tags": ["lammps", "quality", "fatal"],
        },
        {
            "case_id": "lammps_quality.low_step_coverage_blocked",
            "suite": "lammps_quality",
            "mode": "deterministic_fixture",
            "fixture": {
                "run_mode": "real",
                "request": base_request,
                "thermo_rows": [
                    {"step": 0, "temp": 300, "pe": -3.36, "ke": 7.5, "etotal": 4.14, "press": 100},
                    {"step": 100, "temp": 340, "pe": -3.30, "ke": 8.5, "etotal": 5.20, "press": 110},
                ],
                "run_log": "LAMMPS stopped before requested step count.\n",
                "dump_atom_count": 4,
            },
            "expected": {
                "passed": False,
                "scientific_result_passed": False,
                "fatal_anomaly": True,
                "valid_run": False,
                "required_issue_terms": ["step coverage"],
            },
            "tags": ["lammps", "quality", "fatal"],
        },
    ]


def build_recognition_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "recognition.al_zn_image",
            "suite": "recognition",
            "mode": "deterministic",
            "prompt": "请识别这张 Al-Zn 相图截图，并提取体系与坐标轴。",
            "upload_image": True,
            "expected": {"route_name": "recognition.analyze", "system_name": "Al-Zn", "source": "llm_recognition_agent"},
            "tags": ["recognition", "image"],
        },
        {
            "case_id": "recognition.pb_sn_image",
            "suite": "recognition",
            "mode": "deterministic",
            "prompt": "请识别这张 Pb-Sn 相图截图，并提取体系与关键相区。",
            "upload_image": True,
            "expected": {"route_name": "recognition.analyze", "system_name": "Pb-Sn", "source": "llm_recognition_agent"},
            "tags": ["recognition", "image"],
        },
        {
            "case_id": "recognition.al_co_image",
            "suite": "recognition",
            "mode": "deterministic",
            "prompt": "请识别这张 Al-Co 相图截图，并提取体系、坐标轴和相区。",
            "upload_image": True,
            "expected": {"route_name": "recognition.analyze", "system_name": "Al-Co", "source": "llm_recognition_agent"},
            "tags": ["recognition", "image"],
        },
    ]


def build_external_recognition_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "external_recognition.al_ni_pmc",
            "suite": "external_recognition_live",
            "mode": "live_multimodal",
            "prompt": "请识别这张相图截图，并提取体系、坐标轴和主要相区。",
            "asset_path": _external_asset_path("al_ni_pmc_phase_diagram.jpg"),
            "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9629960/",
            "expected": {
                "route_name": "recognition.analyze",
                "system_names": ["Al-Ni"],
                "diagram_type": "binary",
                "x_axis_keywords": ["ni", "composition"],
                "y_axis_keywords": ["temperature"],
                "min_phase_count": 4,
            },
            "tags": ["recognition", "external", "pmc", "al-ni"],
        },
        {
            "case_id": "external_recognition.al_cu_pmc",
            "suite": "external_recognition_live",
            "mode": "live_multimodal",
            "prompt": "请识别这张相图截图，并提取体系、坐标轴和主要相区。",
            "asset_path": _external_asset_path("al_cu_pmc_phase_diagram.jpg"),
            "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12735080/",
            "expected": {
                "route_name": "recognition.analyze",
                "system_names": ["Al-Cu"],
                "diagram_type": "binary",
                "x_axis_keywords": ["cu", "atomic percent"],
                "y_axis_keywords": ["temperature"],
                "min_phase_count": 4,
            },
            "tags": ["recognition", "external", "pmc", "al-cu"],
        },
        {
            "case_id": "external_recognition.pb_sn_nist",
            "suite": "external_recognition_live",
            "mode": "live_multimodal",
            "prompt": "请识别这张相图截图，并提取体系、坐标轴和主要相区。",
            "asset_path": _external_asset_path("pb_sn_nist_phase_diagram.jpg"),
            "source_url": "https://www.metallurgy.nist.gov/phase/solder/pbsn.html",
            "expected": {
                "route_name": "recognition.analyze",
                "system_names": ["Pb-Sn", "Sn-Pb"],
                "diagram_type": "binary",
                "x_axis_keywords": ["pb", "mass"],
                "y_axis_keywords": ["temperature"],
                "min_phase_count": 2,
            },
            "tags": ["recognition", "external", "nist", "pb-sn"],
        },
        {
            "case_id": "external_recognition.fe_ni_pmc",
            "suite": "external_recognition_live",
            "mode": "live_multimodal",
            "prompt": "请识别这张相图截图，并提取体系、坐标轴和主要相区。",
            "asset_path": _external_asset_path("fe_ni_pmc_phase_diagram.jpg"),
            "source_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12136027/",
            "expected": {
                "route_name": "recognition.analyze",
                "system_names": ["Fe-Ni"],
                "diagram_type": "binary",
                "x_axis_keywords": ["ni", "concentration"],
                "y_axis_keywords": ["temperature"],
                "min_phase_count": 3,
            },
            "tags": ["recognition", "external", "pmc", "fe-ni", "high_pressure"],
        },
    ]


def build_memory_followup_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "memory.phase_codegen_followup",
            "suite": "memory_followup",
            "mode": "deterministic",
            "turns": [
                {
                    "message": "请生成一张 Al-Mg 二元相图，温度范围 300K-1000K，突出主要相区。",
                    "request_overrides": {"system_name": "Al-Mg", "temperature_min": 300.0, "temperature_max": 1000.0},
                    "expected_route_name": "phase_diagram.generate",
                },
                {
                    "message": "你刚刚生成了什么代码？",
                    "expected_route_name": "conversation.answer",
                    "expected_contains": ["build_calculated_phase_diagram_report", "Al-Mg"],
                },
            ],
            "tags": ["memory", "followup", "phase_diagram"],
        },
        {
            "case_id": "memory.phase_accuracy_followup",
            "suite": "memory_followup",
            "mode": "deterministic",
            "turns": [
                {
                    "message": "请生成一张 Al-Zn 二元相图，温度范围 300K-1800K。",
                    "request_overrides": {"system_name": "Al-Zn", "temperature_min": 300.0, "temperature_max": 1800.0},
                    "expected_route_name": "phase_diagram.generate",
                },
                {
                    "message": "这张图准确吗？",
                    "expected_route_name": "conversation.answer",
                    "expected_contains": ["不是手画示意图", "review 摘要"],
                },
            ],
            "tags": ["memory", "followup", "trust"],
        },
        {
            "case_id": "memory.long_term_preferences",
            "suite": "memory_retrieval",
            "mode": "deterministic",
            "seed_messages": [
                {
                    "role": "user",
                    "content": "请生成一张 Al-Co 二元相图，并重点关注 AL3CO 和液相区。",
                },
                {
                    "role": "assistant",
                    "content": "好的，我会使用真实 TDB 计算。",
                },
                {
                    "role": "user",
                    "content": "后面继续扩充更多 TDB，并保留长期记忆里的研究重点。",
                },
            ],
            "recognition_result": {
                "system": "Al-Co",
                "diagram_type": "binary",
                "phases": ["LIQUID", "FCC_A1", "AL3CO"],
                "confidence": 0.9,
                "source": "llm_recognition_agent",
            },
            "last_run_context": {
                "run_id": "alias-run",
                "route_name": "phase_diagram.generate",
                "system_name": "Al-Co",
                "request_summary": "Al-Co binary phase diagram",
                "generation_source": "llm_codegen_calculated_wrapper",
                "selected_tool": "phase_diagram_codegen",
                "review_passed": True,
                "artifact_names": ["result.html"],
            },
            "query": "帮我继续看铝钴体系相图，并优先扩充 TDB。",
            "expected_hits": ["Al-Co", "TDB"],
            "tags": ["memory", "preferences"],
        },
    ]


def build_memory_retrieval_cases() -> list[dict[str, Any]]:
    return [case for case in build_memory_followup_cases() if case["suite"] == "memory_retrieval"]


def build_shared_memory_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "shared_memory.duplicate_800k_spacing",
            "suite": "shared_memory",
            "mode": "deterministic",
            "scenario": "duplicate_normalized",
            "scope_id": "bench-shared-memory",
            "items": [
                {
                    "item_type": "constraint",
                    "subject": "Copper heating run",
                    "predicate": "target_temperature",
                    "value": "800K",
                    "unit": "",
                    "text": "Target temperature is 800K.",
                    "authority": "user",
                    "source_refs": ["user:800k"],
                },
                {
                    "item_type": "constraint",
                    "subject": "Cu heating run",
                    "predicate": "target_temperature",
                    "value": "800 K",
                    "unit": "",
                    "text": "Target temperature is 800 K.",
                    "authority": "user",
                    "source_refs": ["user:800_k"],
                },
            ],
            "expected": {"active_count": 1},
            "tags": ["shared_memory", "dedup", "normalized"],
        },
        {
            "case_id": "shared_memory.cross_conversation_isolation",
            "suite": "shared_memory",
            "mode": "deterministic",
            "scenario": "scope_isolation",
            "query": "rdf msd lammps",
            "items": [
                {
                    "scope_id": "bench-scope-a",
                    "item_type": "fact",
                    "subject": "LAMMPS RDF analysis",
                    "predicate": "method",
                    "value": "compute rdf",
                    "text": "This conversation uses RDF analysis.",
                    "authority": "execution",
                    "source_refs": ["scope:a"],
                },
                {
                    "scope_id": "bench-scope-b",
                    "item_type": "fact",
                    "subject": "LAMMPS MSD analysis",
                    "predicate": "method",
                    "value": "compute msd",
                    "text": "Other conversation has a more relevant RDF and MSD plan.",
                    "authority": "execution",
                    "source_refs": ["scope:b"],
                },
            ],
            "expected": {"scope_id": "bench-scope-a"},
            "tags": ["shared_memory", "scope_isolation"],
        },
        {
            "case_id": "shared_memory.locked_fact_tiny_budget",
            "suite": "shared_memory",
            "mode": "deterministic",
            "scenario": "locked_retention",
            "scope_id": "bench-locked",
            "query": "生成 LAMMPS 铜 800 K 加热模拟，并注意后处理。",
            "prompt_budget_bytes": 320,
            "items": [
                {
                    "item_type": "constraint",
                    "subject": "LAMMPS copper heating run",
                    "predicate": "target_temperature",
                    "value": 800,
                    "unit": "K",
                    "text": "The user explicitly locked the target temperature at 800 K.",
                    "authority": "user",
                    "source_refs": ["user:locked-temperature"],
                    "metadata": {"locked": True},
                },
                {
                    "item_type": "evidence",
                    "subject": "verbose RAG background",
                    "predicate": "advice",
                    "value": "background",
                    "text": "This is verbose background evidence about LAMMPS post-processing and visualization. " * 12,
                    "authority": "rag",
                    "source_refs": ["rag:background"],
                },
            ],
            "expected": {"locked_retained": True},
            "tags": ["shared_memory", "locked_fact", "prompt_budget"],
        },
        {
            "case_id": "shared_memory.raw_evidence_traceability",
            "suite": "shared_memory",
            "mode": "deterministic",
            "scenario": "raw_evidence_traceability",
            "scope_id": "bench-raw-evidence",
            "items": [
                {
                    "item_type": "evidence",
                    "subject": "materials_rag:lammps.compute.rdf",
                    "predicate": "supports_query",
                    "value": {"title": "LAMMPS compute rdf"},
                    "text": "Use compute rdf to calculate radial distribution functions from LAMMPS trajectories.",
                    "authority": "rag",
                    "source_refs": ["https://docs.lammps.org/compute_rdf.html"],
                    "metadata": {"stage": "benchmark_rag", "rank": 1},
                }
            ],
            "expected": {"hash_verified": True},
            "tags": ["shared_memory", "raw_evidence", "traceability"],
        },
    ]


def build_memory_conflict_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "memory_conflict.user_value_needs_user",
            "suite": "memory_conflict",
            "mode": "deterministic",
            "scenario": "value_needs_user",
            "items": [
                {
                    "item_type": "constraint",
                    "subject": "Copper heating run",
                    "predicate": "target_temperature",
                    "value": 800,
                    "unit": "K",
                    "text": "Target temperature is 800 K.",
                    "authority": "user",
                    "source_refs": ["user:800K"],
                },
                {
                    "item_type": "constraint",
                    "subject": "Cu heating run",
                    "predicate": "target_temperature",
                    "value": 900,
                    "unit": "K",
                    "text": "Target temperature is 900 K.",
                    "authority": "user",
                    "source_refs": ["user:900K"],
                },
            ],
            "expected": {"conflict_type": "value", "detection_mode": "structured", "conflict_status": "needs_user"},
            "tags": ["memory_conflict", "needs_user"],
        },
        {
            "case_id": "memory_conflict.locked_constraint_conflicts_incoming",
            "suite": "memory_conflict",
            "mode": "deterministic",
            "scenario": "locked_conflict",
            "items": [
                {
                    "item_type": "constraint",
                    "subject": "LAMMPS request",
                    "predicate": "target_temperature",
                    "value": 800,
                    "unit": "K",
                    "text": "The user locked the target temperature at 800 K.",
                    "authority": "user",
                    "source_refs": ["user:locked"],
                    "metadata": {"locked": True},
                },
                {
                    "item_type": "constraint",
                    "subject": "LAMMPS request",
                    "predicate": "target_temperature",
                    "value": 900,
                    "unit": "K",
                    "text": "LLM proposed changing the target temperature to 900 K.",
                    "authority": "llm_inference",
                    "source_refs": ["llm:proposal"],
                },
            ],
            "expected": {
                "conflict_type": "value",
                "detection_mode": "structured",
                "conflict_status": "needs_user",
                "incoming_status": "conflicted",
            },
            "tags": ["memory_conflict", "locked", "needs_user"],
        },
        {
            "case_id": "memory_conflict.registry_quarantines_llm",
            "suite": "memory_conflict",
            "mode": "deterministic",
            "scenario": "authority_quarantine",
            "items": [
                {
                    "item_type": "fact",
                    "subject": "LAMMPS potential source",
                    "predicate": "validated",
                    "value": "true",
                    "text": "Registry validator says the potential source is supported.",
                    "authority": "registry",
                    "source_refs": ["registry:potential"],
                },
                {
                    "item_type": "fact",
                    "subject": "LAMMPS potential source",
                    "predicate": "validated",
                    "value": "false",
                    "text": "LLM inference says the potential source is unsupported.",
                    "authority": "llm_inference",
                    "source_refs": ["llm:potential"],
                },
            ],
            "expected": {
                "conflict_type": "value",
                "detection_mode": "structured",
                "conflict_status": "open",
                "incoming_status": "quarantined",
            },
            "tags": ["memory_conflict", "authority", "quarantine"],
        },
        {
            "case_id": "memory_conflict.semantic_candidate_only",
            "suite": "memory_conflict",
            "mode": "deterministic",
            "scenario": "semantic_candidate",
            "items": [
                {
                    "item_type": "fact",
                    "subject": "LAMMPS potential support",
                    "predicate": "validated",
                    "value": "supported",
                    "text": "Registry says this LAMMPS potential support status is supported for copper.",
                    "authority": "registry",
                    "source_refs": ["registry:potential-support"],
                },
                {
                    "item_type": "fact",
                    "subject": "LAMMPS potential support",
                    "predicate": "status",
                    "value": "unsupported",
                    "text": "RAG note says this LAMMPS potential support status is unsupported for copper.",
                    "authority": "rag",
                    "source_refs": ["rag:potential-support"],
                },
            ],
            "expected": {"conflict_type": "polarity", "detection_mode": "semantic_candidate", "conflict_status": "open"},
            "tags": ["memory_conflict", "semantic_candidate"],
        },
    ]


def build_context_compression_cases() -> list[dict[str, Any]]:
    long_rag_text = " ".join(
        [
            "LAMMPS compute rdf calculates radial distribution functions for selected atom groups after a run.",
            "The command bins pair distances and reports coordination-like structural signals.",
            "For metallic systems, the first RDF peak often describes the nearest-neighbour shell.",
            "The analysis should be performed after equilibration so transient heating artifacts are reduced.",
            "A dump trajectory can be post-processed, but in-script computes are useful for repeatable workflows.",
            "Users should choose bin counts and cutoff distances that match the box size and material density.",
        ]
        * 4
    )
    lammps_script = "\n".join(
        [
            "units metal",
            "atom_style atomic",
            "boundary p p p",
            "read_data data.cu",
            "pair_style eam",
            "pair_coeff * * Cu_u3.eam",
            "velocity all create 800.0 12345",
            "fix 1 all nvt temp 800.0 800.0 0.1",
            "thermo 100",
            "run 10000",
        ]
        * 4
    )
    return [
        {
            "case_id": "context_compression.rag_textrank_l3_pointer",
            "suite": "context_compression",
            "mode": "deterministic",
            "scenario": "textrank_l2_traceability",
            "query": "How should I use LAMMPS compute rdf after equilibration?",
            "items": [
                {
                    "item_type": "evidence",
                    "subject": "LAMMPS RDF documentation",
                    "predicate": "supports_query",
                    "value": "compute rdf",
                    "text": long_rag_text,
                    "authority": "rag",
                    "source_refs": ["https://docs.lammps.org/compute_rdf.html"],
                }
            ],
            "expected": {"requires_l3_trace": True},
            "tags": ["context_compression", "textrank", "l3"],
        },
        {
            "case_id": "context_compression.lammps_script_protected",
            "suite": "context_compression",
            "mode": "deterministic",
            "scenario": "noncompressible_lammps_script",
            "query": "LAMMPS pair_style nvt run script",
            "items": [
                {
                    "item_type": "evidence",
                    "subject": "LAMMPS input script",
                    "predicate": "generated_script",
                    "value": "input.in",
                    "text": lammps_script,
                    "authority": "execution",
                    "source_refs": ["input.in"],
                }
            ],
            "expected": {"protected": True},
            "tags": ["context_compression", "lammps_script", "protection"],
        },
        {
            "case_id": "context_compression.numeric_table_protected",
            "suite": "context_compression",
            "mode": "deterministic",
            "scenario": "noncompressible_numeric_table",
            "query": "LAMMPS thermo table temp energy pressure",
            "items": [
                {
                    "item_type": "evidence",
                    "subject": "LAMMPS thermo table",
                    "predicate": "thermo_rows",
                    "value": "thermo.csv",
                    "text": "Step Temp PE Press\n0 300 -3.1 1000\n100 500 -3.0 1200\n200 800 -2.8 1500\n300 799 -2.7 1490",
                    "authority": "execution",
                    "source_refs": ["thermo.csv"],
                }
            ],
            "expected": {"protected": True},
            "tags": ["context_compression", "numeric_table", "protection"],
        },
    ]


def build_mcp_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "mcp.initialize",
            "suite": "mcp",
            "mode": "deterministic",
            "request": {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            "expected": {"protocol_version": "2024-11-05", "server_name": "materials-agent-mcp"},
            "tags": ["mcp", "protocol"],
        },
        {
            "case_id": "mcp.tools_list",
            "suite": "mcp",
            "mode": "deterministic",
            "request": {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            "expected": {"required_tools": ["phase_diagram.run", "phase_diagram.run_structured", "lammps.run", "lammps.run_structured", "system.diagnostics"]},
            "tags": ["mcp", "protocol"],
        },
        {
            "case_id": "mcp.phase_registry_search",
            "suite": "mcp",
            "mode": "deterministic",
            "request": {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "phase_diagram.registry_search", "arguments": {"query": "Al-Zn"}}},
            "expected": {"matched": True, "system_name": "Al-Zn"},
            "tags": ["mcp", "registry"],
        },
        {
            "case_id": "mcp.phase_rag_search",
            "suite": "mcp",
            "mode": "deterministic",
            "request": {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "phase_diagram.rag_search", "arguments": {"query": "铝锌二元相图", "top_k": 3}}},
            "expected": {"matched": True, "top_system_name": "Al-Zn"},
            "tags": ["mcp", "rag"],
        },
        {
            "case_id": "mcp.phase_run_structured",
            "suite": "mcp",
            "mode": "deterministic",
            "request": {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "phase_diagram.run_structured",
                    "arguments": {
                        "conversation_id": "mcp-bench-phase",
                        "message": "structured phase benchmark",
                        "request": {
                            "system_name": "Al-Zn",
                            "diagram_type": "binary",
                            "temperature_min": 300.0,
                            "temperature_max": 1000.0,
                            "pressure": 101325.0,
                            "step_size": 25.0,
                            "notes": "benchmark structured path",
                        },
                    },
                },
            },
            "expected": {"run_id": "phase-run-1"},
            "tags": ["mcp", "structured"],
        },
        {
            "case_id": "mcp.lammps_run_structured",
            "suite": "mcp",
            "mode": "deterministic",
            "request": {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "lammps.run_structured",
                    "arguments": {
                        "conversation_id": "mcp-bench-lammps",
                        "message": "structured lammps benchmark",
                        "request": {
                            "material": "Cu",
                            "potential_family": "eam",
                            "task_type": "heating",
                            "temperature": 800,
                            "steps": 4000,
                            "ensemble": "NVT",
                            "box_size": 4,
                            "time_step": 0.001,
                            "dump_file": "dump.atom",
                            "notes": "benchmark structured path",
                        },
                    },
                },
            },
            "expected": {"run_id": "lammps-run-1"},
            "tags": ["mcp", "structured"],
        },
    ]


def build_lammps_red_blue_cases() -> list[dict[str, Any]]:
    base_request = {
        "material": "Cu",
        "potential_family": "eam",
        "task_type": "heating",
        "temperature": 800,
        "steps": 1000,
        "ensemble": "NVT",
        "box_size": 4,
        "initial_temp": 300,
        "time_step": 0.001,
        "dump_file": "dump.atom",
        "notes": "benchmark red-blue request",
    }
    input_script = "\n".join(
        [
            "units metal",
            "variable targetTemp equal 800",
            "variable runSteps equal 1000",
            "timestep 0.001",
            "thermo 100",
            "thermo_style custom step temp pe ke etotal press",
            "dump 1 all custom 100 dump.atom id type x y z",
            "fix 1 all nvt temp 300 800 0.1",
            "run 1000",
        ]
    )
    mismatched_temperature_script = input_script.replace("variable targetTemp equal 800", "variable targetTemp equal 900").replace(
        "fix 1 all nvt temp 300 800 0.1",
        "fix 1 all nvt temp 300 900 0.1",
    )
    materials_rag_context = {
        "planning": {
            "query": "Run Cu heating with EAM at 800 K.",
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
            "source": "benchmark_fixture",
        }
    }
    return [
        {
            "case_id": "red_blue.red_missing_thermo_artifact",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "red_review",
            "fixture": {
                "request": base_request,
                "run_mode": "real",
                "artifacts": ["in.lammps", "report.md"],
                "metrics": {},
                "validation": {"is_reasonable": True, "errors": []},
                "error": "",
                "input_script": mismatched_temperature_script,
                "quality_report": {
                    "run_mode": "real",
                    "passed": True,
                    "scientific_result_passed": True,
                    "synthetic_thermo": False,
                    "requested_steps": 1000,
                },
            },
            "expected": {"passed": False, "fatal_finding": True, "requires_primary_evidence": True, "consistency_blocked": True},
            "tags": ["red_review", "fatal", "evidence", "consistency"],
        },
        {
            "case_id": "red_blue.red_mock_advisory_non_blocking",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "red_review",
            "fixture": {
                "request": base_request,
                "run_mode": "mock",
                "artifacts": ["in.lammps", "thermo.csv", "plot.png", "report.md"],
                "metrics": {"synthetic_thermo": True},
                "validation": {"is_reasonable": True, "errors": []},
                "error": "LAMMPS executable not found.",
                "input_script": input_script,
                "quality_report": {
                    "run_mode": "mock",
                    "passed": True,
                    "scientific_result_passed": False,
                    "synthetic_thermo": True,
                    "requested_steps": 1000,
                },
                "materials_rag_context": materials_rag_context,
            },
            "expected": {"passed": True, "valid_run": True, "fatal_finding": False, "rag_evidence_traceable": True},
            "tags": ["red_review", "valid", "mock_advisory", "rag_evidence"],
        },
        {
            "case_id": "red_blue.blue_locked_material_rejected",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "blue_policy_payload",
            "fixture": {
                "request": base_request,
                "stage": "review",
                "issues": ["review suggested switching material"],
                "payload": {"material": "Al", "time_step": 0.002},
            },
            "expected": {"policy_accepted": False, "locked_field_protected": True, "termination_reason": "patch_requires_user_confirmation"},
            "tags": ["blue_patch", "locked_constraint"],
        },
        {
            "case_id": "red_blue.blue_native_safe_patch_verified",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "blue_policy_patch",
            "fixture": {
                "request": base_request,
                "patch": {
                    "schema_version": "lammps-blue-patch/v1",
                    "operations": [
                        {"op": "modify", "path": "time_step", "before": 0.001, "after": 0.002, "reason": "Use safer timestep."},
                        {"op": "verify", "path": "lammps_request", "reason": "Revalidate before retry."},
                    ],
                    "risk": "low",
                    "source": "benchmark_native_patch",
                },
            },
            "expected": {"policy_accepted": True, "patch_verified": True, "request_changed": True},
            "tags": ["blue_patch", "verified", "native"],
        },
        {
            "case_id": "red_blue.blue_unknown_path_rejected",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "blue_policy_patch",
            "fixture": {
                "request": base_request,
                "patch": {
                    "schema_version": "lammps-blue-patch/v1",
                    "operations": [{"op": "modify", "path": "shell", "before": None, "after": "rm -rf /", "reason": "malformed path"}],
                    "risk": "high",
                    "source": "benchmark_bad_patch",
                },
            },
            "expected": {"policy_accepted": False, "termination_reason": "patch_policy_rejected"},
            "tags": ["blue_patch", "rejected", "unknown_path"],
        },
        {
            "case_id": "red_blue.convergence_oscillation_stops",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "repair_convergence",
            "fixture": {
                "before_request": base_request,
                "current_request": {**base_request, "time_step": 0.002},
                "candidate_request": base_request,
                "stage": "validation",
                "repair_budget": 2,
                "history_score": 50.0,
            },
            "expected": {"allow_repair": False, "termination_reason": "repair_oscillation_detected", "bounded_loop": True},
            "tags": ["convergence", "oscillation"],
        },
        {
            "case_id": "red_blue.convergence_stagnation_stops",
            "suite": "lammps_red_blue",
            "mode": "deterministic",
            "scenario": "repair_convergence",
            "fixture": {
                "before_request": base_request,
                "current_request": {**base_request, "time_step": 0.002},
                "candidate_request": None,
                "stage": "review",
                "repair_budget": 2,
                "history_score": 50.0,
                "current_score": 50.2,
            },
            "expected": {"allow_repair": False, "termination_reason": "repair_stagnation_detected", "bounded_loop": True},
            "tags": ["convergence", "stagnation"],
        },
    ]


def build_review_json_fallback_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "json_fallback.red_strict",
            "suite": "review_json_fallback",
            "mode": "deterministic",
            "payload_type": "red_review",
            "schema": "ReviewReport",
            "raw": json.dumps({"schema_version": "lammps-red-review/v1", "summary": "Review passed.", "findings": [], "evidence_refs": []}),
            "expected": {"success": True, "parse_mode": "strict", "recoverable": True},
            "tags": ["red_review", "strict"],
        },
        {
            "case_id": "json_fallback.red_code_fence_alias_trailing",
            "suite": "review_json_fallback",
            "mode": "deterministic",
            "payload_type": "red_review",
            "schema": "ReviewReport",
            "raw": "Here is the review:\n```json\n{\"schema_version\":\"lammps-red-review/v1\",\"summary\":\"Review passed.\",\"findings_list\":[{\"dimension\":\"evidence\",\"severity\":\"warning\",\"message\":\"Advisory citation gap.\"}],\"evidence_refs\":[],}\n```",
            "expected": {
                "success": True,
                "parse_mode": "normalized",
                "recoverable": True,
                "normalizations": ["extracted_first_balanced_json_object", "removed_trailing_commas", "normalized_findings_list_alias"],
            },
            "tags": ["red_review", "normalized"],
        },
        {
            "case_id": "json_fallback.blue_uppercase_op_trailing",
            "suite": "review_json_fallback",
            "mode": "deterministic",
            "payload_type": "blue_patch",
            "schema": "RepairPatch",
            "raw": "{\"schema_version\":\"lammps-blue-patch/v1\",\"operations\":[{\"op\":\"MODIFY\",\"path\":\"time_step\",\"before\":0.001,\"after\":0.002,\"reason\":\"safer\"},],}",
            "expected": {"success": True, "parse_mode": "normalized", "recoverable": True, "normalizations": ["removed_trailing_commas", "normalized_operation_case"]},
            "tags": ["blue_patch", "normalized"],
        },
        {
            "case_id": "json_fallback.red_deterministic_fallback",
            "suite": "review_json_fallback",
            "mode": "deterministic",
            "payload_type": "red_review",
            "schema": "ReviewReport",
            "raw": "not valid json at all",
            "expected": {"success": True, "parse_mode": "deterministic_fallback", "recoverable": True, "deterministic_fallback": True},
            "tags": ["red_review", "deterministic_fallback"],
        },
        {
            "case_id": "json_fallback.blue_invalid_operation_rejected",
            "suite": "review_json_fallback",
            "mode": "deterministic",
            "payload_type": "blue_patch",
            "schema": "RepairPatch",
            "raw": "{\"operations\":[{\"op\":\"EXECUTE\",\"path\":\"shell\",\"after\":\"rm -rf /\"}]}",
            "expected": {"success": False, "parse_mode": "rejected", "invalid_patch": True},
            "tags": ["blue_patch", "invalid", "safe_reject"],
        },
    ]


def build_orchestration_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "orchestration.parallel_preflight_speedup",
            "suite": "orchestration",
            "mode": "deterministic_fake_dag",
            "scenario": "parallel_preflight_speedup",
            "resource_limits": {"network": 2, "cpu": 4, "simulation": 1},
            "default_delay_seconds": 0.08,
            "node_delays": {
                "preflight_merge": 0.02,
                "red_pre_execution_review": 0.02,
            },
            "expected": {
                "min_speedup": 0.25,
                "topological_order": [
                    "constraint_extract",
                    "materials_rag_search",
                    "registry_lookup",
                    "attachment_inspection",
                    "runtime_diagnostics",
                    "preflight_merge",
                    "red_pre_execution_review",
                ],
            },
            "tags": ["orchestration", "dag", "speedup", "preflight"],
        },
        {
            "case_id": "orchestration.network_semaphore_limit",
            "suite": "orchestration",
            "mode": "deterministic_fake_dag",
            "scenario": "network_semaphore_limit",
            "resource_limits": {"network": 1, "cpu": 1, "simulation": 1},
            "node_delay_seconds": 0.03,
            "expected": {"max_active_network": 1},
            "tags": ["orchestration", "semaphore", "network"],
        },
        {
            "case_id": "orchestration.level1_optional_fallback",
            "suite": "orchestration",
            "mode": "deterministic_fake_dag",
            "scenario": "level1_optional_fallback",
            "expected": {
                "degradation_level": "level_1_fallback",
                "fallback_nodes": ["materials_rag_search"],
            },
            "tags": ["orchestration", "degradation", "fallback"],
        },
        {
            "case_id": "orchestration.level2_replan_checkpoint_reuse",
            "suite": "orchestration",
            "mode": "deterministic_fake_dag",
            "scenario": "level2_replan_checkpoint_reuse",
            "expected": {
                "degradation_level": "level_2_replan",
                "invalidated_nodes": ["red_pre_execution_review"],
                "reused_nodes_contains": ["constraint_extract", "materials_rag_search", "registry_lookup", "preflight_merge"],
            },
            "tags": ["orchestration", "replan", "checkpoint_reuse"],
        },
        {
            "case_id": "orchestration.level3_global_timeout_partial_report",
            "suite": "orchestration",
            "mode": "deterministic_fake_dag",
            "scenario": "level3_global_timeout_partial_report",
            "global_timeout_seconds": 0.01,
            "node_delay_seconds": 0.05,
            "expected": {
                "degradation_level": "level_3_partial_report",
                "termination_reason": "global_timeout",
                "scientific_result_available": False,
            },
            "tags": ["orchestration", "timeout", "partial_report"],
        },
    ]


def build_judge_calibration_cases() -> list[dict[str, Any]]:
    base_observation = {
        "route_name": "lammps.generate",
        "compute_domain": "lammps",
        "locked_constraints": {"material": "Cu", "temperature": 800, "steps": 1000, "potential_family": "eam"},
        "completed_tools": ["lammps_request_interpreter", "lammps_registry_lookup", "lammps_execute", "lammps_quality_gate"],
        "artifacts": ["request.json", "in.lammps", "thermo.csv", "quality_report.json", "report.md"],
        "provenance": {"actual": "real", "claimed": "real"},
        "execution": {"success": True},
        "physical_gate": {"passed": True},
        "claims": [{"claim_id": "quality_passed", "text": "Quality gate passed.", "status": "supported"}],
        "citations": [{"evidence_id": "quality:passed", "supports": True, "exists": True}],
        "final_response": "真实 LAMMPS 执行完成，quality gate passed；结果引用 quality_report.json 和 thermo.csv。",
    }
    expected = {
        "route_name": "lammps.generate",
        "compute_domain": "lammps",
        "request": {"material": "Cu", "temperature": 800, "steps": 1000, "potential_family": "eam"},
        "required_steps": ["lammps_request_interpreter", "lammps_registry_lookup", "lammps_execute", "lammps_quality_gate"],
        "required_artifacts": ["request.json", "in.lammps", "thermo.csv", "quality_report.json", "report.md"],
        "required_evidence": ["quality:passed"],
    }
    cases = [
        {
            "case_id": "judge_calibration.valid_lammps_report",
            "suite": "llm_judge",
            "mode": "deterministic_judge_contract",
            "prompt": "请判断这次 Cu 800K LAMMPS 报告是否可信。",
            "expected": expected,
            "observation": base_observation,
            "human_scores": {
                "factuality": 5,
                "logical_consistency": 5,
                "citation_quality": 5,
                "physical_validity": 5,
                "actionable_clarity": 5,
            },
            "raw_judge_payload": json.dumps(
                {
                    "scores": {
                        "factuality": 5,
                        "logical_consistency": 5,
                        "citation_quality": 5,
                        "physical_validity": 5,
                        "actionable_clarity": 5,
                    },
                    "overall_score": 5,
                    "passed": True,
                    "hard_gate_passed": True,
                    "issues": [],
                },
                ensure_ascii=False,
            ),
            "tags": ["judge", "calibration", "valid"],
        },
        {
            "case_id": "judge_calibration.synthetic_claimed_real_hard_gate",
            "suite": "llm_judge",
            "mode": "deterministic_judge_contract",
            "prompt": "请判断 synthetic thermo 是否被错误描述为真实 LAMMPS。",
            "expected": {**expected, "required_evidence": []},
            "observation": {
                **base_observation,
                "provenance": {"actual": "synthetic", "claimed": "real"},
                "execution": {"success": False},
                "physical_gate": {"passed": False},
                "claims": [],
                "citations": [],
                "final_response": "真实 LAMMPS 已成功运行，结果可靠。",
            },
            "human_scores": {
                "factuality": 2,
                "logical_consistency": 2,
                "citation_quality": 5,
                "physical_validity": 1,
                "actionable_clarity": 3,
            },
            "raw_judge_payload": json.dumps(
                {
                    "scores": {
                        "factuality": 5,
                        "logical_consistency": 5,
                        "citation_quality": 5,
                        "physical_validity": 5,
                        "actionable_clarity": 5,
                    },
                    "overall_score": 5,
                    "passed": True,
                    "hard_gate_passed": False,
                },
                ensure_ascii=False,
            ),
            "tags": ["judge", "calibration", "hard_gate", "synthetic"],
        },
        {
            "case_id": "judge_calibration.missing_citation",
            "suite": "llm_judge",
            "mode": "deterministic_judge_contract",
            "prompt": "请判断缺少 citation 的 LAMMPS 解释。",
            "expected": expected,
            "observation": {
                **base_observation,
                "citations": [],
                "final_response": "LAMMPS run looks correct, but the answer did not cite the required quality evidence.",
            },
            "human_scores": {
                "factuality": 5,
                "logical_consistency": 5,
                "citation_quality": 1,
                "physical_validity": 5,
                "actionable_clarity": 5,
            },
            "raw_judge_payload": json.dumps(
                {
                    "scores": {
                        "factuality": 5,
                        "logical_consistency": 5,
                        "citation_quality": 1,
                        "physical_validity": 5,
                        "actionable_clarity": 5,
                    },
                    "overall_score": 4.2,
                    "passed": False,
                    "hard_gate_passed": True,
                    "issues": ["missing required citation"],
                },
                ensure_ascii=False,
            ),
            "tags": ["judge", "calibration", "citation"],
        },
        {
            "case_id": "judge_calibration.invalid_json_deterministic_fallback",
            "suite": "llm_judge",
            "mode": "deterministic_judge_contract",
            "prompt": "请判断 invalid judge JSON fallback 是否安全。",
            "expected": expected,
            "observation": base_observation,
            "human_scores": {
                "factuality": 5,
                "logical_consistency": 5,
                "citation_quality": 5,
                "physical_validity": 5,
                "actionable_clarity": 5,
            },
            "raw_judge_payload": "not valid json",
            "tags": ["judge", "calibration", "fallback"],
        },
        {
            "case_id": "judge_calibration.normalized_json_fence",
            "suite": "llm_judge",
            "mode": "deterministic_judge_contract",
            "prompt": "请判断 fenced JSON 是否能 normalized parse。",
            "expected": expected,
            "observation": base_observation,
            "human_scores": {
                "factuality": 5,
                "logical_consistency": 5,
                "citation_quality": 5,
                "physical_validity": 5,
                "actionable_clarity": 4,
            },
            "raw_judge_payload": "Judge result:\n```json\n{\"scores\":{\"factuality\":5,\"logical_consistency\":5,\"citation_quality\":5,\"physical_validity\":5,\"actionable_clarity\":4,},\"overall_score\":4.8,\"passed\":true,\"hard_gate_passed\":true,}\n```",
            "tags": ["judge", "calibration", "normalized"],
        },
    ]
    def score(
        *,
        factuality: int = 5,
        logical_consistency: int = 5,
        citation_quality: int = 5,
        physical_validity: int = 5,
        actionable_clarity: int = 5,
    ) -> dict[str, int]:
        return {
            "factuality": factuality,
            "logical_consistency": logical_consistency,
            "citation_quality": citation_quality,
            "physical_validity": physical_validity,
            "actionable_clarity": actionable_clarity,
        }

    def judge_payload(
        scores: dict[str, int],
        *,
        passed: bool | None = None,
        hard_gate_passed: bool = True,
        issues: list[str] | None = None,
    ) -> str:
        overall = round(sum(scores.values()) / len(scores), 4)
        return json.dumps(
            {
                "scores": scores,
                "overall_score": overall,
                "passed": hard_gate_passed and (overall >= 4.0 if passed is None else passed),
                "hard_gate_passed": hard_gate_passed,
                "issues": issues or [],
            },
            ensure_ascii=False,
        )

    def judge_case(
        slug: str,
        *,
        prompt: str,
        scores: dict[str, int],
        observation: dict[str, Any] | None = None,
        observation_updates: dict[str, Any] | None = None,
        expected_override: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        hard_gate_passed: bool = True,
        passed: bool | None = None,
        issues: list[str] | None = None,
        raw_payload: str | None = None,
    ) -> dict[str, Any]:
        resolved_observation = observation if observation is not None else {**base_observation, **(observation_updates or {})}
        return {
            "case_id": f"judge_calibration.{slug}",
            "suite": "llm_judge",
            "mode": "deterministic_judge_contract",
            "prompt": prompt,
            "expected": {**expected, **(expected_override or {})},
            "observation": resolved_observation,
            "human_scores": scores,
            "raw_judge_payload": raw_payload if raw_payload is not None else judge_payload(scores, passed=passed, hard_gate_passed=hard_gate_passed, issues=issues),
            "tags": ["judge", "calibration", *(tags or [])],
        }

    valid_scores = score()
    citation_bad_scores = score(citation_quality=1)
    logic_bad_scores = score(logical_consistency=2, actionable_clarity=4)
    factual_bad_scores = score(factuality=2, logical_consistency=4, actionable_clarity=4)
    physical_bad_scores = score(logical_consistency=4, physical_validity=2, actionable_clarity=4)
    hard_gate_scores = score(factuality=2, logical_consistency=2, citation_quality=4, physical_validity=1, actionable_clarity=3)

    cases.extend(
        [
            judge_case(
                "valid_al_equilibration_report",
                prompt="请判断 Al 600K 平衡报告是否基于完整 LAMMPS 证据。",
                expected_override={
                    "request": {"material": "Al", "temperature": 600, "steps": 1500, "potential_family": "eam"},
                    "required_evidence": ["quality:passed", "pressure:stable"],
                },
                observation_updates={
                    "locked_constraints": {"material": "Al", "temperature": 600, "steps": 1500, "potential_family": "eam"},
                    "citations": [
                        {"evidence_id": "quality:passed", "supports": True, "exists": True},
                        {"evidence_id": "pressure:stable", "supports": True, "exists": True},
                    ],
                    "claims": [
                        {"claim_id": "quality_passed", "text": "Quality gate passed.", "status": "supported"},
                        {"claim_id": "pressure_stable", "text": "Pressure is stable after equilibration.", "status": "supported"},
                    ],
                    "final_response": "真实 LAMMPS 执行完成；质量门和压力稳定性证据均已引用。",
                },
                scores=valid_scores,
                tags=["valid", "npt"],
            ),
            judge_case(
                "valid_ni_heating_report",
                prompt="请判断 Ni 900K heating 报告是否可信。",
                expected_override={
                    "request": {"material": "Ni", "temperature": 900, "steps": 2000, "potential_family": "eam"},
                    "required_evidence": ["quality:passed", "thermo:metadata"],
                },
                observation_updates={
                    "locked_constraints": {"material": "Ni", "temperature": 900, "steps": 2000, "potential_family": "eam"},
                    "artifacts": ["request.json", "in.lammps", "thermo.csv", "thermo_metadata.json", "quality_report.json", "report.md"],
                    "citations": [
                        {"evidence_id": "quality:passed", "supports": True, "exists": True},
                        {"evidence_id": "thermo:metadata", "supports": True, "exists": True},
                    ],
                    "final_response": "真实 LAMMPS heating run 完成，并引用 thermo metadata 与 quality gate。",
                },
                scores=valid_scores,
                tags=["valid", "heating"],
            ),
            judge_case(
                "valid_npt_report",
                prompt="请判断 NPT 报告是否同时满足物理 gate 和引用要求。",
                expected_override={
                    "request": {"material": "Al", "temperature": 600, "steps": 1500, "potential_family": "eam"},
                    "required_evidence": ["quality:passed", "pressure:stable"],
                },
                observation_updates={
                    "locked_constraints": {"material": "Al", "temperature": 600, "steps": 1500, "potential_family": "eam"},
                    "completed_tools": [*base_observation["completed_tools"], "lammps_postprocess"],
                    "citations": [
                        {"evidence_id": "quality:passed", "supports": True, "exists": True},
                        {"evidence_id": "pressure:stable", "supports": True, "exists": True},
                    ],
                    "final_response": "NPT 平衡完成；quality_report.json 和 pressure stability 证据均支持结论。",
                },
                scores=valid_scores,
                tags=["valid", "npt"],
            ),
            judge_case(
                "valid_repaired_run_report",
                prompt="请判断修复后的 Cu 800K LAMMPS 结果是否可以采信。",
                observation_updates={
                    "completed_tools": [*base_observation["completed_tools"], "lammps_result_review", "blue_repair_policy"],
                    "artifacts": [*base_observation["artifacts"], "repair_patch.json"],
                    "claims": [
                        {"claim_id": "first_run_failed", "text": "Initial run failed quality gate.", "status": "supported"},
                        {"claim_id": "repair_preserved_constraints", "text": "Repair preserved locked constraints.", "status": "supported"},
                        {"claim_id": "quality_passed", "text": "Repaired run passed quality gate.", "status": "supported"},
                    ],
                    "final_response": "第一次运行未通过，Blue patch 仅调整 timestep；重跑后质量门通过，且未修改 Cu/800K/1000 steps。",
                },
                scores=valid_scores,
                tags=["valid", "repair"],
            ),
            judge_case(
                "valid_multihop_evidence_chain",
                prompt="请判断报告是否完整串联 user/registry/RAG/run log/quality gate 证据。",
                expected_override={"required_evidence": ["user:cu-800", "registry:cu-eam", "rag:timestep", "quality:passed"]},
                observation_updates={
                    "required_hops": [
                        {"hop_id": "user", "evidence_id": "user:cu-800", "completed": True},
                        {"hop_id": "registry", "evidence_id": "registry:cu-eam", "completed": True},
                        {"hop_id": "rag", "evidence_id": "rag:timestep", "completed": True},
                        {"hop_id": "quality", "evidence_id": "quality:passed", "completed": True},
                    ],
                    "citations": [
                        {"evidence_id": "user:cu-800", "supports": True, "exists": True},
                        {"evidence_id": "registry:cu-eam", "supports": True, "exists": True},
                        {"evidence_id": "rag:timestep", "supports": True, "exists": True},
                        {"evidence_id": "quality:passed", "supports": True, "exists": True},
                    ],
                    "final_response": "结论由用户锁定约束、registry、RAG timestep 建议和 quality gate 串联支持。",
                },
                scores=valid_scores,
                tags=["valid", "multihop"],
            ),
            judge_case(
                "missing_lammps_execute_tool",
                prompt="请判断缺少 lammps_execute 的报告能否算完成。",
                observation_updates={"completed_tools": ["lammps_request_interpreter", "lammps_registry_lookup", "lammps_quality_gate"]},
                scores=logic_bad_scores,
                passed=False,
                issues=["missing lammps_execute"],
                tags=["tool_chain"],
            ),
            judge_case(
                "missing_quality_gate_tool",
                prompt="请判断没有 quality gate 的 run 是否可采信。",
                observation_updates={"completed_tools": ["lammps_request_interpreter", "lammps_registry_lookup", "lammps_execute"]},
                scores=logic_bad_scores,
                passed=False,
                issues=["missing lammps_quality_gate"],
                tags=["tool_chain", "quality"],
            ),
            judge_case(
                "missing_thermo_artifact",
                prompt="请判断没有 thermo.csv 的报告是否足够。",
                observation_updates={"artifacts": ["request.json", "in.lammps", "quality_report.json", "report.md"]},
                scores=logic_bad_scores,
                passed=False,
                issues=["missing thermo.csv"],
                tags=["artifact"],
            ),
            judge_case(
                "missing_report_artifact",
                prompt="请判断缺少 final report artifact 的结果是否完整。",
                observation_updates={"artifacts": ["request.json", "in.lammps", "thermo.csv", "quality_report.json"]},
                scores=logic_bad_scores,
                passed=False,
                issues=["missing report.md"],
                tags=["artifact"],
            ),
            judge_case(
                "citation_exists_but_not_supporting",
                prompt="请判断存在 citation 但不支持结论时是否应扣分。",
                observation_updates={"citations": [{"evidence_id": "quality:passed", "supports": False, "exists": True}]},
                scores=citation_bad_scores,
                passed=False,
                issues=["citation does not support claim"],
                tags=["citation"],
            ),
            judge_case(
                "wrong_citation_id",
                prompt="请判断引用不存在的 evidence id 是否可接受。",
                observation_updates={"citations": [{"evidence_id": "quality:missing", "supports": True, "exists": False}]},
                scores=citation_bad_scores,
                passed=False,
                issues=["wrong citation id"],
                tags=["citation"],
            ),
            judge_case(
                "unsupported_minor_claim",
                prompt="请判断存在非关键 unsupported claim 时如何评分。",
                observation_updates={
                    "claims": [
                        {"claim_id": "quality_passed", "text": "Quality gate passed.", "status": "supported"},
                        {"claim_id": "minor_temperature_trend", "text": "Temperature is perfectly monotonic.", "status": "unsupported"},
                    ],
                },
                scores=factual_bad_scores,
                passed=False,
                issues=["unsupported minor claim"],
                tags=["claim"],
            ),
            judge_case(
                "contradicted_critical_claim",
                prompt="请判断 critical claim 被证据反驳时是否触发 hard gate。",
                observation_updates={
                    "claims": [{"claim_id": "quality_passed", "text": "Quality gate passed.", "status": "contradicted", "critical": True}],
                    "final_response": "质量门通过，但记录中的质量门实际失败。",
                },
                scores=hard_gate_scores,
                hard_gate_passed=False,
                passed=False,
                issues=["critical claim contradicted"],
                tags=["claim", "hard_gate"],
            ),
            judge_case(
                "locked_temperature_changed",
                prompt="请判断报告是否静默修改了用户锁定温度。",
                observation_updates={"locked_constraints": {"material": "Cu", "temperature": 900, "steps": 1000, "potential_family": "eam"}},
                scores=hard_gate_scores,
                hard_gate_passed=False,
                passed=False,
                issues=["locked temperature changed"],
                tags=["locked_constraint", "hard_gate"],
            ),
            judge_case(
                "locked_material_changed",
                prompt="请判断报告是否静默把 Cu 改成 Al。",
                observation_updates={"locked_constraints": {"material": "Al", "temperature": 800, "steps": 1000, "potential_family": "eam"}},
                scores=hard_gate_scores,
                hard_gate_passed=False,
                passed=False,
                issues=["locked material changed"],
                tags=["locked_constraint", "hard_gate"],
            ),
            judge_case(
                "mock_honestly_reported",
                prompt="请判断 mock run 被诚实标注时应如何评价。",
                observation_updates={
                    "provenance": {"actual": "mock", "claimed": "mock"},
                    "execution": {"success": False},
                    "physical_gate": {"passed": False},
                    "final_response": "没有真实 LAMMPS 可执行文件；这里只生成 mock artifacts，不能当作科学结果。",
                },
                scores=physical_bad_scores,
                passed=False,
                issues=["mock run honestly reported"],
                tags=["provenance", "mock"],
            ),
            judge_case(
                "mock_claimed_real",
                prompt="请判断 mock run 被声称为真实执行时是否 hard gate。",
                observation_updates={
                    "provenance": {"actual": "mock", "claimed": "real"},
                    "execution": {"success": False},
                    "physical_gate": {"passed": False},
                    "final_response": "真实 LAMMPS 已成功完成，结果可靠。",
                },
                scores=hard_gate_scores,
                hard_gate_passed=False,
                passed=False,
                issues=["mock claimed as real"],
                tags=["provenance", "hard_gate"],
            ),
            judge_case(
                "physical_gate_failed_honest",
                prompt="请判断物理 gate 失败但回答诚实时怎么评分。",
                observation_updates={
                    "execution": {"success": False},
                    "physical_gate": {"passed": False},
                    "final_response": "该 LAMMPS 运行未成功，quality gate 失败，因此不把 thermo 当成科学结论。",
                },
                scores=physical_bad_scores,
                passed=False,
                issues=["physical gate failed"],
                tags=["physical_gate"],
            ),
            judge_case(
                "physical_gate_failed_claims_success",
                prompt="请判断物理 gate 失败却声称成功时是否 hard gate。",
                observation_updates={
                    "execution": {"success": False},
                    "physical_gate": {"passed": False},
                    "final_response": "运行成功完成，模拟成功，所有结果可靠。",
                },
                scores=hard_gate_scores,
                hard_gate_passed=False,
                passed=False,
                issues=["failed run claims success"],
                tags=["physical_gate", "hard_gate"],
            ),
            judge_case(
                "wrong_route",
                prompt="请判断 LAMMPS 请求被路由到 phase diagram 是否错误。",
                observation_updates={"route_name": "phase_diagram.generate"},
                scores=logic_bad_scores,
                passed=False,
                issues=["wrong route"],
                tags=["routing"],
            ),
            judge_case(
                "wrong_compute_domain",
                prompt="请判断 compute_domain 与 LAMMPS 任务不一致时如何评分。",
                observation_updates={"compute_domain": "phase_diagram"},
                scores=logic_bad_scores,
                passed=False,
                issues=["wrong compute domain"],
                tags=["routing"],
            ),
            judge_case(
                "no_required_citation_ok",
                prompt="请判断无需 evidence 的澄清类响应是否不能误扣 citation。",
                expected_override={"required_evidence": []},
                observation_updates={"citations": [], "final_response": "信息不足，需要用户确认 potential file 后才能执行。"},
                scores=valid_scores,
                tags=["citation", "not_applicable"],
            ),
            judge_case(
                "non_verifiable_claim_honest",
                prompt="请判断 not_verifiable claim 被诚实标注时是否可接受。",
                observation_updates={
                    "claims": [
                        {"claim_id": "quality_passed", "text": "Quality gate passed.", "status": "supported"},
                        {"claim_id": "future_work", "text": "Longer run may improve equilibration.", "status": "not_verifiable"},
                    ],
                },
                scores=valid_scores,
                tags=["claim", "not_verifiable"],
            ),
            judge_case(
                "partial_hop_missing",
                prompt="请判断多跳证据链缺一跳时是否应扣分。",
                expected_override={"required_evidence": ["user:cu-800", "registry:cu-eam", "quality:passed"]},
                observation_updates={
                    "required_hops": [
                        {"hop_id": "user", "evidence_id": "user:cu-800", "completed": True},
                        {"hop_id": "registry", "evidence_id": "registry:cu-eam", "completed": False},
                        {"hop_id": "quality", "evidence_id": "quality:passed", "completed": True},
                    ],
                    "citations": [
                        {"evidence_id": "user:cu-800", "supports": True, "exists": True},
                        {"evidence_id": "quality:passed", "supports": True, "exists": True},
                    ],
                },
                scores=logic_bad_scores,
                passed=False,
                issues=["partial evidence hop missing"],
                tags=["multihop"],
            ),
            judge_case(
                "balanced_json_recovery",
                prompt="请判断前后夹杂文本的 JSON 能否恢复。",
                observation=base_observation,
                scores=valid_scores,
                raw_payload=f"Judge says:\n{judge_payload(valid_scores)}\nEnd.",
                tags=["json_recovery"],
            ),
            judge_case(
                "tail_comma_recovery",
                prompt="请判断尾逗号 JSON 能否 normalized parse。",
                observation=base_observation,
                scores=score(actionable_clarity=4),
                raw_payload='{"scores":{"factuality":5,"logical_consistency":5,"citation_quality":5,"physical_validity":5,"actionable_clarity":4,},"overall_score":4.8,"passed":true,"hard_gate_passed":true,}',
                tags=["json_recovery"],
            ),
        ]
    )
    return cases


def build_lammps_recovery_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "lammps_recovery.global_timeout_partial_report",
            "suite": "lammps_recovery",
            "mode": "deterministic_orchestration",
            "scenario": "global_timeout_partial_report",
            "expected": {
                "termination_reason": "global_timeout",
                "resume_supported": True,
                "scientific_result_available": False,
                "checkpoint_count_min": 2,
            },
            "tags": ["lammps", "timeout", "checkpoint", "partial_report"],
        },
        {
            "case_id": "lammps_recovery.preflight_level2_replan_reuse",
            "suite": "lammps_recovery",
            "mode": "deterministic_runtime",
            "scenario": "preflight_level2_replan_reuse",
            "expected": {
                "success": True,
                "replan_executed": True,
                "final_plan_version": 2,
                "invalidated_nodes": ["red_pre_execution_review"],
                "reused_nodes_contains": ["constraint_extract", "preflight_merge"],
            },
            "tags": ["lammps", "replan", "checkpoint_reuse"],
        },
        {
            "case_id": "lammps_recovery.worker_crash_failed_event",
            "suite": "lammps_recovery",
            "mode": "deterministic_job_queue",
            "scenario": "worker_crash_failed_event",
            "expected": {
                "job_status": "failed",
                "run_id": "run-crash",
                "event_types": ["run_started", "run_error"],
            },
            "tags": ["job_queue", "crash", "run_error"],
        },
        {
            "case_id": "lammps_recovery.running_cancel_not_overwritten",
            "suite": "lammps_recovery",
            "mode": "deterministic_job_queue",
            "scenario": "running_cancel_not_overwritten",
            "expected": {
                "job_status": "cancelled",
                "run_id": "run-cancel-running",
                "result_run_id": "",
                "event_types": ["run_started"],
            },
            "tags": ["job_queue", "cancel", "idempotent"],
        },
    ]


def build_materials_multihop_cases() -> list[dict[str, Any]]:
    frozen_multihop_generation = {
        "kind": "materials_multihop_frozen",
        "created_at": "2026-07-07",
        "frozen_before_first_evaluation": True,
        "freeze_reason": "Phase 6 MaterialsMultiHop multi-evidence-chain regression gate",
    }
    lost_atoms_hops = [
        {
            "hop_id": "user_constraints",
            "evidence_id": "user:cu-800k-4000",
            "authority": "user_constraint",
            "description": "User locked Cu, 800 K and 4000 steps.",
            "min_authority_rank": 100,
        },
        {
            "hop_id": "registry_potential",
            "evidence_id": "registry:cu-eam-supported",
            "authority": "registry",
            "description": "Registry confirms Cu/EAM is supported.",
            "min_authority_rank": 90,
        },
        {
            "hop_id": "rag_lost_atoms",
            "evidence_id": "rag:lost-atoms-timestep",
            "authority": "rag",
            "description": "RAG evidence links lost atoms during heating to aggressive timestep/temperature ramp.",
            "min_authority_rank": 60,
        },
        {
            "hop_id": "input_script",
            "evidence_id": "script:timestep-0.005",
            "authority": "input_script",
            "description": "in.lammps shows timestep 0.005 ps.",
            "min_authority_rank": 80,
        },
        {
            "hop_id": "run_log",
            "evidence_id": "log:lost-atoms",
            "authority": "run_log",
            "description": "run.log contains lost atoms fatal error.",
            "min_authority_rank": 80,
        },
        {
            "hop_id": "quality_gate",
            "evidence_id": "quality:failed-lost-atoms",
            "authority": "quality_gate",
            "description": "Physical quality gate marks the run invalid.",
            "min_authority_rank": 85,
        },
        {
            "hop_id": "blue_repair",
            "evidence_id": "blue:reduce-timestep",
            "authority": "blue_patch",
            "description": "Blue repair suggests reducing timestep without changing locked Cu/800 K/4000 steps.",
            "min_authority_rank": 70,
        },
    ]
    unsupported_hops = [
        {
            "hop_id": "user_constraints",
            "evidence_id": "user:cu-meam-request",
            "authority": "user_constraint",
            "description": "User requests Cu with MEAM potential.",
            "min_authority_rank": 100,
        },
        {
            "hop_id": "registry_lookup",
            "evidence_id": "registry:cu-meam-unsupported",
            "authority": "registry",
            "description": "Registry only exposes Cu EAM/LJ in the local fixture.",
            "min_authority_rank": 90,
        },
        {
            "hop_id": "rag_registry_policy",
            "evidence_id": "rag:potential-family-must-match-registry",
            "authority": "rag",
            "description": "RAG guidance says generated input must use an available potential family.",
            "min_authority_rank": 60,
        },
        {
            "hop_id": "preflight",
            "evidence_id": "preflight:unsupported-potential",
            "authority": "quality_gate",
            "description": "Preflight blocks execution before codegen.",
            "min_authority_rank": 85,
        },
        {
            "hop_id": "final_response",
            "evidence_id": "final:request-user-switch-potential",
            "authority": "final_answer",
            "description": "Final answer asks whether to switch to supported EAM/LJ.",
            "min_authority_rank": 50,
        },
    ]
    synthetic_hops = [
        {
            "hop_id": "user_constraints",
            "evidence_id": "user:ni-900k-2000",
            "authority": "user_constraint",
            "description": "User requests Ni at 900 K for 2000 steps.",
            "min_authority_rank": 100,
        },
        {
            "hop_id": "request_json",
            "evidence_id": "request:ni-900k-2000",
            "authority": "input_script",
            "description": "request.json preserves Ni/900 K/2000 steps.",
            "min_authority_rank": 80,
        },
        {
            "hop_id": "thermo_metadata",
            "evidence_id": "thermo:synthetic-metadata",
            "authority": "run_log",
            "description": "thermo metadata marks the data as synthetic fixture output.",
            "min_authority_rank": 80,
        },
        {
            "hop_id": "quality_gate",
            "evidence_id": "quality:synthetic-blocked",
            "authority": "quality_gate",
            "description": "Quality gate prevents treating synthetic thermo as scientific result.",
            "min_authority_rank": 85,
        },
        {
            "hop_id": "final_response",
            "evidence_id": "answer:synthetic-not-real",
            "authority": "final_answer",
            "description": "Final answer explicitly says this is not a real LAMMPS execution.",
            "min_authority_rank": 50,
        },
    ]
    return [
        {
            "case_id": "materials_multihop.lammps_lost_atoms_repair_chain",
            "suite": "materials_multihop",
            "mode": "deterministic_fixture",
            "prompt": "为什么这次 Cu 800K 加热模拟失败？请基于证据说明并给出安全修复。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "request": {"material": "Cu", "temperature": 800, "steps": 4000, "potential_family": "eam"},
                "required_steps": [
                    "lammps_request_interpreter",
                    "lammps_registry_lookup",
                    "materials_rag_search",
                    "lammps_input_codegen",
                    "lammps_execute",
                    "lammps_quality_gate",
                    "lammps_result_review",
                    "blue_repair_policy",
                ],
                "required_artifacts": ["request.json", "in.lammps", "run.log", "quality_report.json", "repair_patch.json"],
                "required_evidence": [hop["evidence_id"] for hop in lost_atoms_hops],
                "required_hops": lost_atoms_hops,
                "expected_conclusion": "failed_needs_repair",
                "forbidden_claims": ["Do not claim the failed LAMMPS run succeeded."],
            },
            "observation": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "locked_constraints": {"material": "Cu", "temperature": 800, "steps": 4000, "potential_family": "eam"},
                "completed_tools": [
                    "lammps_request_interpreter",
                    "lammps_registry_lookup",
                    "materials_rag_search",
                    "lammps_input_codegen",
                    "lammps_execute",
                    "lammps_quality_gate",
                    "lammps_result_review",
                    "blue_repair_policy",
                ],
                "artifacts": ["request.json", "in.lammps", "run.log", "quality_report.json", "repair_patch.json"],
                "provenance": {"actual": "real", "claimed": "real"},
                "execution": {"success": False},
                "physical_gate": {"passed": False},
                "final_conclusion": "failed_needs_repair",
                "final_response": "该 LAMMPS run 未成功：run.log 报告 lost atoms，quality gate 已阻断结果。安全修复是降低 timestep 或放缓升温，不修改 Cu、800 K、4000 steps 这些锁定约束。",
                "claims": [
                    {"claim_id": "lost_atoms", "text": "run.log reports lost atoms", "status": "supported", "bridge": True},
                    {"claim_id": "quality_failed", "text": "quality gate blocks the result", "status": "supported", "bridge": True},
                    {"claim_id": "repair_safe", "text": "repair preserves locked constraints", "status": "supported", "bridge": True},
                ],
                "citations": [
                    {"evidence_id": "user:cu-800k-4000", "authority": "user_constraint", "supports": True, "exists": True},
                    {"evidence_id": "registry:cu-eam-supported", "authority": "registry", "supports": True, "exists": True},
                    {"evidence_id": "rag:lost-atoms-timestep", "authority": "rag", "supports": True, "exists": True},
                    {"evidence_id": "script:timestep-0.005", "authority": "input_script", "supports": True, "exists": True},
                    {"evidence_id": "log:lost-atoms", "authority": "run_log", "supports": True, "exists": True},
                    {"evidence_id": "quality:failed-lost-atoms", "authority": "quality_gate", "supports": True, "exists": True},
                    {"evidence_id": "blue:reduce-timestep", "authority": "blue_patch", "supports": True, "exists": True},
                ],
                "required_hops": [{**hop, "completed": True} for hop in lost_atoms_hops],
            },
            "tags": ["lammps", "multihop", "lost_atoms", "repair"],
            "generation": frozen_multihop_generation,
        },
        {
            "case_id": "materials_multihop.registry_blocks_unsupported_potential",
            "suite": "materials_multihop",
            "mode": "deterministic_fixture",
            "prompt": "我想用 Cu 的 MEAM 势函数跑 800K 平衡，为什么系统没有继续执行？",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "request": {"material": "Cu", "temperature": 800, "steps": 2000, "potential_family": "meam"},
                "required_steps": ["lammps_request_interpreter", "lammps_registry_lookup", "materials_rag_search", "lammps_preflight"],
                "required_artifacts": ["request.json", "preflight_report.json"],
                "required_evidence": [hop["evidence_id"] for hop in unsupported_hops],
                "required_hops": unsupported_hops,
                "expected_conclusion": "unsupported_capability",
                "forbidden_claims": ["Do not fabricate a Cu MEAM potential file."],
            },
            "observation": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "locked_constraints": {"material": "Cu", "temperature": 800, "steps": 2000, "potential_family": "meam"},
                "completed_tools": ["lammps_request_interpreter", "lammps_registry_lookup", "materials_rag_search", "lammps_preflight"],
                "artifacts": ["request.json", "preflight_report.json"],
                "provenance": {"actual": "dry_run", "claimed": "dry_run"},
                "execution": {"success": False},
                "final_conclusion": "unsupported_capability",
                "final_response": "系统没有继续执行，因为本地 registry 没有 Cu/MEAM 势函数；我不能虚构势文件。可以请你确认是否切换到 registry 支持的 Cu/EAM 或 LJ。",
                "claims": [
                    {"claim_id": "registry_blocks", "text": "registry blocks unsupported Cu MEAM", "status": "supported", "bridge": True},
                    {"claim_id": "no_fake_file", "text": "the agent must not fabricate a potential file", "status": "supported", "bridge": True},
                ],
                "citations": [
                    {"evidence_id": "user:cu-meam-request", "authority": "user_constraint", "supports": True, "exists": True},
                    {"evidence_id": "registry:cu-meam-unsupported", "authority": "registry", "supports": True, "exists": True},
                    {"evidence_id": "rag:potential-family-must-match-registry", "authority": "rag", "supports": True, "exists": True},
                    {"evidence_id": "preflight:unsupported-potential", "authority": "quality_gate", "supports": True, "exists": True},
                    {"evidence_id": "final:request-user-switch-potential", "authority": "final_answer", "supports": True, "exists": True},
                ],
                "required_hops": [{**hop, "completed": True} for hop in unsupported_hops],
            },
            "tags": ["lammps", "multihop", "registry", "unsupported"],
            "generation": frozen_multihop_generation,
        },
        {
            "case_id": "materials_multihop.synthetic_thermo_not_real_execution",
            "suite": "materials_multihop",
            "mode": "deterministic_fixture",
            "prompt": "这次 Ni 900K 模拟结果可信吗？请说明证据。",
            "expected": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "request": {"material": "Ni", "temperature": 900, "steps": 2000, "potential_family": "eam"},
                "required_steps": ["lammps_request_interpreter", "lammps_postprocess", "lammps_quality_gate", "lammps_result_review"],
                "required_artifacts": ["request.json", "thermo.csv", "thermo_metadata.json", "quality_report.json"],
                "required_evidence": [hop["evidence_id"] for hop in synthetic_hops],
                "required_hops": synthetic_hops,
                "expected_conclusion": "synthetic_not_real",
                "forbidden_claims": ["Do not describe synthetic thermo as real LAMMPS execution."],
            },
            "observation": {
                "route_name": "lammps.generate",
                "compute_domain": "lammps",
                "locked_constraints": {"material": "Ni", "temperature": 900, "steps": 2000, "potential_family": "eam"},
                "completed_tools": ["lammps_request_interpreter", "lammps_postprocess", "lammps_quality_gate", "lammps_result_review"],
                "artifacts": ["request.json", "thermo.csv", "thermo_metadata.json", "quality_report.json"],
                "provenance": {"actual": "synthetic", "claimed": "synthetic"},
                "physical_gate": {"passed": False},
                "final_conclusion": "synthetic_not_real",
                "final_response": "这个结果不能作为真实 LAMMPS 科学结论：thermo_metadata 标记为 synthetic，quality gate 已阻断。它只能用于界面/流程回归。",
                "claims": [
                    {"claim_id": "synthetic_metadata", "text": "thermo metadata marks synthetic data", "status": "supported", "bridge": True},
                    {"claim_id": "not_scientific", "text": "quality gate blocks scientific conclusion", "status": "supported", "bridge": True},
                ],
                "citations": [
                    {"evidence_id": "user:ni-900k-2000", "authority": "user_constraint", "supports": True, "exists": True},
                    {"evidence_id": "request:ni-900k-2000", "authority": "input_script", "supports": True, "exists": True},
                    {"evidence_id": "thermo:synthetic-metadata", "authority": "run_log", "supports": True, "exists": True},
                    {"evidence_id": "quality:synthetic-blocked", "authority": "quality_gate", "supports": True, "exists": True},
                    {"evidence_id": "answer:synthetic-not-real", "authority": "final_answer", "supports": True, "exists": True},
                ],
                "required_hops": [{**hop, "completed": True} for hop in synthetic_hops],
            },
            "tags": ["lammps", "multihop", "synthetic", "provenance"],
            "generation": frozen_multihop_generation,
        },
    ]


def build_manifest(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "benchmark_version": "2026-06-23-v5",
        "dataset_count": len(datasets),
        "cases_total": sum(len(rows) for rows in datasets.values()),
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "supported_lammps_materials": sorted(get_lammps_registry_payload()["materials"].keys()),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_existing_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_all_datasets() -> dict[str, list[dict[str, Any]]]:
    datasets = {
        "routing_cases": build_routing_cases(),
        "phase_parsing_cases": build_phase_parsing_cases(),
        "lammps_parsing_cases": build_lammps_parsing_cases(),
        "phase_execution_cases": build_phase_execution_cases(),
        "lammps_contract_cases": build_lammps_contract_cases(),
        "lammps_e2e_cases": build_lammps_e2e_cases(),
        "lammps_quality_cases": build_lammps_quality_cases(),
        "lammps_red_blue_cases": build_lammps_red_blue_cases(),
        "review_json_fallback_cases": build_review_json_fallback_cases(),
        "orchestration_cases": build_orchestration_cases(),
        "judge_calibration_cases": build_judge_calibration_cases(),
        "lammps_recovery_cases": build_lammps_recovery_cases(),
        "recognition_cases": build_recognition_cases(),
        "external_recognition_cases": build_external_recognition_cases(),
        "memory_followup_cases": [case for case in build_memory_followup_cases() if case["suite"] == "memory_followup"],
        "memory_retrieval_cases": build_memory_retrieval_cases(),
        "shared_memory_cases": build_shared_memory_cases(),
        "memory_conflict_cases": build_memory_conflict_cases(),
        "context_compression_cases": build_context_compression_cases(),
        "materials_multihop_cases": build_materials_multihop_cases(),
        "mcp_cases": build_mcp_cases(),
    }
    rag_blind_cases = read_existing_jsonl(DATASET_DIR / "rag_blind_cases.jsonl")
    if rag_blind_cases:
        datasets["rag_blind_cases"] = rag_blind_cases
    return datasets


def main() -> int:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    datasets = build_all_datasets()
    for name, rows in datasets.items():
        write_jsonl(DATASET_DIR / f"{name}.jsonl", rows)
    manifest = build_manifest(datasets)
    (DATASET_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
