from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.llm_route_learning import NeuralRouteModel, extract_route_features
from app.lammps.ir import compile_ir, request_to_ir
from app.lammps.multifidelity import evaluate_pilot, plan_multifidelity_run
from app.materials_rag.models import MaterialsRagDocument, MaterialsRagHit
from app.orchestration import DAGNode, DAGPlan
from app.orchestration.reward import build_plan_variants, search_plans
from app.rag.uncertainty import estimate_retrieval_uncertainty
from benchmarks.train_llm_route_mlp import build_route_probe_cases


def run_advanced_methods_benchmark() -> dict[str, Any]:
    sections = {
        "mlp_route_policy": _benchmark_mlp(),
        "process_reward_search": _benchmark_prm(),
        "neurosymbolic_ir": _benchmark_ir(),
        "uncertainty_rag": _benchmark_uncertainty(),
        "multifidelity": _benchmark_multifidelity(),
    }
    total = sum(int(section["cases"]) for section in sections.values())
    passed = sum(int(section["passed"]) for section in sections.values())
    return {
        "schema_version": "matterlab-advanced-methods-benchmark/v1",
        "cases": total,
        "passed": passed,
        "accuracy": passed / max(total, 1),
        "sections": sections,
        "interpretation": (
            "Deterministic method-contract benchmark. It measures policy learning, constraint rejection, "
            "selective retrieval, process scoring, and pilot decisions; it is not a live LLM or real-LAMMPS accuracy claim."
        ),
    }


def _benchmark_mlp() -> dict[str, Any]:
    model_path = BACKEND_ROOT / "models" / "llm_route_mlp" / "model.json"
    model = NeuralRouteModel.load(model_path)
    rows = build_route_probe_cases()
    passed = 0
    by_label: dict[str, dict[str, int]] = {}
    for row in rows:
        expected = str(row["label"])
        features = extract_route_features(
            system_prompt=str(row["system_prompt"]),
            user_prompt=str(row["user_prompt"]),
            max_tokens=int(row["max_tokens"]),
            temperature=float(row["temperature"]),
            capability=str(row["capability"]),
            multimodal=bool(row["multimodal"]),
        )
        predicted = model.recommend(features).tier
        passed += int(predicted == expected)
        bucket = by_label.setdefault(expected, {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        bucket["passed"] += int(predicted == expected)
    return {
        "cases": len(rows),
        "passed": passed,
        "accuracy": passed / max(len(rows), 1),
        "by_label": by_label,
        "model_path": str(model_path),
    }


def _benchmark_prm() -> dict[str, Any]:
    passed = 0
    cases = 40
    selected_distribution: dict[str, int] = {}
    for index in range(cases):
        plan = DAGPlan(
            plan_id=f"plan-{index}",
            global_timeout_seconds=600,
            nodes=[
                DAGNode(node_id="retrieve", node_type="rag", critical=False, retryable=True, max_attempts=2, timeout_seconds=45, output_keys=["evidence_refs"]),
                DAGNode(node_id="validate", node_type="validate", dependencies=["retrieve"], critical=True, timeout_seconds=60, output_keys=["validation_report"]),
                DAGNode(node_id="execute", node_type="simulation", dependencies=["validate"], critical=True, resource_class="simulation", timeout_seconds=120, output_keys=["execution_report"]),
            ],
        )
        risk_level = 0.85 if index % 2 else 0.15
        result = search_plans(build_plan_variants(plan), latency_budget_seconds=250 + index, risk_level=risk_level)
        policy = str(result.selected_plan.metadata.get("candidate_policy") or "unknown")
        selected_distribution[policy] = selected_distribution.get(policy, 0) + 1
        expected_policy = "robust" if risk_level > 0.5 else "efficient"
        passed += int(
            len(result.candidate_scores) == 3
            and result.selected_plan.topological_order() == ["retrieve", "validate", "execute"]
            and policy == expected_policy
        )
    return {"cases": cases, "passed": passed, "contract_rate": passed / cases, "selected_distribution": selected_distribution}


def _benchmark_ir() -> dict[str, Any]:
    cases = 100
    passed = 0
    for index in range(cases):
        valid = index % 2 == 0
        request = {
            "material": "Cu" if index % 4 else "Al",
            "potential_family": "eam",
            "task_type": "heating",
            "temperature": 900 if valid else 2500,
            "initial_temp": 300,
            "steps": 5000,
            "ensemble": "NVT",
            "time_step": 0.001 if valid else 0.02,
        }
        rejected = False
        try:
            compile_ir(request_to_ir(request))
        except Exception:
            rejected = True
        passed += int((valid and not rejected) or ((not valid) and rejected))
    return {"cases": cases, "passed": passed, "mutation_detection_rate": passed / cases}


def _benchmark_uncertainty() -> dict[str, Any]:
    cases = 80
    passed = 0
    doc = MaterialsRagDocument(id="evidence", domain="lammps", doc_type="guide", title="Evidence", content="grounded")
    for index in range(cases):
        answerable = index % 2 == 0
        if answerable:
            hits = [
                MaterialsRagHit(document=doc, score=8.0, lexical_score=2.0, bm25_score=1.0, vector_score=0.9, rerank_score=0.9, graph_score=0.8),
                MaterialsRagHit(document=doc.model_copy(update={"id": f"evidence-{index}"}), score=2.0, lexical_score=1.0, bm25_score=0.5, vector_score=0.6, rerank_score=0.7, graph_score=0.5),
            ]
        else:
            hits = []
        action = estimate_retrieval_uncertainty(hits).action
        passed += int((answerable and action == "answer") or ((not answerable) and action == "abstain"))
    return {"cases": cases, "passed": passed, "selective_action_accuracy": passed / cases}


def _benchmark_multifidelity() -> dict[str, Any]:
    cases = 80
    passed = 0
    avoided_full_runs = 0
    for index in range(cases):
        risky = index % 2 == 1
        plan = plan_multifidelity_run(
            {
                "task_type": "heating" if risky else "equilibration",
                "temperature": 1500 if risky else 600,
                "steps": 20_000 if risky else 800,
                "time_step": 0.006 if risky else 0.001,
            },
            enabled=True,
        )
        if risky:
            decision = evaluate_pilot(plan, execution_success=False, quality_passed=False, scientific_result_passed=False, fatal_anomalies=1)
            correct = plan.requires_pilot and decision.action == "stop"
            avoided_full_runs += int(decision.action == "stop")
        else:
            correct = not plan.requires_pilot
        passed += int(correct)
    return {
        "cases": cases,
        "passed": passed,
        "decision_accuracy": passed / cases,
        "avoided_failed_full_runs": avoided_full_runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic benchmark contracts for MatterLab advanced methods.")
    parser.add_argument("--output", type=Path, default=Path("outputs/benchmarks/advanced_methods.json"))
    args = parser.parse_args()
    report = run_advanced_methods_benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
