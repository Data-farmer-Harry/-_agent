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


def _slug(text: str) -> str:
    return text.lower().replace(" ", "_").replace("/", "_").replace("-", "_")


def build_routing_cases() -> list[dict[str, Any]]:
    return [
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


def build_phase_parsing_cases() -> list[dict[str, Any]]:
    return [
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
            "asset_path": str(ASSET_DIR / "al_ni_pmc_phase_diagram.jpg"),
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
            "asset_path": str(ASSET_DIR / "al_cu_pmc_phase_diagram.jpg"),
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
            "asset_path": str(ASSET_DIR / "pb_sn_nist_phase_diagram.jpg"),
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
            "asset_path": str(ASSET_DIR / "fe_ni_pmc_phase_diagram.jpg"),
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


def build_manifest(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "benchmark_version": "2026-04-09-v2",
        "dataset_count": len(datasets),
        "cases_total": sum(len(rows) for rows in datasets.values()),
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "supported_lammps_materials": sorted(get_lammps_registry_payload()["materials"].keys()),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_all_datasets() -> dict[str, list[dict[str, Any]]]:
    return {
        "routing_cases": build_routing_cases(),
        "phase_parsing_cases": build_phase_parsing_cases(),
        "lammps_parsing_cases": build_lammps_parsing_cases(),
        "phase_execution_cases": build_phase_execution_cases(),
        "lammps_contract_cases": build_lammps_contract_cases(),
        "lammps_e2e_cases": build_lammps_e2e_cases(),
        "recognition_cases": build_recognition_cases(),
        "external_recognition_cases": build_external_recognition_cases(),
        "memory_followup_cases": [case for case in build_memory_followup_cases() if case["suite"] == "memory_followup"],
        "memory_retrieval_cases": build_memory_retrieval_cases(),
        "mcp_cases": build_mcp_cases(),
    }


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
