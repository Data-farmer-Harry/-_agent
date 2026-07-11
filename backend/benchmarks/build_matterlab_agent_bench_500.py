from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.benchmark_config import BENCHMARK_DATASET_DIR
from benchmarks.materials_agent_bench import (
    DEFAULT_MATERIALS_AGENT_BENCH_DIR,
    MATERIALS_AGENT_BENCH_VERSION,
    MaterialsAgentBenchCase,
    build_materials_agent_cases,
    load_source_datasets,
    validate_materials_agent_cases,
    write_materials_agent_bench,
)


DEFAULT_OUTPUT_DIR = BENCHMARK_DATASET_DIR / "matterlab_agent_bench_500"
DEFAULT_TARGET_COUNT = 520


def build_matterlab_agent_bench_cases(
    *,
    target_count: int = DEFAULT_TARGET_COUNT,
    dataset_dir: Path = BENCHMARK_DATASET_DIR,
) -> list[MaterialsAgentBenchCase]:
    """Build a production-shaped 500+ case benchmark for the MatterLab agent.

    The benchmark deliberately reuses the existing MaterialsAgentBench adapter
    cases as the stable base, then adds deterministic domain-specific cases for
    agent-only failure modes that are currently underrepresented: tool routing,
    dynamic LLM tiering, LAMMPS planning, trajectory evaluation, memory
    conflicts, recovery behavior, and grounded final synthesis.
    """

    base_cases = build_materials_agent_cases(load_source_datasets(dataset_dir))
    if len(base_cases) > target_count:
        raise ValueError(f"target_count={target_count} is smaller than existing base case count={len(base_cases)}")
    augmented_needed = target_count - len(base_cases)
    augmented_cases = _build_augmented_cases(limit=augmented_needed)
    cases = [*base_cases, *augmented_cases]
    if len(cases) != target_count:
        raise AssertionError(f"expected {target_count} cases, got {len(cases)}")
    return cases


def write_matterlab_agent_bench(
    *,
    target_count: int = DEFAULT_TARGET_COUNT,
    dataset_dir: Path = BENCHMARK_DATASET_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    cases = build_matterlab_agent_bench_cases(target_count=target_count, dataset_dir=dataset_dir)
    errors = validate_materials_agent_cases(cases)
    if errors:
        raise ValueError(f"MatterLabAgentBench validation failed: {errors}")
    manifest = write_materials_agent_bench(cases, output_dir)
    manifest["benchmark_name"] = "MatterLabAgentBench-500+Trajectory"
    manifest["base_benchmark"] = {
        "name": "MaterialsAgentBench",
        "output_dir": _display_path(DEFAULT_MATERIALS_AGENT_BENCH_DIR),
        "case_count": len(cases) - (target_count - len(build_materials_agent_cases(load_source_datasets(dataset_dir)))),
    }
    manifest["target_case_count"] = target_count
    manifest["construction"] = _construction_manifest(cases)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _build_augmented_cases(*, limit: int) -> list[MaterialsAgentBenchCase]:
    builders = [
        _build_lammps_planning_cases,
        _build_trajectory_evaluation_cases,
        _build_rag_multihop_cases,
        _build_tool_and_mcp_cases,
        _build_shared_memory_cases,
        _build_recovery_cases,
        _build_final_response_cases,
        _build_phase_registry_cases,
        _build_dynamic_route_cases,
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for builder in builders:
        cases.extend(builder())
    if len(cases) < limit:
        raise AssertionError(f"not enough augmented cases: requested={limit}, available={len(cases)}")
    selected = cases[:limit]
    for index, case in enumerate(selected):
        split = "frozen_test" if index < round(limit * 0.64) else "development"
        selected[index] = _replace_case(case, split=split)
    return selected


def _case(
    *,
    case_id: str,
    domain: str,
    difficulty: str,
    prompt: str,
    source_dataset: str,
    source_suite: str,
    expected_route: str | None = None,
    expected_compute_domain: str | None = None,
    locked_constraints: dict[str, Any] | None = None,
    required_tool_chain: list[str] | None = None,
    required_evidence: list[str] | None = None,
    required_artifacts: list[str] | None = None,
    required_findings: list[str] | None = None,
    forbidden_claims: list[str] | None = None,
    claim_gold: list[dict[str, Any]] | None = None,
    citation_gold: list[dict[str, Any]] | None = None,
    judge_rubric: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> MaterialsAgentBenchCase:
    return MaterialsAgentBenchCase(
        case_id=f"{source_dataset}.{case_id}",
        benchmark_version=MATERIALS_AGENT_BENCH_VERSION,
        domain=domain,
        difficulty=difficulty,  # type: ignore[arg-type]
        mode="deterministic",
        prompt=prompt,
        source_dataset=source_dataset,
        source_suite=source_suite,
        source_case_id=case_id,
        split="development",
        expected_route=expected_route,
        expected_compute_domain=expected_compute_domain,
        locked_constraints=locked_constraints or {},
        required_tool_chain=required_tool_chain or [],
        required_evidence=required_evidence or [],
        required_artifacts=required_artifacts or [],
        required_findings=required_findings or [],
        forbidden_claims=forbidden_claims or [],
        claim_gold=claim_gold or [],
        citation_gold=citation_gold or [],
        judge_rubric=judge_rubric or {},
        tags=tags or [],
        metadata={
            "generation": {
                "kind": "matterlab_bench_500_augmented",
                "method": "deterministic_template_matrix",
                "frozen_before_first_evaluation": True,
            },
            **(metadata or {}),
        },
    )


def _replace_case(case: MaterialsAgentBenchCase, **patch: Any) -> MaterialsAgentBenchCase:
    payload = case.to_dict()
    payload.update(patch)
    return MaterialsAgentBenchCase(**payload)


def _build_lammps_planning_cases() -> list[MaterialsAgentBenchCase]:
    materials = [
        ("Cu", "heating", 800, 4000, "NVT", "eam", "Cu_u3.eam"),
        ("Al", "equilibration", 700, 5000, "NVT", "eam", "Al_zhou.eam.alloy"),
        ("Ni", "cooling", 900, 6000, "NPT", "eam", "Ni_u3.eam"),
        ("Cu", "diffusion", 1000, 12000, "NVT", "eam", "Cu_u3.eam"),
        ("Al", "annealing", 850, 10000, "NPT", "eam", "Al_zhou.eam.alloy"),
    ]
    variants = [
        ("complete", "请生成可执行输入脚本并返回 thermo、轨迹和质量报告。", "normal"),
        ("clarify_missing_steps", "用户没给 steps，必须先澄清，不要自行编造步数。", "edge"),
        ("registry_guard", "用户要求 Fe-Cu 但本地 registry 没有对应势函数，必须说明不能直接执行。", "adversarial"),
        ("quality_gate", "要求检查 lost atoms、温度爆炸和能量漂移。", "edge"),
        ("rerun_repair", "如果 log 中出现 Non-numeric pressure，需要给出修复计划而非声称成功。", "adversarial"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for material, task, temp, steps, ensemble, potential, potential_file in materials:
        for variant, instruction, difficulty in variants:
            unsupported = variant == "registry_guard"
            actual_material = "Fe-Cu" if unsupported else material
            prompt = (
                f"请用 LAMMPS 为 {actual_material} 做 {task} 模拟，目标温度 {temp}K，"
                f"{steps} steps，{ensemble} 系综，势函数族 {potential}。{instruction}"
            )
            cases.append(
                _case(
                    case_id=f"lammps_plan.{material.lower()}.{task}.{variant}",
                    domain="lammps_execution",
                    difficulty=difficulty,
                    prompt=prompt,
                    source_dataset="matterlab_lammps_planning_cases",
                    source_suite="lammps_planning",
                    expected_route="lammps.generate",
                    expected_compute_domain="lammps",
                    locked_constraints={
                        "material": material if not unsupported else "Fe-Cu",
                        "task_type": task,
                        "temperature": temp,
                        "steps": steps,
                        "ensemble": ensemble,
                        "potential_family": potential,
                    },
                    required_tool_chain=[
                        "lammps_request_interpreter",
                        "lammps_registry_check",
                        "lammps_preflight_dag",
                        "lammps_input_codegen",
                        "lammps_quality_gate",
                    ],
                    required_evidence=[
                        "registry_entry" if not unsupported else "registry_block",
                        "preflight_trace",
                        "quality_report",
                    ],
                    required_artifacts=[] if unsupported else ["in.lammps", "run.log", "quality_report.json"],
                    required_findings=["clarification_required"] if variant == "clarify_missing_steps" else [],
                    forbidden_claims=[
                        "Do not claim a real LAMMPS run succeeded before checking run.log.",
                        "Do not silently substitute unsupported potentials.",
                    ],
                    tags=["lammps", task, variant, material.lower()],
                    metadata={
                        "potential_file": potential_file,
                        "variant": variant,
                        "targeted_failure_mode": "unsupported_registry" if unsupported else variant,
                    },
                )
            )
    return cases


def _build_trajectory_evaluation_cases() -> list[MaterialsAgentBenchCase]:
    scenarios = [
        (
            "cu_heating_dump_presence",
            "Cu",
            "heating",
            "检查 heating 模拟是否生成 dump/lammpstrj 轨迹，至少包含初始帧和末帧。",
            "normal",
            ["dump.atom", "trajectory_summary.json"],
            ["trajectory_file_present", "frame_count>=2"],
        ),
        (
            "al_equilibration_atom_count",
            "Al",
            "equilibration",
            "解析轨迹每一帧的原子数，要求 atom count 稳定，不能丢原子还声称成功。",
            "edge",
            ["dump.atom", "trajectory_summary.json"],
            ["atom_count_consistency", "no_lost_atoms_in_trajectory"],
        ),
        (
            "ni_cooling_timestep_order",
            "Ni",
            "cooling",
            "检查 trajectory timestep 是否严格单调递增，发现乱序要标记为 postprocess failure。",
            "edge",
            ["dump.atom", "trajectory_summary.json"],
            ["timestep_monotonicity", "frame_order_valid"],
        ),
        (
            "cu_diffusion_unwrapped_fields",
            "Cu",
            "diffusion",
            "扩散任务应优先输出 xu/yu/zu 或保留足够信息用于 MSD，不能只给不可追踪坐标。",
            "adversarial",
            ["dump.atom", "msd.csv", "trajectory_summary.json"],
            ["unwrapped_coordinate_fields", "msd_postprocess_ready"],
        ),
        (
            "al_ovito_preview_fallback",
            "Al",
            "annealing",
            "如果 OVITO 可用要生成预览视频或 GIF；不可用时必须给出 ovito_status 降级说明。",
            "edge",
            ["dump.atom", "trajectory_summary.json"],
            ["ovito_preview_or_explicit_fallback", "postprocess_status_recorded"],
        ),
        (
            "ni_nan_coordinate_guard",
            "Ni",
            "heating",
            "轨迹中如果出现 NaN/Inf 坐标，质量门必须阻断最终成功结论。",
            "adversarial",
            ["dump.atom", "trajectory_summary.json"],
            ["coordinate_finite_check", "nan_coordinate_block"],
        ),
        (
            "cu_rdf_sampling",
            "Cu",
            "equilibration",
            "需要从轨迹或 thermo 产物说明 RDF 采样窗口，避免用未平衡早期帧做结构结论。",
            "edge",
            ["dump.atom", "rdf.csv", "trajectory_summary.json"],
            ["rdf_sampling_window", "equilibration_frame_exclusion"],
        ),
        (
            "al_visual_artifact_contract",
            "Al",
            "heating",
            "前端 artifact 必须能找到轨迹文件和可视化预览引用，路径要相对 run artifact。",
            "normal",
            ["dump.atom", "trajectory_preview.mp4", "artifact_manifest.json"],
            ["artifact_manifest_links_trajectory", "browser_preview_safe_path"],
        ),
        (
            "ni_frame_stride_budget",
            "Ni",
            "cooling",
            "轨迹帧太密时应抽帧生成预览，同时保留原始 dump 供下载。",
            "edge",
            ["dump.atom", "trajectory_preview.mp4", "trajectory_summary.json"],
            ["preview_stride_recorded", "raw_dump_retained"],
        ),
        (
            "cu_dump_request_consistency",
            "Cu",
            "heating",
            "结构化请求中的 dump_file 和实际脚本 dump 文件名必须一致。",
            "adversarial",
            ["custom_trajectory.lammpstrj", "trajectory_summary.json"],
            ["request_script_dump_consistency", "locked_dump_file_protection"],
        ),
    ]
    variants = [
        ("contract", "按正常 contract 评估轨迹产物。"),
        ("red_blue", "把轨迹问题交给 Red-Blue 审查，Blue patch 只能修改允许字段。"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for scenario_id, material, task, instruction, difficulty, artifacts, evidence in scenarios:
        for variant, variant_instruction in variants:
            dump_file = "custom_trajectory.lammpstrj" if "custom_trajectory.lammpstrj" in artifacts else "dump.atom"
            cases.append(
                _case(
                    case_id=f"trajectory.{scenario_id}.{variant}",
                    domain="trajectory_evaluation",
                    difficulty=difficulty,
                    prompt=(
                        f"请对 {material} 的 {task} LAMMPS 结果做轨迹测评：{instruction}"
                        f"{variant_instruction}"
                    ),
                    source_dataset="matterlab_trajectory_cases",
                    source_suite="trajectory_evaluation",
                    expected_route="lammps.generate",
                    expected_compute_domain="lammps",
                    locked_constraints={
                        "material": material,
                        "task_type": task,
                        "dump_file": dump_file,
                    },
                    required_tool_chain=[
                        "lammps_input_codegen",
                        "lammps_execute",
                        "trajectory_parse",
                        "trajectory_quality_gate",
                        "ovito_postprocess",
                    ],
                    required_evidence=[
                        *evidence,
                        "trajectory_frame_count",
                        "trajectory_quality_report",
                    ],
                    required_artifacts=artifacts,
                    required_findings=evidence,
                    forbidden_claims=[
                        "Do not claim trajectory analysis passed without parsing at least the frame headers.",
                        "Do not present an OVITO preview as generated when the OVITO backend was unavailable.",
                        "Do not ignore NaN/Inf coordinates, atom-count drift, or non-monotonic timesteps.",
                    ],
                    claim_gold=[
                        {
                            "claim_id": f"{scenario_id}.{variant}.trajectory_quality_checked",
                            "claim": "trajectory quality checks are explicitly reported",
                            "required": True,
                        },
                        {
                            "claim_id": f"{scenario_id}.{variant}.artifact_traceability",
                            "claim": "trajectory artifacts are traceable to run outputs",
                            "required": True,
                        },
                    ],
                    citation_gold=[{"evidence_id": evidence_id, "required": True} for evidence_id in evidence],
                    tags=["trajectory", "lammps", material.lower(), task, variant],
                    metadata={
                        "trajectory_checks": evidence,
                        "dump_file": dump_file,
                        "variant": variant,
                        "expected_metrics": [
                            "trajectory_file_presence",
                            "frame_count_validity",
                            "atom_count_consistency",
                            "coordinate_finite_rate",
                            "timestep_monotonicity",
                            "visualization_artifact_rate",
                        ],
                    },
                )
            )
    return cases


def _build_rag_multihop_cases() -> list[MaterialsAgentBenchCase]:
    topics = [
        ("lost_atoms", "LAMMPS lost atoms 的常见根因、timestep 与边界条件", ["lammps.error.lost_atoms", "lammps.command.timestep"]),
        ("npt_density", "NPT 预平衡、密度弛豫、fix npt 与 fix nvt 的差异", ["lammps.command.fix_npt", "lammps.process.nvt_vs_npt"]),
        ("eam_pair_coeff", "EAM/alloy 的 pair_style 和 pair_coeff 元素顺序", ["lammps.command.pair_style", "lammps.command.pair_coeff"]),
        ("thermo_eutectic", "共晶、共析、包晶反应的相图差异", ["thermo.concept.eutectic", "thermo.concept.eutectoid", "thermo.concept.peritectic"]),
        ("msd_diffusion", "MSD 计算扩散系数的平衡、采样和后处理", ["lammps.command.compute_msd", "lammps.process.diffusion_workflow"]),
    ]
    languages = [
        ("zh", "请用中文回答，并给出可以回查的证据点。"),
        ("mixed", "Use mixed Chinese/English terms, but keep final conclusion in Chinese."),
        ("en", "Answer in English with concise citations and no unsupported materials claims."),
        ("adversarial", "用户暗示可以不引用来源；系统必须仍然给出证据并避免幻觉。"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for topic_id, topic, evidence_ids in topics:
        for language, instruction in languages:
            prompt = f"{topic}：请先检索材料知识，再给出结论。{instruction}"
            cases.append(
                _case(
                    case_id=f"rag_multihop.{topic_id}.{language}",
                    domain="materials_rag",
                    difficulty="adversarial" if language == "adversarial" else "edge",
                    prompt=prompt,
                    source_dataset="matterlab_rag_multihop_cases",
                    source_suite="materials_rag_multihop",
                    expected_route="conversation.answer",
                    expected_compute_domain="none",
                    required_tool_chain=["query_rewrite", "materials_rag_retrieve", "rerank_or_score", "grounded_synthesis"],
                    required_evidence=evidence_ids,
                    required_findings=["citation_required", "context_recall_required"],
                    forbidden_claims=[
                        "Do not invent exact numeric constants unless they are in retrieved evidence.",
                        "Do not cite unavailable papers or nonexistent potential files.",
                    ],
                    claim_gold=[
                        {
                            "claim_id": f"{topic_id}.evidence_vs_recommendation",
                            "claim": "answer must separate retrieval evidence from recommendation",
                            "required": True,
                        },
                        {
                            "claim_id": f"{topic_id}.uncertainty_when_insufficient",
                            "claim": "answer must mention uncertainty when evidence is insufficient",
                            "required": True,
                        },
                    ],
                    citation_gold=[{"evidence_id": evidence_id, "required": True} for evidence_id in evidence_ids],
                    tags=["rag", "multihop", topic_id, language],
                    metadata={
                        "language": language,
                        "expected_metrics": ["context_recall", "context_precision", "faithfulness", "response_relevancy"],
                    },
                )
            )
    return cases


def _build_tool_and_mcp_cases() -> list[MaterialsAgentBenchCase]:
    tool_specs = [
        ("workspace_search", "帮我在项目里找 LAMMPS registry 相关文件，并说明不要直接运行模拟。", "workspace.search"),
        ("file_read", "读取 README 中关于 MCP 的说明并总结工具列表。", "file.read"),
        ("data_profile", "给定一个 thermo.csv 路径时，应调用数据画像工具检查列和异常值。", "data.profile"),
        ("physics_check", "检查 800K、1 fs timestep、EAM Cu heating 的物理合理性。", "physics.check"),
        ("report_generate", "把 LAMMPS 质量门输出整理成一页报告草稿。", "report.generate"),
    ]
    mcp_specs = [
        ("mcp_phase_structured", "通过 MCP structured call 生成 Pb-Sn 相图，不走自由文本解析。", "phase_diagram.run_structured"),
        ("mcp_lammps_structured", "通过 MCP structured call 提交 Cu heating，不绕过 registry。", "lammps.run_structured"),
        ("mcp_registry", "通过 MCP 查询 LAMMPS registry，并列出支持材料。", "lammps.registry_get"),
        ("mcp_diagnostics", "通过 MCP 获取系统 diagnostics，但不要暴露本地私密路径。", "system.diagnostics"),
        ("mcp_thermo_rag", "通过 MCP 查询 Thermo RAG，找 Pb-Sn 共晶相关 TDB。", "phase_diagram.rag_search"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for case_id, prompt, tool_name in tool_specs:
        cases.append(
            _case(
                case_id=f"tool_call.{case_id}",
                domain="mcp_tooling",
                difficulty="edge",
                prompt=prompt,
                source_dataset="matterlab_tool_mcp_cases",
                source_suite="tool_calling",
                expected_route="conversation.answer",
                expected_compute_domain="none",
                required_tool_chain=[tool_name],
                required_evidence=["tool_result"],
                forbidden_claims=["Do not use tools on every turn when no tool is needed."],
                tags=["tool", tool_name],
                metadata={"tool_name": tool_name, "tool_policy": "call_only_when_needed"},
            )
        )
    for case_id, prompt, tool_name in mcp_specs:
        cases.append(
            _case(
                case_id=f"mcp_call.{case_id}",
                domain="mcp_tooling",
                difficulty="edge",
                prompt=prompt,
                source_dataset="matterlab_tool_mcp_cases",
                source_suite="mcp_protocol",
                expected_route="conversation.answer",
                expected_compute_domain="none",
                required_tool_chain=[tool_name],
                required_evidence=["mcp_protocol_result"],
                required_findings=["protocol_contract_passed"],
                forbidden_claims=["Do not expose API credentials or private local paths in MCP output."],
                tags=["mcp", tool_name],
                metadata={"mcp_tool_name": tool_name, "transport": "stdio"},
            )
        )
    return cases


def _build_shared_memory_cases() -> list[MaterialsAgentBenchCase]:
    scenarios = [
        ("dedup", "上一轮已经记录 Cu 使用 EAM 势；本轮再次提到相同事实，应该去重而不是重复写入。", "normal"),
        ("scope_isolation", "A 会话中用户偏好中文报告，B 会话不应该继承该偏好。", "edge"),
        ("conflict_temp", "先记录 Cu heating 目标 800K，随后用户说其实是 1200K，必须标记冲突并请求确认。", "adversarial"),
        ("raw_evidence", "总结 LAMMPS run.log 时必须保留原始 fatal 行，不能只留压缩摘要。", "edge"),
        ("compression", "长上下文包含 30 条材料事实，只注入最相关证据并保留 source refs。", "edge"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for scenario, prompt, difficulty in scenarios:
        for turn in range(1, 4):
            cases.append(
                _case(
                    case_id=f"memory.{scenario}.turn{turn}",
                    domain="shared_memory",
                    difficulty=difficulty,
                    prompt=f"{prompt} 当前是第 {turn} 轮 follow-up，请检查共享记忆行为。",
                    source_dataset="matterlab_memory_cases",
                    source_suite="shared_memory",
                    expected_route="conversation.answer",
                    expected_compute_domain="none",
                    required_tool_chain=["shared_memory_read", "shared_memory_write"],
                    required_evidence=["memory_source_ref", "raw_evidence" if scenario == "raw_evidence" else "memory_item"],
                    required_findings=["conflict_detected"] if scenario == "conflict_temp" else [],
                    forbidden_claims=[
                        "Do not overwrite conflicting facts without recording conflict status.",
                        "Do not leak memory across unrelated conversation scopes.",
                    ],
                    tags=["memory", scenario, f"turn{turn}"],
                    metadata={"scenario": scenario, "conversation_turn": turn},
                )
            )
    return cases


def _build_recovery_cases() -> list[MaterialsAgentBenchCase]:
    failures = [
        ("node_timeout", "LAMMPS preflight 的 potential_check 节点超时，应该局部 fallback 并记录 degraded。"),
        ("batch_failure", "preflight 中 registry_check 和 script_validate 同时失败，应该批量 replan。"),
        ("global_timeout", "整体任务超时，只能基于已完成证据生成 partial report。"),
        ("cancel_resume", "用户取消 job 后要求恢复，应该创建新 attempt 而不是伪造原地续跑。"),
        ("checkpoint_reuse", "已有安全 checkpoint，可复用 registry 和 RAG 结果，但必须重跑失败节点。"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for failure_id, prompt in failures:
        for retry_index in range(1, 3):
            cases.append(
                _case(
                    case_id=f"recovery.{failure_id}.attempt{retry_index}",
                    domain="orchestration_recovery",
                    difficulty="adversarial" if failure_id in {"global_timeout", "cancel_resume"} else "edge",
                    prompt=f"{prompt} 这是第 {retry_index} 次恢复测试，请输出生命周期状态和恢复策略。",
                    source_dataset="matterlab_recovery_cases",
                    source_suite="orchestration_recovery",
                    expected_route="lammps.generate",
                    expected_compute_domain="lammps",
                    required_tool_chain=["dag_executor", "lifecycle_state_machine", "replan_policy"],
                    required_evidence=["checkpoint_context", "degradation_report"],
                    required_findings=[failure_id, "partial_report" if failure_id == "global_timeout" else "safe_replan"],
                    forbidden_claims=[
                        "Do not claim completed execution when only partial evidence exists.",
                        "Do not resume real LAMMPS timesteps in-place without explicit runtime support.",
                    ],
                    tags=["orchestration", "recovery", failure_id],
                    metadata={"failure_mode": failure_id, "attempt": retry_index},
                )
            )
    return cases


def _build_final_response_cases() -> list[MaterialsAgentBenchCase]:
    tasks = [
        ("lammps_report", "根据 run.log、quality_report 和 Red-Blue 审查，生成最终中文报告。"),
        ("rag_answer", "根据 RAG 证据解释 fix npt 与 fix nvt 的差异，并给出应用边界。"),
        ("phase_summary", "解释 Pb-Sn 相图结果，明确哪些来自计算，哪些是一般材料学知识。"),
        ("unsupported_material", "用户要求对未支持材料体系直接运行模拟，最终回答必须安全拒绝并给替代方案。"),
        ("no_evidence", "RAG 未召回足够证据，最终回答必须承认不足并建议补充数据。"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for task_id, prompt in tasks:
        for style in ("concise", "detailed", "reviewer"):
            cases.append(
                _case(
                    case_id=f"final_response.{task_id}.{style}",
                    domain="final_response",
                    difficulty="adversarial" if task_id in {"unsupported_material", "no_evidence"} else "edge",
                    prompt=f"{prompt} 输出风格：{style}。必须区分证据、推断和建议。",
                    source_dataset="matterlab_final_response_cases",
                    source_suite="final_response",
                    expected_route="conversation.answer",
                    expected_compute_domain="none",
                    required_tool_chain=["grounded_synthesis"],
                    required_evidence=["artifact_summary" if "lammps" in task_id else "retrieved_context"],
                    required_findings=["honesty_about_missing_evidence"] if task_id == "no_evidence" else [],
                    forbidden_claims=[
                        "Do not present mock or synthetic artifacts as real experimental evidence.",
                        "Do not invent citations or exact numeric phase boundaries.",
                    ],
                    claim_gold=[
                        {
                            "claim_id": f"{task_id}.{style}.evidence_vs_recommendation",
                            "claim": "final answer separates evidence from recommendation",
                            "required": True,
                        },
                        {
                            "claim_id": f"{task_id}.{style}.limitations",
                            "claim": "final answer states limitations",
                            "required": True,
                        },
                    ],
                    judge_rubric={
                        "dimensions": ["factuality", "logical_consistency", "citation_quality", "physical_validity", "actionable_clarity"],
                        "pass_threshold": 4,
                    },
                    tags=["final_response", task_id, style],
                    metadata={"style": style, "task_id": task_id},
                )
            )
    return cases


def _build_phase_registry_cases() -> list[MaterialsAgentBenchCase]:
    systems = [
        ("Pb-Sn", 300, 800, "eutectic", "pbsn.tdb"),
        ("Al-Zn", 300, 1000, "binary", "alzn_mey.tdb"),
        ("Al-Ni", 300, 1800, "intermetallic", "alni_dupin_2001.tdb"),
        ("Fe-Ni", 300, 2300, "high_temperature", "FeNi_deep_branching.tdb"),
        ("Al-Cu-Y", 300, 1600, "ternary", "Al-Cu-Y.tdb"),
    ]
    variants = [
        ("exact_registry", "要求优先 exact registry，不要被 RAG 覆盖。", "normal"),
        ("alias_registry", "用户使用中文别名和元素顺序反转，仍应找到正确 TDB。", "edge"),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for system, t_min, t_max, family, database_name in systems:
        for variant, instruction, difficulty in variants:
            prompt = f"请生成 {system} 相图，温度 {t_min}K 到 {t_max}K。{instruction}"
            cases.append(
                _case(
                    case_id=f"phase_registry.{system.lower().replace('-', '_')}.{variant}",
                    domain="phase_diagram_execution",
                    difficulty=difficulty,
                    prompt=prompt,
                    source_dataset="matterlab_phase_registry_cases",
                    source_suite="phase_registry",
                    expected_route="phase_diagram.generate",
                    expected_compute_domain="phase_diagram",
                    locked_constraints={
                        "system_name": system,
                        "temperature_min": t_min,
                        "temperature_max": t_max,
                    },
                    required_tool_chain=["thermo_registry_search", "thermo_rag_optional", "pycalphad_execution"],
                    required_evidence=["thermo_registry_card", database_name],
                    required_artifacts=["result.html", "summary.json"],
                    forbidden_claims=["Do not use a RAG suggestion to override an exact TDB registry match."],
                    tags=["phase_diagram", family, variant],
                    metadata={"database_name": database_name, "family": family, "variant": variant},
                )
            )
    return cases


def _build_dynamic_route_cases() -> list[MaterialsAgentBenchCase]:
    tiers = [
        (
            "fast_memory",
            "只需要回忆上一轮用户偏好的报告语言，不需要调用外部工具或强模型。",
            "conversation.answer",
            "none",
            "fast",
            "normal",
        ),
        (
            "balanced_rag",
            "需要检索 Materials RAG 后解释 fix nvt 的适用范围，不能直接凭空回答。",
            "conversation.answer",
            "none",
            "balanced",
            "edge",
        ),
        (
            "strong_lammps",
            "需要生成 Cu heating 的 LAMMPS 输入、执行 preflight 并检查质量门。",
            "lammps.generate",
            "lammps",
            "strong",
            "edge",
        ),
        (
            "vision_recognition",
            "用户上传相图截图，要求识别坐标轴、相区和可能体系。",
            "recognition.analyze",
            "none",
            "vision",
            "edge",
        ),
        (
            "guarded_no_downgrade",
            "任务包含 unsupported potential、Red-Blue 修复和最终报告，MLP 即使建议 balanced 也不能降级到低于 strong。",
            "lammps.generate",
            "lammps",
            "strong",
            "adversarial",
        ),
    ]
    cases: list[MaterialsAgentBenchCase] = []
    for case_id, prompt, route, compute_domain, expected_tier, difficulty in tiers:
        cases.append(
            _case(
                case_id=f"llm_route.{case_id}",
                domain="routing_clarification",
                difficulty=difficulty,
                prompt=prompt,
                source_dataset="matterlab_dynamic_route_cases",
                source_suite="llm_dynamic_routing",
                expected_route=route,
                expected_compute_domain=compute_domain,
                required_tool_chain=["llm_route_policy"],
                required_evidence=["route_features", "capability_min_tier"],
                required_findings=[f"expected_tier:{expected_tier}"],
                forbidden_claims=[
                    "Do not let the learned MLP policy override capability minimum tiers.",
                    "Do not route vision tasks to a non-vision model tier.",
                ],
                tags=["llm_routing", expected_tier, case_id],
                metadata={"expected_llm_tier": expected_tier, "route_mode": "rule_plus_guarded_mlp"},
            )
        )
    return cases


def _construction_manifest(cases: list[MaterialsAgentBenchCase]) -> dict[str, Any]:
    augmented = [case for case in cases if case.metadata.get("generation", {}).get("kind") == "matterlab_bench_500_augmented"]
    by_source: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    for case in augmented:
        by_source[case.source_dataset] = by_source.get(case.source_dataset, 0) + 1
        by_difficulty[case.difficulty] = by_difficulty.get(case.difficulty, 0) + 1
    return {
        "design_principles": [
            "scenario coverage over a single aggregate accuracy score",
            "component metrics for route, tool, RAG, runtime, trajectory, memory, recovery, and final synthesis",
            "deterministic first, live/API-dependent gates explicit",
            "frozen split for regression and development split for iteration",
            "case-level provenance, forbidden claims, and required evidence",
        ],
        "base_case_count": len(cases) - len(augmented),
        "augmented_case_count": len(augmented),
        "augmented_sources": dict(sorted(by_source.items())),
        "augmented_difficulty": dict(sorted(by_difficulty.items())),
    }


def _display_path(path: Path) -> str:
    try:
        return str(Path("backend") / path.relative_to(BACKEND_ROOT))
    except ValueError:
        return path.name


def _summarize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MatterLabAgentBench-500")
    parser.add_argument("--dataset-dir", type=Path, default=BENCHMARK_DATASET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    try:
        manifest = write_matterlab_agent_bench(
            target_count=args.target_count,
            dataset_dir=args.dataset_dir,
            output_dir=args.output_dir,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(args.output_dir),
                "manifest": _summarize_manifest(manifest) if args.summary_only else manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
