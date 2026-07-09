from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings
from app.materials_rag.models import MaterialsRagQuery
from app.materials_rag.retriever import _build_index, search_materials_rag
from app.rag.sqlite_vector_store import get_vector_store
from app.thermo.rag_index import build_thermo_card_index
from app.thermo.rag_retriever import search_thermo_cards


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "outputs" / "rag_recall" / "latest.json"
RECALL_KS = (1, 3, 5)


def _mean(values) -> float:  # noqa: ANN001
    sequence = list(values)
    if not sequence:
        raise ValueError("mean requires at least one value")
    return sum(float(value) for value in sequence) / len(sequence)


@dataclass(frozen=True)
class RecallCase:
    case_id: str
    query: str
    expected: tuple[str, ...]
    domain: str | None = None
    doc_type: str | None = None
    material: str | None = None


MATERIALS_CASES: tuple[RecallCase, ...] = (
    RecallCase("mat_fix_nvt_cn", "fix nvt 怎么保持 800K 恒温？", ("lammps.command.fix_nvt",), domain="lammps"),
    RecallCase("mat_compute_msd_cn", "LAMMPS 里 MSD 怎么算扩散系数？", ("lammps.command.compute_msd",), domain="lammps"),
    RecallCase(
        "mat_dump_trajectory",
        "怎么输出 dump.atom 轨迹给 OVITO 看？",
        ("lammps.command.dump_custom", "lammps.process.ovito_inspection"),
        domain="lammps",
    ),
    RecallCase("mat_thermo_output", "thermo_style custom 怎么输出温度、势能和压力？", ("lammps.command.thermo_style",), domain="lammps"),
    RecallCase("mat_eam_cu", "Cu heating 金属体系应该优先考虑什么势函数？", ("lammps.potential.eam_metals",), domain="lammps"),
    RecallCase("mat_meam", "MEAM 势函数适合哪些金属或半金属体系？", ("lammps.potential.meam",), domain="lammps"),
    RecallCase("mat_lost_atoms", "LAMMPS lost atoms 报错一般怎么处理？", ("lammps.error.lost_atoms",), domain="lammps"),
    RecallCase("mat_out_of_range", "out of range atoms cannot compute PPPM 是什么错误？", ("lammps.error.out_of_range_atoms",), domain="lammps"),
    RecallCase("mat_calphad_tdb", "CALPHAD 和 TDB 热力学数据库是什么关系？", ("thermo.concept.calphad",), domain="thermodynamics"),
    RecallCase("mat_phase_rule", "相律 Gibbs phase rule 在相图里怎么理解？", ("thermo.concept.phase_rule",), domain="thermodynamics"),
    RecallCase("mat_materials_project_api", "Materials Project API 能查 band gap 和 formation energy 吗？", ("materials.database.materials_project_api",), domain="materials"),
    RecallCase("mat_energy_above_hull", "energy above hull 怎么判断材料稳定性？", ("materials.concept.energy_above_hull",), domain="materials"),
    RecallCase("mat_band_gap", "band gap 是判断绝缘体半导体的重要指标吗？", ("materials.concept.band_gap",), domain="materials"),
    RecallCase("mat_jarvis_elastic_phonon", "JARVIS DFT 有没有 elastic tensor 和 phonon 数据？", ("materials.database.jarvis_dft",), domain="materials"),
    RecallCase("mat_elastic_tensor", "elastic tensor 和 bulk modulus shear modulus 有什么关系？", ("materials.concept.elastic_tensor",), domain="materials"),
    RecallCase("mat_phonon", "phonon stability 出现 imaginary frequency 代表什么？", ("materials.concept.phonon_stability",), domain="materials"),
    RecallCase("mat_ti_hcp", "Ti 常温是什么晶体结构？", ("materials.card.ti_hcp",), domain="materials", material="Ti"),
    RecallCase("mat_potential_selection", "氧化物或离子体系怎么选择 interatomic potential？", ("materials.workflow.potential_selection",), domain="materials"),
)


WIKIPEDIA_MATERIALS_CASES: tuple[RecallCase, ...] = (
    RecallCase(
        "wiki_materials_paradigm_cn",
        "材料的加工、内部结构、性能和服役表现之间是什么关系？",
        ("wikipedia.en.materials-science.chunk1", "wikipedia.en.materials-science.chunk2"),
    ),
    RecallCase(
        "wiki_grain_boundary_diffusion",
        "Why can interfaces between neighboring crystals act as fast diffusion paths and affect strength?",
        ("wikipedia.en.grain-boundary.chunk1", "wikipedia.en.grain-boundary.chunk2"),
    ),
    RecallCase(
        "wiki_vacancy_cn",
        "晶格中缺少一个本应占据格点的原子，这属于什么缺陷？",
        ("wikipedia.en.vacancy-defect.chunk1", "wikipedia.en.vacancy-defect.chunk2"),
    ),
    RecallCase(
        "wiki_creep_cn",
        "材料在高温和恒定载荷下随时间缓慢积累变形是什么现象？",
        ("wikipedia.en.creep-deformation.chunk1", "wikipedia.en.creep-deformation.chunk2"),
    ),
    RecallCase(
        "wiki_precipitation_hardening_cn",
        "时效过程中形成细小第二相颗粒，为什么能阻碍位错并增强合金？",
        ("wikipedia.en.precipitation-hardening.chunk1", "wikipedia.en.precipitation-hardening.chunk2"),
    ),
    RecallCase(
        "wiki_tem_cn",
        "如何利用穿透极薄样品的电子束观察位错和晶格细节？",
        (
            "wikipedia.en.transmission-electron-microscopy.chunk1",
            "wikipedia.en.transmission-electron-microscopy.chunk2",
        ),
    ),
    RecallCase(
        "wiki_spinodal_cn",
        "在自由能曲线负曲率区域，体系不需要克服形核势垒就发生成分起伏和分相，这是什么过程？",
        ("wikipedia.en.spinodal-decomposition.chunk1", "wikipedia.en.spinodal-decomposition.chunk2"),
    ),
    RecallCase(
        "wiki_semiconductor_band_structure",
        "How does the energy separation between occupied and empty electronic states control semiconductor conduction?",
        (
            "wikipedia.en.semiconductor.chunk1",
            "wikipedia.en.semiconductor.chunk2",
            "wikipedia.en.band-gap.chunk1",
            "wikipedia.en.band-gap.chunk2",
        ),
    ),
    RecallCase(
        "wiki_molecular_dynamics",
        "Which atomistic simulation method integrates classical equations of motion to generate atomic trajectories?",
        ("wikipedia.en.molecular-dynamics.chunk1", "wikipedia.en.molecular-dynamics.chunk2"),
    ),
    RecallCase(
        "wiki_eam",
        "Which many-body interatomic model represents metallic bonding using the energy of embedding an atom in local electron density?",
        ("wikipedia.en.embedded-atom-model.chunk1", "wikipedia.en.embedded-atom-model.chunk2"),
    ),
    RecallCase(
        "wiki_quenching",
        "Rapidly cooling a hot metal to suppress equilibrium transformations is which heat-treatment operation?",
        ("wikipedia.en.quenching.chunk1", "wikipedia.en.quenching.chunk2"),
    ),
    RecallCase(
        "wiki_fatigue",
        "Progressive damage and crack growth caused by repeated cyclic loading is known as what material failure mechanism?",
        ("wikipedia.en.fatigue-material.chunk1", "wikipedia.en.fatigue-material.chunk2"),
    ),
    RecallCase(
        "wiki_phase_rule_cn",
        "相平衡中组元数、相数和自由度之间是什么关系？",
        ("wikipedia.en.phase-rule.chunk1", "wikipedia.en.phase-rule.chunk2"),
        domain="thermodynamics",
    ),
    RecallCase(
        "wiki_martensite_cn",
        "淬火钢中马氏体的无扩散相变有什么特点？",
        ("wikipedia.en.martensite.chunk1", "wikipedia.en.martensite.chunk2"),
        domain="metallurgy",
    ),
    RecallCase(
        "wiki_xrd_cn",
        "XRD 如何通过衍射角和晶面间距做物相鉴定？",
        ("wikipedia.en.x-ray-diffraction.chunk1", "wikipedia.en.x-ray-diffraction.chunk2"),
        domain="characterization",
    ),
    RecallCase(
        "wiki_interatomic_potential_cn",
        "原子间势和 MEAM、Stillinger-Weber 势如何用于分子动力学？",
        ("wikipedia.en.interatomic-potential.chunk1", "wikipedia.en.interatomic-potential.chunk2"),
        domain="computational_materials",
    ),
    RecallCase(
        "wiki_high_entropy_alloy_cn",
        "多主元高熵合金中的构型熵是什么意思？",
        ("wikipedia.en.high-entropy-alloy.chunk1", "wikipedia.en.high-entropy-alloy.chunk2"),
        domain="metallurgy",
    ),
)


THERMO_CASES: tuple[RecallCase, ...] = (
    RecallCase("thermo_alzn_cn", "生成铝锌二元相图", ("Al-Zn",)),
    RecallCase("thermo_pbsn_eutectic", "Pb Sn eutectic phase diagram", ("Pb-Sn",)),
    RecallCase("thermo_alni_intermetallic", "Al-Ni phase diagram with intermetallic compounds", ("Al-Ni",)),
    RecallCase("thermo_almg_cn", "计算 Al Mg 二元相图", ("Al-Mg",)),
    RecallCase("thermo_alfe_cn", "铁铝相图，包含 Al13Fe4 和 BCC", ("Al-Fe",)),
    RecallCase("thermo_cuni_cn", "铜镍等温固溶相图数据库", ("Cu-Ni",)),
    RecallCase("thermo_nbre_nonstandard", "Nb Re binary with LIQUID_RENB phase", ("Nb-Re",)),
    RecallCase("thermo_crfe_sigma", "Cr-Fe phase diagram with sigma phase", ("Cr-Fe",)),
    RecallCase("thermo_fenb_laves", "Fe-Nb Laves phase thermodynamic database", ("Fe-Nb",)),
    RecallCase("thermo_crnb_laves", "Cr-Nb binary Laves C15 phase", ("Cr-Nb",)),
    RecallCase("thermo_crti", "Cr Ti binary phase diagram", ("Cr-Ti",)),
    RecallCase("thermo_tiv", "Ti-V binary phase diagram", ("Ti-V",)),
    RecallCase("thermo_feco", "Fe Co magnetic alloy phase diagram", ("Fe-Co",)),
    RecallCase("thermo_coni", "Co-Ni binary thermodynamic database", ("Co-Ni",)),
    RecallCase("thermo_alco", "Al-Co binary phase diagram AL3CO", ("Al-Co",)),
    RecallCase("thermo_alpt", "Al Pt intermetallic phase diagram", ("Al-Pt",)),
    RecallCase("thermo_pdtc", "Pd-Tc binary thermodynamic data", ("Pd-Tc",)),
    RecallCase("thermo_rumo", "Ru Mo binary phase diagram", ("Ru-Mo",)),
)


def _first_match_rank(retrieved: list[str], expected: tuple[str, ...]) -> int | None:
    expected_set = set(expected)
    for index, item in enumerate(retrieved, start=1):
        if item in expected_set:
            return index
    return None


def _summarize(results: list[dict[str, object]]) -> dict[str, object]:
    total = len(results)
    ranks = [int(result["rank"]) for result in results if result["rank"] is not None]
    summary: dict[str, object] = {
        "total_cases": total,
        "hits": len(ranks),
        "misses": total - len(ranks),
        "mrr": round(_mean(1.0 / rank for rank in ranks), 4) if ranks else 0.0,
        "mean_rank": round(_mean(ranks), 3) if ranks else None,
    }
    for k in RECALL_KS:
        hit_count = sum(1 for rank in ranks if rank <= k)
        summary[f"hit@{k}"] = round(hit_count / total, 4) if total else 0.0
    return summary


def _evaluate(
    *,
    cases: tuple[RecallCase, ...],
    retrieve: Callable[[RecallCase, int], tuple[list[str], list[dict[str, object]], str]],
    top_k: int,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    backends: list[str] = []
    reranker_backends: list[str] = []
    started = time.perf_counter()
    for case in cases:
        retrieved, raw_hits, backend = retrieve(case, top_k)
        if backend:
            backends.append(backend)
        reranker_backends.extend(
            str(hit.get("reranker_backend") or "")
            for hit in raw_hits
            if hit.get("reranker_backend")
        )
        rank = _first_match_rank(retrieved, case.expected)
        results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected": list(case.expected),
                "rank": rank,
                "retrieved": retrieved,
                "top_hits": raw_hits[:top_k],
            }
        )
    elapsed = time.perf_counter() - started
    return {
        "summary": {
            **_summarize(results),
            "elapsed_seconds": round(elapsed, 3),
            "embedding_backends": sorted(set(backends)),
            "reranker_backends": sorted(set(reranker_backends)),
        },
        "cases": results,
    }


def _materials_retrieve(case: RecallCase, top_k: int) -> tuple[list[str], list[dict[str, object]], str]:
    hits = search_materials_rag(
        MaterialsRagQuery(
            query=case.query,
            domain=case.domain,
            doc_type=case.doc_type,
            material=case.material,
            top_k=top_k,
        )
    )
    retrieved = [hit.document.id for hit in hits]
    raw_hits = [
        {
            "id": hit.document.id,
            "title": hit.document.title,
            "score": hit.score,
            "lexical_score": hit.lexical_score,
            "bm25_score": hit.bm25_score,
            "vector_score": hit.vector_score,
            "embedding_backend": hit.embedding_backend,
            "rerank_score": hit.rerank_score,
            "reranker_backend": hit.reranker_backend,
            "original_rank": hit.original_rank,
            "matched_fields": hit.matched_fields,
        }
        for hit in hits
    ]
    backend = hits[0].embedding_backend if hits else ""
    return retrieved, raw_hits, backend


def _thermo_retrieve(case: RecallCase, top_k: int) -> tuple[list[str], list[dict[str, object]], str]:
    hits = search_thermo_cards(case.query, top_k=top_k, min_score=0.0)
    retrieved = [hit.card.system_name for hit in hits]
    raw_hits = [
        {
            "system_name": hit.card.system_name,
            "score": round(hit.score, 4),
            "lexical_score": round(hit.lexical_score, 4),
            "bm25_score": round(hit.bm25_score, 4),
            "vector_score": round(hit.vector_score, 4),
            "embedding_backend": hit.embedding_backend,
            "rerank_score": hit.rerank_score,
            "reranker_backend": hit.reranker_backend,
            "original_rank": hit.original_rank,
            "match_reasons": list(hit.match_reasons),
        }
        for hit in hits
    ]
    backend = hits[0].embedding_backend if hits else ""
    return retrieved, raw_hits, backend


def _warm_embeddings() -> dict[str, object]:
    materials_index = _build_index()
    thermo_index = build_thermo_card_index()
    inventory = get_vector_store().inventory()
    collections = {
        str(item.get("collection")): item
        for item in inventory.get("collections", [])
        if isinstance(item, dict)
    }
    materials_store = collections.get("materials_rag", {})
    thermo_store = collections.get("thermo_rag", {})
    return {
        "materials_documents": len(materials_index),
        "materials_embedding_backend": materials_index[0].embedding_backend if materials_index else "",
        "materials_vector_dim": int(materials_store.get("dimensions") or 0),
        "thermo_documents": len(thermo_index),
        "thermo_embedding_backend": thermo_index[0].embedding_backend if thermo_index else "",
        "thermo_vector_dim": int(thermo_store.get("dimensions") or 0),
        "reranker_enabled": settings.rag_reranker_enabled,
        "reranker_model": settings.rag_reranker_model,
        "vector_store_backend": inventory.get("backend"),
        "vector_store_path": inventory.get("database_path"),
        "sqlite_vec_version": inventory.get("extension_version"),
    }


def run_rag_recall(*, top_k: int = 5, require_remote: bool = False, limit: int | None = None) -> dict[str, object]:
    top_k = max(top_k, max(RECALL_KS))
    started = time.perf_counter()
    embedding = _warm_embeddings()
    if require_remote:
        backends = {embedding["materials_embedding_backend"], embedding["thermo_embedding_backend"]}
        if backends != {"llm_api"}:
            raise RuntimeError(f"Remote embedding required, but active backends are: {sorted(backends)}")
    material_cases = MATERIALS_CASES[:limit] if limit else MATERIALS_CASES
    wikipedia_cases = WIKIPEDIA_MATERIALS_CASES[:limit] if limit else WIKIPEDIA_MATERIALS_CASES
    thermo_cases = THERMO_CASES[:limit] if limit else THERMO_CASES
    materials = _evaluate(cases=material_cases, retrieve=_materials_retrieve, top_k=top_k)
    wikipedia_materials = _evaluate(cases=wikipedia_cases, retrieve=_materials_retrieve, top_k=top_k)
    thermo = _evaluate(cases=thermo_cases, retrieve=_thermo_retrieve, top_k=top_k)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "top_k": top_k,
        "embedding": {
            **embedding,
            "materials_model": settings.materials_rag_embedding_model,
            "thermo_model": settings.thermo_rag_embedding_model,
        },
        "materials_rag": materials,
        "wikipedia_materials_rag": wikipedia_materials,
        "thermo_rag": thermo,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG embedding recall for materials and thermo retrieval.")
    parser.add_argument("--top-k", type=int, default=5, help="Maximum number of retrieved hits to inspect.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON output path.")
    parser.add_argument("--require-remote", action="store_true", help="Fail if either RAG index falls back to local_hash.")
    parser.add_argument("--limit", type=int, default=None, help="Limit materials and thermo recall cases for quick smoke tests.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report to stdout.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_rag_recall(top_k=args.top_k, require_remote=args.require_remote, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "embedding": report["embedding"],
                    "materials_summary": report["materials_rag"]["summary"],
                    "wikipedia_materials_summary": report["wikipedia_materials_rag"]["summary"],
                    "thermo_summary": report["thermo_rag"]["summary"],
                    "elapsed_seconds": report["elapsed_seconds"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
