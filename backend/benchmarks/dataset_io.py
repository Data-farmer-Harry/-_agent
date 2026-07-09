from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.benchmark_config import BACKEND_ROOT, BENCHMARK_DATASET_DIR


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_datasets(dataset_dir: Path = BENCHMARK_DATASET_DIR) -> dict[str, list[dict[str, Any]]]:
    if not dataset_dir.exists():
        raise FileNotFoundError(f"benchmark dataset directory does not exist: {dataset_dir}")
    datasets: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(dataset_dir.glob("*.jsonl")):
        datasets[path.stem] = load_jsonl(path)
    return datasets


def resolve_backend_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return BACKEND_ROOT / path


def validate_datasets(datasets: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    required_common = {"case_id", "suite", "mode", "tags"}
    for dataset_name, rows in datasets.items():
        if not rows:
            errors.append(f"{dataset_name}: dataset is empty")
            continue
        for index, row in enumerate(rows, start=1):
            dataset_required_common = {"case_id", "suite"} if dataset_name == "rag_blind_cases" else required_common
            missing = sorted(dataset_required_common - set(row.keys()))
            if missing:
                errors.append(f"{dataset_name}:{index}: missing common fields {missing}")
            if dataset_name == "rag_blind_cases":
                for field in ("query", "material", "domain", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name in {
                "routing_cases",
                "phase_parsing_cases",
                "lammps_parsing_cases",
                "phase_execution_cases",
                "lammps_contract_cases",
                "lammps_e2e_cases",
                "recognition_cases",
            }:
                for field in ("prompt", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "lammps_quality_cases":
                for field in ("fixture", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "lammps_red_blue_cases":
                for field in ("scenario", "fixture", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "review_json_fallback_cases":
                for field in ("payload_type", "schema", "raw", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "orchestration_cases":
                for field in ("scenario", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "judge_calibration_cases":
                for field in ("observation", "human_scores", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "lammps_recovery_cases":
                for field in ("scenario", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "external_recognition_cases":
                for field in ("prompt", "expected", "asset_path", "source_url"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
                else:
                    asset_path = resolve_backend_path(row["asset_path"])
                    if not asset_path.exists():
                        errors.append(f"{dataset_name}:{index}: asset_path does not exist: {asset_path}")
            if dataset_name == "memory_followup_cases" and "turns" not in row:
                errors.append(f"{dataset_name}:{index}: missing turns")
            if dataset_name == "memory_retrieval_cases":
                for field in ("seed_messages", "query", "expected_hits"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "shared_memory_cases":
                for field in ("scenario", "items", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "memory_conflict_cases":
                for field in ("scenario", "items", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "context_compression_cases":
                for field in ("scenario", "items", "query", "expected"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "materials_multihop_cases":
                for field in ("prompt", "expected", "observation"):
                    if field not in row:
                        errors.append(f"{dataset_name}:{index}: missing {field}")
            if dataset_name == "mcp_cases" and "request" not in row:
                errors.append(f"{dataset_name}:{index}: missing request")
    return errors
