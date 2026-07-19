from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.llm_route_learning import (  # noqa: E402
    LEARNED_ROUTE_LABELS,
    NeuralRouteModel,
    extract_route_features,
    feature_names,
)
from app.core.llm_capabilities import LLMCapability, get_capability_spec  # noqa: E402


LABEL_TO_ID = {label: index for index, label in enumerate(LEARNED_ROUTE_LABELS)}
ID_TO_LABEL = {index: label for label, index in LABEL_TO_ID.items()}
FEATURE_NAMES = feature_names()


def _portable_path(path: str | Path) -> str:
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def build_synthetic_route_dataset(
    samples_per_class: int = 800,
    seed: int = 20260710,
    *,
    include_challenge_cases: bool = True,
) -> list[dict[str, object]]:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for label in LEARNED_ROUTE_LABELS:
        for case_index in range(samples_per_class):
            rows.append(_sample_case(label=label, case_index=case_index, rng=rng))
    if include_challenge_cases:
        rows.extend(build_route_challenge_cases(seed=seed + 17, repeats=max(2, samples_per_class // 120)))
        rows.extend(build_deployment_wrapper_cases(seed=seed + 29, repeats=max(8, samples_per_class // 40)))
    rng.shuffle(rows)
    return rows


def load_telemetry_route_dataset(
    events_path: Path,
    *,
    max_rows: int = 2500,
    include_failed: bool = False,
) -> list[dict[str, object]]:
    """Build privacy-safe route samples from observability logs.

    The log records must contain precomputed feature vectors. This function does
    not need, read, or reconstruct prompt text; labels come from the actually
    selected final tier. Failed calls are skipped by default because they are
    noisy negative evidence rather than a clean target.
    """

    if not events_path.exists():
        return []

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if len(rows) >= max_rows:
            break
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("event") != "llm.routing_call":
            continue
        if record.get("success") is False and not include_failed:
            continue
        label = str(record.get("tier") or "").strip()
        if label not in LABEL_TO_ID:
            continue
        values = _feature_values_from_telemetry(record)
        if values is None:
            continue
        prompt_hash = str(record.get("prompt_hash") or f"line-{line_number}")
        capability = str(record.get("capability") or "general")
        dedupe_key = (prompt_hash, capability, label)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(
            {
                "case_id": f"telemetry.{line_number:06d}.{prompt_hash}",
                "label": label,
                "feature_values": values,
                "feature_schema": "llm-route-features/v1",
                "capability": capability,
                "max_tokens": int(record.get("requested_max_tokens") or record.get("effective_max_tokens") or 0),
                "temperature": float(record.get("temperature") or 0.0),
                "multimodal": bool(record.get("multimodal")),
                "difficulty": "telemetry_fallback" if record.get("fallback_from") else "telemetry_success",
                "source": "observability_telemetry",
                "prompt_hash": prompt_hash,
                "request_id": str(record.get("request_id") or ""),
                "run_id": str(record.get("run_id") or ""),
                "duration_ms": float(record.get("duration_ms") or 0.0),
                "fallback_from": str(record.get("fallback_from") or ""),
            }
        )
    return rows


def build_simulated_production_telemetry(
    samples: int = 1600,
    seed: int = 20260719,
) -> list[dict[str, object]]:
    """Generate privacy-safe production-like observations for offline routing.

    These rows are simulations, not real user traffic. Labels are selected from
    simulated quality/latency/cost outcomes under the same capability floors as
    runtime routing. Prompt text is discarded after feature extraction.
    """

    rng = np.random.default_rng(seed)
    workloads: tuple[dict[str, object], ...] = (
        {
            "family": "simple_chat",
            "weight": 0.18,
            "capability": LLMCapability.CHAT_ANSWER.value,
            "complexity": (0, 0, 1),
            "system": "Answer the active user question directly and concisely.",
            "prompts": (
                "什么是共晶反应？请简短解释。",
                "你好，请用一句话介绍这个材料 Agent。",
                "解释一下 NVT 和 NPT 的区别，不运行模拟。",
            ),
            "max_tokens": (220, 850),
        },
        {
            "family": "technical_chat",
            "weight": 0.12,
            "capability": LLMCapability.CHAT_ANSWER.value,
            "complexity": (1, 1, 2),
            "system": "Answer a technical materials question and preserve scientific uncertainty.",
            "prompts": (
                "比较 EAM、MEAM 和 LJ 势函数的适用边界，并说明选择依据。",
                "解释 Al-Zn 相图中 liquidus 和 solidus 的关系。",
                "分析升温速率、时间步和系综对分子动力学稳定性的共同影响。",
            ),
            "max_tokens": (700, 1700),
        },
        {
            "family": "memory_summary",
            "weight": 0.09,
            "capability": LLMCapability.MEMORY_SUMMARY.value,
            "complexity": (0, 0, 1),
            "system": "Compress durable research memory without retaining transient wording.",
            "prompts": (
                "压缩这段会话，只保留材料体系、运行结果和未解决问题。",
                "生成长期记忆摘要，保留用户锁定的温度和步数。",
            ),
            "max_tokens": (120, 320),
        },
        {
            "family": "prompt_suggestion",
            "weight": 0.06,
            "capability": LLMCapability.PROMPT_SUGGEST.value,
            "complexity": (0, 0, 1),
            "system": "Suggest one useful next research prompt.",
            "prompts": (
                "根据上一轮结果推荐一个最值得继续追问的问题。",
                "给出一个用于验证模拟可信度的后续提问。",
            ),
            "max_tokens": (120, 300),
        },
        {
            "family": "supervisor",
            "weight": 0.10,
            "capability": LLMCapability.SUPERVISOR_ROUTE.value,
            "complexity": (1, 1, 2),
            "system": "Choose a route and return structured JSON.",
            "prompts": (
                "判断这轮应该聊天、识图、生成相图还是运行 LAMMPS。",
                "用户说继续上一轮并修改温度，请结合历史上下文选择 route_name。",
                "请求同时提到相图和 LAMMPS，请判断是否需要澄清。",
            ),
            "max_tokens": (450, 950),
        },
        {
            "family": "rag_answer",
            "weight": 0.13,
            "capability": LLMCapability.RAG_ANSWER.value,
            "complexity": (1, 1, 2),
            "system": "Use retrieved evidence and answer with grounded scientific claims.",
            "prompts": (
                "结合检索证据解释 Fe-C 共析点并说明引用边界。",
                "根据知识库比较 EAM 与 MEAM，并区分事实和建议。",
                "综合多段材料文献证据分析势函数选择，不得编造引用。",
            ),
            "max_tokens": (650, 1800),
        },
        {
            "family": "phase_compute",
            "weight": 0.09,
            "capability": LLMCapability.PHASE_CODEGEN.value,
            "complexity": (2, 2, 2),
            "system": "Generate a safe pycalphad wrapper under the local TDB contract.",
            "prompts": (
                "为 Al-Zn 生成受限的 pycalphad 相图 wrapper。",
                "修复相图代码并保持数据库、相名和输出契约。",
            ),
            "max_tokens": (1400, 2600),
        },
        {
            "family": "phase_review",
            "weight": 0.06,
            "capability": LLMCapability.PHASE_REVIEW.value,
            "complexity": (2, 2, 2),
            "system": "Review phase-diagram code and structured output conservatively.",
            "prompts": (
                "审查生成相图的数据库来源、相名和 HTML 输出契约。",
                "检查相图结果是否错误声称了不存在的热力学数据。",
            ),
            "max_tokens": (650, 1300),
        },
        {
            "family": "lammps_parse",
            "weight": 0.06,
            "capability": LLMCapability.LAMMPS_REQUEST_PARSE.value,
            "complexity": (2, 2, 2),
            "system": "Parse a LAMMPS request into conservative JSON.",
            "prompts": (
                "解析 Cu 从 300K 加热到 800K、4000 steps、NVT、EAM 的运行参数。",
                "从用户请求中抽取材料、势函数、温度、步数和系综。",
            ),
            "max_tokens": (650, 1100),
        },
        {
            "family": "lammps_repair_review",
            "weight": 0.08,
            "capability": LLMCapability.LAMMPS_REQUEST_REPAIR.value,
            "complexity": (2, 2, 2),
            "system": "Repair and verify a LAMMPS request without changing locked constraints.",
            "prompts": (
                "lost atoms 后安全修复 time_step，并 VERIFY 材料、温度和步数未改变。",
                "根据 thermo 和 run.log 审查失败原因，输出 ADD/MODIFY/VERIFY patch。",
            ),
            "max_tokens": (800, 1700),
        },
        {
            "family": "vision_recognition",
            "weight": 0.03,
            "capability": LLMCapability.VISION_RECOGNITION.value,
            "complexity": (3, 3, 3),
            "system": "Read the uploaded phase-diagram image and return structured visual facts.",
            "prompts": (
                "识别相图截图中的坐标轴、相区和关键点。data:image/png;base64,...",
                "从 image_url 提取 plot region、labels 和 curves。",
            ),
            "max_tokens": (900, 1900),
            "multimodal": True,
        },
    )
    probabilities = np.asarray([float(item["weight"]) for item in workloads], dtype=float)
    probabilities = probabilities / probabilities.sum()
    rows: list[dict[str, object]] = []
    for index in range(max(0, samples)):
        workload = workloads[int(rng.choice(len(workloads), p=probabilities))]
        complexity = int(rng.choice(workload["complexity"]))  # type: ignore[arg-type]
        capability = str(workload["capability"])
        user_prompt = str(rng.choice(workload["prompts"]))  # type: ignore[arg-type]
        system_prompt = str(workload["system"])
        multimodal = bool(workload.get("multimodal"))
        token_bounds = workload["max_tokens"]
        max_tokens = int(rng.integers(int(token_bounds[0]), int(token_bounds[1]) + 1))  # type: ignore[index]
        temperature = float(rng.choice([0.05, 0.1, 0.2]))
        history_chars = int(rng.choice([0, 0, 0, 800, 2400, 7200], p=[0.24, 0.20, 0.16, 0.18, 0.14, 0.08]))
        if history_chars:
            user_prompt = (
                f"User message:\n{user_prompt}\n\n"
                f"Current summary:\n{'context evidence artifact ' * max(1, history_chars // 26)}\n\n"
                "Tool results from this turn:\n(none)\n\nConversation history:\n[]"
            )
        outcomes, label = _simulate_route_outcomes(
            capability=capability,
            complexity=complexity,
            multimodal=multimodal,
            max_tokens=max_tokens,
            rng=rng,
        )
        features = extract_route_features(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
            multimodal=multimodal,
        )
        rows.append(
            {
                "case_id": f"simulated_telemetry.{index:06d}",
                "label": label,
                "feature_values": features.values,
                "feature_schema": "llm-route-features/v1",
                "capability": capability,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "multimodal": multimodal,
                "difficulty": f"simulated_{workload['family']}",
                "source": "simulated_production_telemetry",
                "simulation": {
                    "not_real_user_traffic": True,
                    "workload_family": workload["family"],
                    "complexity": complexity,
                    "history_chars": history_chars,
                    "candidate_outcomes": outcomes,
                },
            }
        )
    return rows


def _simulate_route_outcomes(
    *,
    capability: str,
    complexity: int,
    multimodal: bool,
    max_tokens: int,
    rng: np.random.Generator,
) -> tuple[dict[str, dict[str, object]], str]:
    levels = {"fast": 0, "balanced": 1, "strong": 2, "vision": 3}
    base_latency = {"fast": 480.0, "balanced": 1050.0, "strong": 2350.0, "vision": 2050.0}
    cost_units = {"fast": 0.20, "balanced": 0.65, "strong": 1.45, "vision": 1.70}
    capability_spec = get_capability_spec(capability)
    floor = capability_spec.minimum_tier if capability_spec is not None else "fast"
    candidates = ("vision",) if multimodal else ("fast", "balanced", "strong")
    outcomes: dict[str, dict[str, object]] = {}
    eligible: list[tuple[float, str]] = []
    for tier in candidates:
        level = levels[tier]
        below_floor = tier != "vision" and level < levels.get(floor, 0)
        shortfall = max(0, complexity - level)
        quality = float(np.clip(0.945 - 0.145 * shortfall + 0.012 * max(0, level - complexity) + rng.normal(0, 0.035), 0.0, 1.0))
        success_probability = float(np.clip(0.985 - 0.10 * shortfall, 0.65, 0.995))
        success = bool(rng.random() <= success_probability)
        latency_ms = float(base_latency[tier] * rng.lognormal(mean=0.0, sigma=0.22) * (1.0 + max_tokens / 9000.0))
        cost = float(cost_units[tier] * (0.7 + max_tokens / 2200.0))
        quality_threshold = 0.84 if complexity < 2 else 0.87
        meets_quality = bool(success and quality >= quality_threshold and not below_floor)
        utility = float(latency_ms / 5000.0 + cost * 0.55 + (1.0 - quality) * 4.0)
        outcomes[tier] = {
            "quality_score": round(quality, 4),
            "latency_ms": round(latency_ms, 2),
            "cost_units": round(cost, 4),
            "success": success,
            "meets_quality_floor": meets_quality,
            "below_capability_floor": below_floor,
            "utility": round(utility, 4),
        }
        if meets_quality:
            eligible.append((utility, tier))
    if eligible:
        return outcomes, min(eligible)[1]
    fallback = max(candidates, key=lambda tier: (float(outcomes[tier]["quality_score"]), levels[tier]))
    return outcomes, fallback


def train_route_mlp(
    rows: list[dict[str, object]],
    *,
    hidden_dim: int = 32,
    epochs: int = 650,
    learning_rate: float = 0.035,
    l2: float = 1e-4,
    batch_size: int = 96,
    seed: int = 20260710,
    train_fraction: float = 0.78,
    calibration_fraction: float = 0.16,
) -> tuple[NeuralRouteModel, dict[str, object], dict[str, list[dict[str, object]]]]:
    x_all, y_all = _rows_to_arrays(rows)
    train_pool_idx, test_idx = _stratified_split(y_all, train_fraction=train_fraction, seed=seed)
    fit_relative_idx, calibration_relative_idx = _stratified_split(
        y_all[train_pool_idx],
        train_fraction=1.0 - calibration_fraction,
        seed=seed + 1,
    )
    train_idx = train_pool_idx[fit_relative_idx]
    calibration_idx = train_pool_idx[calibration_relative_idx]
    x_train, y_train = x_all[train_idx], y_all[train_idx]
    x_calibration, y_calibration = x_all[calibration_idx], y_all[calibration_idx]
    x_test, y_test = x_all[test_idx], y_all[test_idx]
    train_rows = [rows[index] for index in train_idx]
    calibration_rows = [rows[index] for index in calibration_idx]
    test_rows = [rows[index] for index in test_idx]

    rng = np.random.default_rng(seed)
    feature_mean = x_train.mean(axis=0, keepdims=True)
    feature_std = np.maximum(x_train.std(axis=0, keepdims=True), 1e-8)
    x_train_norm = (x_train - feature_mean) / feature_std
    x_calibration_norm = (x_calibration - feature_mean) / feature_std
    x_test_norm = (x_test - feature_mean) / feature_std

    input_dim = x_train.shape[1]
    output_dim = len(LEARNED_ROUTE_LABELS)
    weights1 = rng.normal(0.0, np.sqrt(2.0 / input_dim), size=(input_dim, hidden_dim))
    bias1 = np.zeros((1, hidden_dim), dtype=float)
    weights2 = rng.normal(0.0, np.sqrt(2.0 / hidden_dim), size=(hidden_dim, output_dim))
    bias2 = np.zeros((1, output_dim), dtype=float)

    loss_history: list[dict[str, float]] = []
    for epoch in range(1, epochs + 1):
        order = rng.permutation(len(x_train_norm))
        for start in range(0, len(order), batch_size):
            batch_idx = order[start : start + batch_size]
            xb = x_train_norm[batch_idx]
            yb = y_train[batch_idx]
            y_one_hot = _one_hot(yb, output_dim)

            hidden_pre = xb @ weights1 + bias1
            hidden = np.maximum(0.0, hidden_pre)
            logits = hidden @ weights2 + bias2
            probs = _softmax(logits)

            grad_logits = (probs - y_one_hot) / len(xb)
            grad_weights2 = hidden.T @ grad_logits + l2 * weights2
            grad_bias2 = grad_logits.sum(axis=0, keepdims=True)
            grad_hidden = grad_logits @ weights2.T
            grad_hidden_pre = grad_hidden * (hidden_pre > 0.0)
            grad_weights1 = xb.T @ grad_hidden_pre + l2 * weights1
            grad_bias1 = grad_hidden_pre.sum(axis=0, keepdims=True)

            weights1 -= learning_rate * grad_weights1
            bias1 -= learning_rate * grad_bias1
            weights2 -= learning_rate * grad_weights2
            bias2 -= learning_rate * grad_bias2

        if epoch == 1 or epoch % 50 == 0 or epoch == epochs:
            train_probs = _forward(x_train_norm, weights1, bias1, weights2, bias2)
            test_probs = _forward(x_test_norm, weights1, bias1, weights2, bias2)
            loss_history.append(
                {
                    "epoch": float(epoch),
                    "train_loss": _cross_entropy(y_train, train_probs),
                    "test_loss": _cross_entropy(y_test, test_probs),
                    "train_accuracy": _accuracy(y_train, train_probs.argmax(axis=1)),
                    "test_accuracy": _accuracy(y_test, test_probs.argmax(axis=1)),
                }
            )

    calibration_logits = _forward_logits(x_calibration_norm, weights1, bias1, weights2, bias2)
    calibration_temperature = _fit_temperature_scaling(y_calibration, calibration_logits)
    ood_threshold = _fit_ood_threshold(x_train_norm)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_source": "mixed_route_training",
        "samples_total": len(rows),
        "train_samples": int(len(train_idx)),
        "calibration_samples": int(len(calibration_idx)),
        "test_samples": int(len(test_idx)),
        "dataset_distribution": _row_distribution(rows),
        "hidden_dim": hidden_dim,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "l2": l2,
        "seed": seed,
    }
    model = NeuralRouteModel(
        labels=tuple(LEARNED_ROUTE_LABELS),
        feature_names=feature_names(),
        feature_mean=feature_mean.reshape(-1),
        feature_std=feature_std.reshape(-1),
        weights1=weights1,
        bias1=bias1.reshape(-1),
        weights2=weights2,
        bias2=bias2.reshape(-1),
        calibration_temperature=calibration_temperature,
        ood_threshold=ood_threshold,
        metadata=metadata,
    )
    train_probs = _predict_probs(model, x_train)
    calibration_probs = _predict_probs(model, x_calibration)
    test_probs = _predict_probs(model, x_test)
    raw_calibration_probs = _softmax(calibration_logits)
    raw_test_probs = _softmax(_forward_logits(x_test_norm, weights1, bias1, weights2, bias2))
    probe_rows = build_route_probe_cases()
    x_probe, y_probe = _rows_to_arrays(probe_rows)
    probe_probs = _predict_probs(model, x_probe)
    metrics = {
        "schema_version": "llm-route-mlp-metrics/v1",
        "metadata": metadata,
        "train": _classification_metrics(y_train, train_probs),
        "calibration_split": _classification_metrics(y_calibration, calibration_probs),
        "test": _classification_metrics(y_test, test_probs),
        "probe": _classification_metrics(y_probe, probe_probs),
        "calibration": {
            "method": "temperature_scaling",
            "temperature": calibration_temperature,
            "ood_threshold": ood_threshold,
            "calibration_samples": int(len(calibration_idx)),
            "calibration_before": _calibration_metrics(y_calibration, raw_calibration_probs),
            "calibration_after": _calibration_metrics(y_calibration, calibration_probs),
            "test_before": _calibration_metrics(y_test, raw_test_probs),
            "test_after": _calibration_metrics(y_test, test_probs),
        },
        "loss_history": loss_history,
    }
    return model, metrics, {
        "train": train_rows,
        "calibration": calibration_rows,
        "test": test_rows,
        "probe": probe_rows,
    }


def write_experiment_outputs(
    *,
    model: NeuralRouteModel,
    metrics: dict[str, object],
    splits: dict[str, list[dict[str, object]]],
    output_dir: Path,
    write_splits: bool = False,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.json"
    metrics_path = output_dir / "metrics.json"
    report_path = output_dir / "report.md"

    model.save(model_path)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_metrics_markdown(metrics, model_path=_portable_path(model_path)), encoding="utf-8")
    outputs = {
        "model": str(model_path),
        "metrics": str(metrics_path),
        "report": str(report_path),
    }
    if write_splits:
        train_path = output_dir / "train.jsonl"
        calibration_path = output_dir / "calibration.jsonl"
        test_path = output_dir / "test.jsonl"
        probe_path = output_dir / "probe.jsonl"
        _write_jsonl(train_path, splits["train"])
        _write_jsonl(calibration_path, splits["calibration"])
        _write_jsonl(test_path, splits["test"])
        _write_jsonl(probe_path, splits["probe"])
        outputs.update(
            {
                "train": str(train_path),
                "calibration": str(calibration_path),
                "test": str(test_path),
                "probe": str(probe_path),
            }
        )
    return outputs


def render_metrics_markdown(metrics: dict[str, object], *, model_path: str | Path) -> str:
    test = metrics["test"]  # type: ignore[index]
    train = metrics["train"]  # type: ignore[index]
    metadata = metrics["metadata"]  # type: ignore[index]
    lines = [
        "# LLM Route MLP Benchmark",
        "",
        f"Model artifact: `{model_path}`",
        "",
        "## Summary",
        "",
        f"Training source: `{metadata.get('training_source', 'unknown')}`",
        "",
        (
            f"Synthetic samples: `{metadata.get('synthetic_samples', 'n/a')}` · "
            f"simulated production telemetry: `{metadata.get('simulated_telemetry_samples', 'n/a')}` · "
            f"real telemetry: `{metadata.get('telemetry_samples', 'n/a')}`"
        ),
        "",
        "> Simulated production telemetry is an offline proxy dataset and is not real user traffic.",
        "",
        "| Split | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 | Top-2 accuracy | Log loss | ECE | Brier |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        _summary_row("train", train),
        _summary_row("calibration", metrics["calibration_split"]),  # type: ignore[index]
        _summary_row("test", test),
        _summary_row("probe", metrics["probe"]),  # type: ignore[index]
        "",
    ]
    calibration = metrics["calibration"]  # type: ignore[index]
    lines.extend(
        [
            "## Confidence calibration",
            "",
            f"Method: `{calibration['method']}` · fitted temperature: `{calibration['temperature']:.4f}` · "
            f"OOD threshold: `{calibration['ood_threshold']:.4f}`",
            "",
            "| Evaluation | ECE before | ECE after | Brier before | Brier after |",
            "| --- | ---: | ---: | ---: | ---: |",
            (
                f"| calibration split | {calibration['calibration_before']['expected_calibration_error']:.4f} | "
                f"{calibration['calibration_after']['expected_calibration_error']:.4f} | "
                f"{calibration['calibration_before']['brier_score']:.4f} | "
                f"{calibration['calibration_after']['brier_score']:.4f} |"
            ),
            (
                f"| frozen test | {calibration['test_before']['expected_calibration_error']:.4f} | "
                f"{calibration['test_after']['expected_calibration_error']:.4f} | "
                f"{calibration['test_before']['brier_score']:.4f} | "
                f"{calibration['test_after']['brier_score']:.4f} |"
            ),
            "",
        ]
    )
    lines.extend(_classification_markdown_section("Test", test))
    lines.extend(_classification_markdown_section("Probe", metrics["probe"]))  # type: ignore[arg-type,index]
    lines.extend(
        [
            "",
            "## Training metadata",
            "",
            "```json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a tiny MLP for learned LLM route recommendation.")
    parser.add_argument("--samples-per-class", type=int, default=800)
    parser.add_argument("--epochs", type=int, default=850)
    parser.add_argument("--hidden-dim", type=int, default=48)
    parser.add_argument("--learning-rate", type=float, default=0.028)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--output-dir", type=Path, default=BACKEND_ROOT / "models" / "llm_route_mlp")
    parser.add_argument("--include-telemetry", action="store_true", help="Mix privacy-safe real LLM routing telemetry into training.")
    parser.add_argument("--telemetry-only", action="store_true", help="Train only from telemetry rows; useful once enough real data exists.")
    parser.add_argument("--telemetry-path", type=Path, default=BACKEND_ROOT / "outputs" / "logs" / "events.jsonl")
    parser.add_argument("--telemetry-max-rows", type=int, default=2500)
    parser.add_argument("--include-failed-telemetry", action="store_true", help="Include failed LLM calls as noisy observed labels.")
    parser.add_argument(
        "--simulated-telemetry-samples",
        type=int,
        default=0,
        help="Add explicitly labeled production-like simulated observations; these are never reported as real traffic.",
    )
    parser.add_argument("--write-splits", action="store_true", help="Persist reproducible train/test/probe JSONL files for audit.")
    args = parser.parse_args()

    telemetry_rows = (
        load_telemetry_route_dataset(
            args.telemetry_path,
            max_rows=args.telemetry_max_rows,
            include_failed=args.include_failed_telemetry,
        )
        if args.include_telemetry or args.telemetry_only
        else []
    )
    synthetic_rows = [] if args.telemetry_only else build_synthetic_route_dataset(samples_per_class=args.samples_per_class, seed=args.seed)
    simulated_rows = build_simulated_production_telemetry(
        samples=max(0, args.simulated_telemetry_samples),
        seed=args.seed + 101,
    )
    rows = [*synthetic_rows, *simulated_rows, *telemetry_rows]
    if not rows:
        raise SystemExit("No MLP training rows available. Generate synthetic rows or collect telemetry first.")
    model, metrics, splits = train_route_mlp(
        rows,
        hidden_dim=args.hidden_dim,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )
    if args.telemetry_only:
        training_source = "telemetry_only"
    elif telemetry_rows and simulated_rows:
        training_source = "synthetic_plus_simulated_plus_real_telemetry"
    elif telemetry_rows:
        training_source = "synthetic_plus_real_telemetry"
    elif simulated_rows:
        training_source = "synthetic_plus_simulated_production_telemetry"
    else:
        training_source = "hard_synthetic_llm_route_cases"
    metrics["metadata"]["training_source"] = training_source
    metrics["metadata"]["synthetic_samples"] = len(synthetic_rows)
    metrics["metadata"]["simulated_telemetry_samples"] = len(simulated_rows)
    metrics["metadata"]["simulated_telemetry_is_real"] = False
    metrics["metadata"]["telemetry_samples"] = len(telemetry_rows)
    metrics["metadata"]["telemetry_path"] = _portable_path(args.telemetry_path)
    model.metadata.update(metrics["metadata"])  # keep model.json metadata aligned with metrics.json.
    outputs = write_experiment_outputs(
        model=model,
        metrics=metrics,
        splits=splits,
        output_dir=args.output_dir,
        write_splits=args.write_splits,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "outputs": outputs,
                "synthetic_samples": len(synthetic_rows),
                "simulated_telemetry_samples": len(simulated_rows),
                "telemetry_samples": len(telemetry_rows),
                "dataset_distribution": metrics["metadata"]["dataset_distribution"],  # type: ignore[index]
                "train": metrics["train"],
                "test": metrics["test"],
                "probe": metrics["probe"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _sample_case(label: str, case_index: int, rng: np.random.Generator) -> dict[str, object]:
    difficulty = str(rng.choice(["clean", "mixed", "adversarial", "long_noise"], p=[0.42, 0.32, 0.16, 0.10]))
    filler = _filler(rng, int(rng.integers(8, 90 if difficulty != "clean" else 45)))
    if label == "fast":
        system_prompt = rng.choice(
            [
                "Answer briefly in Chinese.",
                "Compress this memory into a concise summary.",
                "You suggest one short next prompt.",
                "Explain a term without doing tool calls, code repair, or simulation.",
            ]
        )
        user_prompt = rng.choice(
            [
                f"你好，请用一句话介绍这个系统。{filler}",
                f"把这段对话压缩成 120 字以内的长期记忆。{filler}",
                f"推荐一个下一步追问，不要回答问题本身。{filler}",
                f"请解释一个普通概念，保持简短。{filler}",
                f"只解释 LAMMPS 是什么，不要生成脚本、不要运行模拟。{filler}",
                f"用一句话解释 RAG 的 Hit@5，不要检索文献。{filler}",
                f"解释 image_url 这个字段的含义，但没有真实图片要分析。{filler}",
            ]
        )
        capability = rng.choice(["chat", "memory.summary", "prompt.suggest", "conversation.answer"])
        max_tokens = int(rng.integers(120, 950))
        temperature = float(rng.choice([0.1, 0.2, 0.45]))
        multimodal = False
    elif label == "balanced":
        system_prompt = rng.choice(
            [
                "You are a supervisor router. Return structured JSON.",
                "Rewrite the query for RAG retrieval and cite relevant context.",
                "Summarize literature evidence with conservative citations.",
                "Classify intent and prepare retrieval context; do not generate executable code.",
            ]
        )
        user_prompt = rng.choice(
            [
                f"请做 query rewrite，结合 RAG retrieval 和 citation 线索。{filler}",
                f"Choose exactly one route_name for this materials request and return JSON schema. {filler}",
                f"根据文献和引用上下文，整理一个平衡的研究摘要。{filler}",
                f"评估这个 benchmark case 的规则指标和引用覆盖率。{filler}",
                f"这段用户问题里提到 Python code，但只需要判别 intent 和 route_name，不要修复。{filler}",
                f"给 RAG 生成 3 个检索 query，输入里包含 LAMMPS、phase diagram 和 citation 噪声。{filler}",
                f"做 literature triage：判断需要哪些 evidence，不要做 Red/Blue 修复。{filler}",
            ]
        )
        capability = rng.choice(["supervisor.router", "rag.query_rewrite", "literature.citation", "benchmark.evaluate", "routing.intent"])
        max_tokens = int(rng.integers(650, 2600))
        temperature = float(rng.choice([0.05, 0.1, 0.2]))
        multimodal = False
    elif label == "strong":
        system_prompt = rng.choice(
            [
                "Answer or route safely.",
                "You repair and review a structured LAMMPS request. Return JSON only.",
                "Return runnable Python code only for phase diagram calculation.",
                "You are a Red-Blue reviewer checking factuality, logic, and physical consistency.",
                "Perform high-risk repair or judge; preserve locked scientific constraints.",
            ]
        )
        user_prompt = rng.choice(
            [
                "LAMMPS Cu EAM NPT failed. Use MODIFY and VERIFY patch.",
                "LAMMPS timestep instability. Repair request JSON.",
                "NVT molecular dynamics failed. Check EAM potential and thermo.csv.",
                "Fix LAMMPS input script after execution error.",
                "用户只写了：跑 LAMMPS 报错了，帮我修。需要先保守生成 repair JSON。",
                "对一段相图 Python wrapper 做安全审查：不能删除 pycalphad/TDB 约束。",
                f"LAMMPS Cu EAM NPT molecular dynamics failed with timestep instability. Use ADD DELETE MODIFY VERIFY patch. {filler}",
                f"修复这段 Python code traceback，并保持 pycalphad/TDB 相图计算约束。{filler}",
                f"审查 LAMMPS thermo.csv、dump 和 potential 设置，判断 NVT/NPT 物理一致性。{filler}",
                f"对 Red Blue review 结果做 judge 评分，检查事实性、逻辑一致性和引用质量。{filler}",
            ]
        )
        capability = rng.choice(["lammps.review", "lammps.repair", "phase.codegen", "judge.review"])
        max_tokens = int(rng.integers(700, 4300))
        temperature = float(rng.choice([0.0, 0.05, 0.1]))
        multimodal = False
    else:
        system_prompt = rng.choice(
            [
                "Analyze this multimodal phase diagram image.",
                "You are a RecognitionAgent for screenshots and image_url inputs.",
                "Extract plot regions, axes, labels, and phase fields from data:image input.",
                "Use visual evidence from the uploaded image; text alone is insufficient.",
            ]
        )
        user_prompt = rng.choice(
            [
                f"请识别这张相图截图的坐标轴、相区和关键点。data:image/png;base64,... {filler}",
                f"Analyze image_url and return JSON for plot region reconstruction. {filler}",
                f"多模态图像解析：识别 phase diagram 图片中的 labels、axis 和 curves。{filler}",
                f"根据 screenshot 生成 recognition result，并给出可交互重建线索。{filler}",
                f"用户上传了 image/png 文件，文本里还提到 LAMMPS，但任务是识别图像坐标轴。{filler}",
                f"从 data:image 的 plot region 中提取 labels；不要假设文字描述足够。{filler}",
            ]
        )
        capability = rng.choice(["vision.recognition", "recognition.analyze", "multimodal.phase_image"])
        max_tokens = int(rng.integers(900, 3600))
        temperature = float(rng.choice([0.05, 0.1, 0.2]))
        multimodal = True

    row = {
        "case_id": f"synthetic_route.{label}.{case_index:04d}",
        "label": label,
        "system_prompt": str(system_prompt),
        "user_prompt": str(user_prompt),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "capability": str(capability),
        "multimodal": multimodal,
        "difficulty": difficulty,
    }
    return _harden_case(row, rng)


def _harden_case(row: dict[str, object], rng: np.random.Generator) -> dict[str, object]:
    label = str(row["label"])
    difficulty = str(row.get("difficulty") or "clean")
    if difficulty == "clean":
        return row

    row = {**row}
    distractor = _distractor_for(label, rng)
    if difficulty in {"mixed", "long_noise"}:
        row["user_prompt"] = f"{row['user_prompt']}\n\nAdditional noisy context:\n{distractor}"
    if difficulty == "adversarial":
        row["system_prompt"] = f"{row['system_prompt']} {_adversarial_instruction(label, rng)}"
        row["user_prompt"] = f"{distractor}\n\nActual task:\n{row['user_prompt']}"
        if rng.random() < 0.36:
            row["capability"] = _misleading_capability(label, rng)
    if difficulty == "long_noise":
        row["user_prompt"] = (
            f"{row['user_prompt']}\n\n"
            f"Long unrelated conversation history:\n{_filler(rng, int(rng.integers(160, 420)))}"
        )
        row["max_tokens"] = int(max(int(row["max_tokens"]), rng.integers(1100, 3600)))
    if label == "vision":
        row["multimodal"] = True
    return row


def build_route_challenge_cases(seed: int = 20260727, repeats: int = 6) -> list[dict[str, object]]:
    rng = np.random.default_rng(seed)
    base = [
        _probe(
            "challenge.fast.lammps_concept",
            "fast",
            "Explain only; do not call tools.",
            "LAMMPS、NVT、NPT 分别是什么意思？不要写脚本，不要运行模拟，只做概念解释。",
            420,
            0.2,
            "chat",
            False,
            difficulty="adversarial",
        ),
        _probe(
            "challenge.fast.vision_word_no_image",
            "fast",
            "Answer briefly.",
            "解释一下 image_url 字段是什么；这里没有上传图片，也不要做图像识别。",
            360,
            0.1,
            "chat",
            False,
            difficulty="adversarial",
        ),
        _probe(
            "challenge.fast.long_memory_with_code_noise",
            "fast",
            "Compress memory under 180 Chinese characters.",
            "把下面长对话压缩成长期记忆。里面出现 Python traceback、LAMMPS、JSON，但这些都是历史噪声，不要修复。",
            220,
            0.1,
            "memory.summary",
            False,
            difficulty="long_noise",
        ),
        _probe(
            "challenge.balanced.rag_with_lammps_noise",
            "balanced",
            "Rewrite retrieval queries only.",
            "用户问 LAMMPS EAM potential 文献背景，请生成 RAG query 和 citation plan，不要生成 input script。",
            950,
            0.1,
            "rag.query_rewrite",
            False,
            difficulty="mixed",
        ),
        _probe(
            "challenge.balanced.router_with_image_word",
            "balanced",
            "Choose route_name as JSON.",
            "用户说“如果之后上传图片再识别”，但当前没有 image_url；现在只需要 supervisor router 判断下一步。",
            800,
            0.1,
            "supervisor.router",
            False,
            difficulty="adversarial",
        ),
        _probe(
            "challenge.balanced.literature_with_code",
            "balanced",
            "Summarize evidence, no code execution.",
            "文献摘要里包含 code、JSON、benchmark 这些词；任务是 literature citation triage，不是代码修复。",
            1400,
            0.1,
            "literature.citation",
            False,
            difficulty="mixed",
        ),
        _probe(
            "challenge.strong.short_lammps_repair",
            "strong",
            "Repair safely and return JSON.",
            "LAMMPS 跑崩了，thermo.csv 异常，帮我修 input。",
            900,
            0.05,
            "lammps.repair",
            False,
            difficulty="adversarial",
        ),
        _probe(
            "challenge.strong.phase_codegen_short",
            "strong",
            "Return runnable Python only.",
            "给 Al-Zn 生成 pycalphad 相图 wrapper，必须保留 TDB 约束。",
            1800,
            0.05,
            "phase.codegen",
            False,
            difficulty="mixed",
        ),
        _probe(
            "challenge.strong.red_blue_judge",
            "strong",
            "Run Red/Blue quality judge.",
            "审查这个修复 patch 是否违反 locked scientific constraints，并给 ADD/DELETE/MODIFY/VERIFY 结论。",
            1300,
            0.05,
            "judge.review",
            False,
            difficulty="mixed",
        ),
        _probe(
            "challenge.vision.image_plus_lammps_noise",
            "vision",
            "Use the uploaded image evidence.",
            "上传的是相图截图 data:image/png;base64,... 文本里还提到 LAMMPS，但真正任务是识别坐标轴和相区。",
            1500,
            0.1,
            "recognition.analyze",
            True,
            difficulty="mixed",
        ),
        _probe(
            "challenge.vision.screenshot_reconstruct",
            "vision",
            "Extract visual plot geometry.",
            "从 screenshot/image_url 里提取 plot region、labels、curves，输出 reconstruction JSON。",
            1800,
            0.1,
            "vision.recognition",
            True,
            difficulty="mixed",
        ),
    ]
    rows: list[dict[str, object]] = []
    for repeat in range(repeats):
        for item in base:
            row = {**item}
            row["case_id"] = f"{item['case_id']}.r{repeat:02d}"
            if repeat:
                row = _harden_case(row, rng)
            rows.append(row)
    return rows


def build_deployment_wrapper_cases(seed: int = 20260739, repeats: int = 20) -> list[dict[str, object]]:
    """Model the real ChatAgent envelope without letting envelope noise win.

    Production prompts carry memory, tool, RAG, and history sections. The MLP
    should route on the active user message while still seeing total-context
    load. These cases cover both short and long envelopes for every tier.
    """

    rng = np.random.default_rng(seed)
    templates = (
        ("fast", "它和包晶反应有什么区别？请简洁回答。", "chat.answer", 700, False),
        ("balanced", "Fe-C 共析点是什么？请结合知识库依据和引用回答。", "rag.answer", 1200, False),
        ("strong", "LAMMPS NPT 运行失败，请修复 input 并验证物理约束。", "lammps.repair", 1600, False),
        ("vision", "请识别上传的相图截图。data:image/png;base64,...", "recognition.analyze", 1600, True),
    )
    rows: list[dict[str, object]] = []
    for repeat in range(repeats):
        context_words = int(rng.integers(180, 1100))
        noisy_context = _filler(rng, context_words)
        for label, message, capability, max_tokens, multimodal in templates:
            wrapped = (
                f"User message:\n{message}\n\n"
                f"Current summary:\n{noisy_context}\n\n"
                f"Retrieved long-term memory:\nRAG JSON code LAMMPS image_url {noisy_context}\n\n"
                "Shared memory context:\n{}\n\n"
                "Selected skill guidance:\n(none)\n\n"
                "Tool results from this turn:\n(none)\n\n"
                "Conversation history:\n[]"
            )
            rows.append(
                _probe(
                    f"deployment_wrapper.{label}.{repeat:03d}",
                    label,
                    "Answer the active task and respect supplied context boundaries.",
                    wrapped,
                    max_tokens,
                    0.1,
                    capability,
                    multimodal,
                    difficulty="deployment_wrapper",
                )
            )
    return rows


def build_route_probe_cases() -> list[dict[str, object]]:
    wrapper_noise = "RAG JSON code LAMMPS image_url memory tool result historical noise. " * 70
    return [
        _probe("probe.fast.short_chat", "fast", "Answer briefly.", "你好，请用一句话介绍这个系统。", 250, 0.1, "chat", False),
        _probe("probe.fast.memory", "fast", "Compress memory.", "把这轮对话压缩成 120 字长期记忆。", 180, 0.1, "memory.summary", False),
        _probe("probe.fast.prompt", "fast", "Suggest prompt.", "推荐一个下一步追问，不要回答研究问题本身。", 220, 0.45, "prompt.suggest", False),
        _probe("probe.fast.lammps_concept", "fast", "Explain only.", "LAMMPS 是什么？只解释概念，不要写 input script。", 420, 0.2, "chat", False),
        _probe("probe.fast.no_image", "fast", "Answer briefly.", "解释 image_url 字段含义，但当前没有上传图片。", 320, 0.1, "chat", False),
        _probe("probe.fast.memory_noise", "fast", "Compress memory.", "压缩长期记忆：文本里有 traceback 和 JSON 字样，但不要修复。", 220, 0.1, "memory.summary", False),
        _probe("probe.fast.log_format", "fast", "Explain only.", "解释 LAMMPS log 文件每一列大概是什么意思，不要诊断失败原因。", 520, 0.2, "chat", False),
        _probe("probe.fast.filename_image", "fast", "Answer briefly.", "文件名叫 phase_image.png，但我没上传图片，只想问这个文件名是什么意思。", 300, 0.1, "chat", False),
        _probe("probe.fast.prompt_with_rag_noise", "fast", "Suggest prompt.", "给我一个下一步追问，历史里出现 RAG/citation/benchmark 但当前不要检索。", 260, 0.35, "prompt.suggest", False),
        _probe("probe.fast_short_json_definition", "fast", "Explain simply.", "JSON schema 是什么？一句话解释，不要返回 JSON。", 260, 0.1, "chat", False),
        _probe(
            "probe.fast.production_wrapper",
            "fast",
            "Answer the active task.",
            f"User message:\n它和包晶反应有什么区别？\n\nCurrent summary:\n{wrapper_noise}\n\nTool results from this turn:\n(none)\n\nConversation history:\n[]",
            700,
            0.2,
            "chat.answer",
            False,
        ),
        _probe("probe.balanced.router", "balanced", "Route safely.", "Choose exactly one route_name and return JSON schema.", 900, 0.1, "supervisor.router", False),
        _probe("probe.balanced.rag", "balanced", "Rewrite query.", "请做 query rewrite，结合 RAG retrieval 和 citation。", 800, 0.1, "rag.query_rewrite", False),
        _probe("probe.balanced.literature", "balanced", "Evidence summary.", "整理 literature citation 和 benchmark evidence。", 1300, 0.1, "literature.citation", False),
        _probe("probe.balanced.code_noise", "balanced", "Classify intent only.", "用户提到 Python code，但任务只是选择 route_name，不要修复。", 820, 0.1, "supervisor.router", False),
        _probe("probe.balanced.image_future", "balanced", "Route current request.", "用户说之后可能上传图片；当前只做 routing intent JSON。", 760, 0.1, "routing.intent", False),
        _probe("probe.balanced.lammps_literature", "balanced", "Plan evidence.", "为 LAMMPS EAM potential 做文献检索 query，不生成模拟脚本。", 1100, 0.1, "literature.citation", False),
        _probe("probe.balanced.repair_docs_rag", "balanced", "Plan retrieval.", "帮我检索 LAMMPS repair 相关文档和 citation，不要实际修改 input。", 1200, 0.1, "rag.query_rewrite", False),
        _probe("probe.balanced.mixed_route", "balanced", "Choose route.", "用户可能要 conversation.answer、lammps.generate 或 recognition.analyze，请只返回 route JSON。", 1050, 0.1, "supervisor.router", False),
        _probe("probe.balanced_benchmark_metrics", "balanced", "Evaluate benchmark metadata.", "计算 benchmark 指标定义：Hit@k、citation coverage、macro-F1，不调用 judge。", 1500, 0.1, "benchmark.evaluate", False),
        _probe("probe.balanced_phase_query", "balanced", "Rewrite query.", "为 pycalphad TDB 相图资料生成检索 query，不写 Python wrapper。", 900, 0.1, "rag.query_rewrite", False),
        _probe(
            "probe.balanced.production_rag_wrapper",
            "balanced",
            "Ground the active task.",
            f"User message:\nFe-C 共析点是什么？请结合知识库依据回答。\n\nCurrent summary:\n{wrapper_noise}\n\nMaterials RAG context:\nretrieved evidence",
            1200,
            0.1,
            "rag.answer",
            False,
        ),
        _probe("probe.strong.short_lammps", "strong", "Answer or route safely.", "LAMMPS Cu EAM NPT failed. Use MODIFY and VERIFY patch.", 900, 0.1, "lammps.review", False),
        _probe("probe.strong.repair", "strong", "Repair safely.", "Fix LAMMPS input script after execution error and check thermo.csv.", 1200, 0.1, "lammps.repair", False),
        _probe("probe.strong.codegen", "strong", "Return runnable Python code only.", "生成 pycalphad TDB phase diagram wrapper code。", 1800, 0.1, "phase.codegen", False),
        _probe("probe.strong.judge", "strong", "Judge quality.", "Red Blue review judge：检查事实性、逻辑一致性和引用质量。", 1100, 0.1, "judge.review", False),
        _probe("probe.strong.tiny_repair", "strong", "Repair JSON.", "LAMMPS input 报错，帮我修。", 800, 0.05, "lammps.repair", False),
        _probe("probe.strong.locked_constraints", "strong", "Review patch.", "检查 patch 是否修改 locked material/temperature/steps 约束。", 1000, 0.05, "judge.review", False),
        _probe("probe.strong.traceback", "strong", "Return runnable Python only.", "修复相图代码 traceback，保持 pycalphad helper。", 1600, 0.05, "phase.codegen", False),
        _probe("probe.strong.short_modify", "strong", "Repair JSON.", "MODIFY time_step；NPT 不稳定。", 760, 0.05, "lammps.repair", False),
        _probe("probe.strong_json_fallback", "strong", "Repair malformed advisory.", "Red review JSON 解析失败，需要三层 fallback 修复并保持 VERIFY 证据。", 1300, 0.05, "review.repair", False),
        _probe("probe.strong_physical_gate", "strong", "Judge physical consistency.", "检查 thermo.csv 温度漂移、EAM potential 和 ensemble 是否物理一致。", 1350, 0.05, "lammps.review", False),
        _probe("probe.strong_code_security", "strong", "Return safe Python patch.", "修复 Python wrapper，但禁止 subprocess、网络访问和删除文件。", 1600, 0.05, "phase.codegen", False),
        _probe("probe.vision.image", "vision", "Analyze image.", "请识别这张相图截图的坐标轴和相区。data:image/png;base64,...", 900, 0.1, "recognition.analyze", True),
        _probe("probe.vision.reconstruct", "vision", "Analyze image_url.", "Extract plot region and labels from image_url for reconstruction.", 1400, 0.1, "vision.recognition", True),
        _probe("probe.vision_lammps_noise", "vision", "Use visual evidence.", "上传 image/png 相图截图；文本里提到 LAMMPS 只是噪声，请识别相区。", 1300, 0.1, "recognition.analyze", True),
        _probe("probe.vision_json", "vision", "Analyze screenshot.", "从 screenshot 提取 plot region、axis labels、curves，返回 JSON。", 1700, 0.1, "multimodal.phase_image", True),
        _probe("probe.vision_future_html", "vision", "Analyze uploaded image first.", "根据上传图片识别结果生成交互式 HTML；第一步需要视觉识别。data:image/png;base64,...", 1900, 0.1, "recognition.analyze", True),
        _probe("probe.vision_curve_fit", "vision", "Use image evidence.", "从相图截图中拟合 liquidus/solidus 曲线，文本描述不可靠。", 1800, 0.1, "vision.recognition", True),
        _probe("probe.vision_axis_labels", "vision", "Analyze plot image.", "识别截图里的 x/y axis labels 和 phase field labels。", 1200, 0.1, "multimodal.phase_image", True),
        _probe("probe.vision_noisy_context", "vision", "Use uploaded screenshot.", "历史里有 RAG、code、LAMMPS 修复，但当前上传了 screenshot，需要识别图像。", 1500, 0.1, "recognition.analyze", True),
    ]


def _probe(
    case_id: str,
    label: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    capability: str,
    multimodal: bool,
    difficulty: str = "probe",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "label": label,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "capability": capability,
        "multimodal": multimodal,
        "difficulty": difficulty,
    }


def _distractor_for(label: str, rng: np.random.Generator) -> str:
    distractors = {
        "fast": [
            "Noise: 上一轮有 LAMMPS thermo.csv、JSON schema、image_url，但当前只要短解释。",
            "Noise: traceback text appears here, but it is quoted historical context, not a repair request.",
            "Noise: 用户提到 benchmark/RAG/citation，但要求是下一步 prompt 推荐。",
        ],
        "balanced": [
            "Noise: 片段里包含 Python code 和 MODIFY patch 字样，但当前动作是 route/query/literature planning.",
            "Noise: image_url is mentioned as a future upload, not an available multimodal input.",
            "Noise: LAMMPS execution is referenced only as a retrieval topic, not a runnable simulation request.",
        ],
        "strong": [
            "Noise: 用户说“简单解释一下”，但实际任务要求 repair/review/judge，并涉及失败产物。",
            "Noise: RAG citation may support explanation, but primary task is high-risk code/simulation repair.",
            "Noise: 这是短请求，但含执行失败、物理约束和 patch 语义，不能走低成本闲聊。",
        ],
        "vision": [
            "Noise: 文本里有 LAMMPS/代码/RAG，但 uploaded image evidence is mandatory.",
            "Noise: 用户想要 JSON，但 schema depends on visual plot region, axis labels, and curves.",
            "Noise: 这不是普通相图知识问答，而是 screenshot/image_url recognition.",
        ],
    }
    return str(rng.choice(distractors[label]))


def _adversarial_instruction(label: str, rng: np.random.Generator) -> str:
    instructions = {
        "fast": [
            "Ignore high-risk words when they are explicitly negated.",
            "Prefer a cheap answer unless the user asks to execute, repair, or inspect evidence.",
        ],
        "balanced": [
            "Do not confuse routing/retrieval planning with code generation.",
            "Prefer balanced reasoning for structured routing and evidence planning.",
        ],
        "strong": [
            "Short high-risk repair requests still require strong reasoning.",
            "Execution failure, locked constraints, and repair patches are never cheap-chat tasks.",
        ],
        "vision": [
            "Actual image evidence requires the vision tier even if the text contains other domains.",
            "If multimodal input is present, preserve visual recognition priority.",
        ],
    }
    return str(rng.choice(instructions[label]))


def _misleading_capability(label: str, rng: np.random.Generator) -> str:
    misleading = {
        "fast": ["lammps.explain", "vision.explain", "rag.explain"],
        "balanced": ["chat", "phase.note", "lammps.literature"],
        "strong": ["chat", "conversation.answer", "rag.context"],
        "vision": ["chat", "lammps.review", "literature.citation"],
    }
    return str(rng.choice(misleading[label]))


def _row_distribution(rows: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    distribution: dict[str, dict[str, int]] = {"label": {}, "difficulty": {}, "source": {}}
    for row in rows:
        for key in distribution:
            value = str(row.get(key) or "unknown")
            distribution[key][value] = distribution[key].get(value, 0) + 1
    return distribution


def _feature_values_from_telemetry(record: dict[str, Any]) -> tuple[float, ...] | None:
    values_by_name = record.get("feature_values")
    if isinstance(values_by_name, dict):
        try:
            return tuple(float(values_by_name[name]) for name in FEATURE_NAMES)
        except (KeyError, TypeError, ValueError):
            return None

    values_by_name = record.get("feature_values_by_name")
    if isinstance(values_by_name, dict):
        try:
            return tuple(float(values_by_name[name]) for name in FEATURE_NAMES)
        except (KeyError, TypeError, ValueError):
            return None

    values = record.get("feature_values")
    if isinstance(values, list) and len(values) == len(FEATURE_NAMES):
        try:
            return tuple(float(value) for value in values)
        except (TypeError, ValueError):
            return None
    return None


def _feature_values_from_row(row: dict[str, object]) -> tuple[float, ...]:
    values_payload = row.get("feature_values")
    if isinstance(values_payload, dict):
        return tuple(float(values_payload[name]) for name in FEATURE_NAMES)
    if isinstance(values_payload, (list, tuple)) and len(values_payload) == len(FEATURE_NAMES):
        return tuple(float(value) for value in values_payload)

    features = extract_route_features(
        system_prompt=str(row["system_prompt"]),
        user_prompt=str(row["user_prompt"]),
        max_tokens=int(row["max_tokens"]),
        temperature=float(row["temperature"]),
        capability=str(row["capability"]),
        multimodal=bool(row["multimodal"]),
    )
    return features.values


def _rows_to_arrays(rows: list[dict[str, object]]) -> tuple[np.ndarray, np.ndarray]:
    x_values: list[tuple[float, ...]] = []
    y_values: list[int] = []
    for row in rows:
        x_values.append(_feature_values_from_row(row))
        y_values.append(LABEL_TO_ID[str(row["label"])])
    return np.asarray(x_values, dtype=float), np.asarray(y_values, dtype=int)


def _stratified_split(y: np.ndarray, *, train_fraction: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    train_indices: list[int] = []
    test_indices: list[int] = []
    for class_id in sorted(set(int(value) for value in y)):
        indices = np.flatnonzero(y == class_id)
        rng.shuffle(indices)
        split_at = max(1, min(len(indices) - 1, int(round(len(indices) * train_fraction))))
        train_indices.extend(indices[:split_at].tolist())
        test_indices.extend(indices[split_at:].tolist())
    rng.shuffle(train_indices)
    rng.shuffle(test_indices)
    return np.asarray(train_indices, dtype=int), np.asarray(test_indices, dtype=int)


def _classification_metrics(y_true: np.ndarray, probs: np.ndarray) -> dict[str, object]:
    y_pred = probs.argmax(axis=1)
    matrix = _confusion_matrix(y_true, y_pred, len(LEARNED_ROUTE_LABELS))
    per_class: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    precision_values: list[float] = []
    recall_values: list[float] = []
    supports: list[int] = []
    weighted_f1 = 0.0
    for class_id, label in enumerate(LEARNED_ROUTE_LABELS):
        tp = float(matrix[class_id, class_id])
        fp = float(matrix[:, class_id].sum() - tp)
        fn = float(matrix[class_id, :].sum() - tp)
        support = int(matrix[class_id, :].sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": support}
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)
        supports.append(support)
        weighted_f1 += f1 * support
    total = max(1, int(sum(supports)))
    top2 = np.argsort(probs, axis=1)[:, -2:]
    top2_hits = float(np.mean([truth in top2_row for truth, top2_row in zip(y_true, top2, strict=True)]))
    return {
        "accuracy": _accuracy(y_true, y_pred),
        "balanced_accuracy": float(np.mean(recall_values)),
        "macro_precision": float(np.mean(precision_values)),
        "macro_recall": float(np.mean(recall_values)),
        "macro_f1": float(np.mean(f1_values)),
        "weighted_f1": float(weighted_f1 / total),
        "top2_accuracy": top2_hits,
        "log_loss": _cross_entropy(y_true, probs),
        **_calibration_metrics(y_true, probs),
        "confusion_matrix": matrix.astype(int).tolist(),
        "per_class": per_class,
    }


def _predict_probs(model: NeuralRouteModel, x_raw: np.ndarray) -> np.ndarray:
    x_norm = (x_raw - model.feature_mean) / model.feature_std
    logits = _forward_logits(
        x_norm,
        model.weights1,
        model.bias1.reshape(1, -1),
        model.weights2,
        model.bias2.reshape(1, -1),
    )
    return _softmax(logits / model.calibration_temperature)


def _forward(
    x_norm: np.ndarray,
    weights1: np.ndarray,
    bias1: np.ndarray,
    weights2: np.ndarray,
    bias2: np.ndarray,
) -> np.ndarray:
    return _softmax(_forward_logits(x_norm, weights1, bias1, weights2, bias2))


def _forward_logits(
    x_norm: np.ndarray,
    weights1: np.ndarray,
    bias1: np.ndarray,
    weights2: np.ndarray,
    bias2: np.ndarray,
) -> np.ndarray:
    hidden = np.maximum(0.0, x_norm @ weights1 + bias1)
    return hidden @ weights2 + bias2


def _fit_temperature_scaling(y_true: np.ndarray, logits: np.ndarray) -> float:
    candidates = np.exp(np.linspace(np.log(0.35), np.log(6.0), 420))
    losses = np.asarray([_cross_entropy(y_true, _softmax(logits / temperature)) for temperature in candidates])
    return float(candidates[int(np.argmin(losses))])


def _fit_ood_threshold(x_train_norm: np.ndarray) -> float:
    top_count = min(5, x_train_norm.shape[1])
    scores = []
    for row in np.abs(x_train_norm):
        top_values = np.partition(row, -top_count)[-top_count:]
        scores.append(float(np.sqrt(np.mean(np.square(np.minimum(top_values, 25.0))))))
    return float(max(3.0, np.quantile(np.asarray(scores, dtype=float), 0.995)))


def _calibration_metrics(y_true: np.ndarray, probs: np.ndarray, bins: int = 12) -> dict[str, float]:
    confidences = probs.max(axis=1)
    predictions = probs.argmax(axis=1)
    correctness = (predictions == y_true).astype(float)
    expected_calibration_error = 0.0
    bin_edges = np.linspace(0.0, 1.0, bins + 1)
    for index in range(bins):
        lower = bin_edges[index]
        upper = bin_edges[index + 1]
        mask = (confidences > lower) & (confidences <= upper)
        if index == 0:
            mask = (confidences >= lower) & (confidences <= upper)
        if not np.any(mask):
            continue
        expected_calibration_error += float(np.mean(mask)) * abs(
            float(np.mean(correctness[mask])) - float(np.mean(confidences[mask]))
        )
    one_hot = _one_hot(y_true, probs.shape[1])
    brier_score = float(np.mean(np.sum(np.square(probs - one_hot), axis=1)))
    return {
        "expected_calibration_error": float(expected_calibration_error),
        "brier_score": brier_score,
        "mean_confidence": float(np.mean(confidences)),
    }


def _cross_entropy(y_true: np.ndarray, probs: np.ndarray) -> float:
    clipped = np.clip(probs[np.arange(len(y_true)), y_true], 1e-12, 1.0)
    return float(-np.mean(np.log(clipped)))


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(y_true == y_pred))


def _confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, class_count: int) -> np.ndarray:
    matrix = np.zeros((class_count, class_count), dtype=int)
    for truth, pred in zip(y_true, y_pred, strict=True):
        matrix[int(truth), int(pred)] += 1
    return matrix


def _one_hot(y: np.ndarray, class_count: int) -> np.ndarray:
    result = np.zeros((len(y), class_count), dtype=float)
    result[np.arange(len(y)), y] = 1.0
    return result


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def _filler(rng: np.random.Generator, length: int) -> str:
    vocabulary = np.asarray(
        [
            "materials",
            "agent",
            "context",
            "evidence",
            "实验",
            "结构",
            "结果",
            "分析",
            "temperature",
            "phase",
            "workflow",
            "metadata",
            "constraint",
            "quality",
            "run",
            "artifact",
        ],
        dtype=object,
    )
    return " ".join(str(token) for token in rng.choice(vocabulary, size=length, replace=True))


def _classification_markdown_section(title: str, metrics: dict[str, Any]) -> list[str]:
    lines = [
        f"## {title} per-class metrics",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label in LEARNED_ROUTE_LABELS:
        item = metrics["per_class"][label]
        lines.append(
            f"| `{label}` | {item['precision']:.4f} | {item['recall']:.4f} | {item['f1']:.4f} | {item['support']} |"
        )
    lines.extend(
        [
            "",
            f"## {title} confusion matrix",
            "",
            "Rows are true classes; columns are predicted classes.",
            "",
            "| True \\ Pred | " + " | ".join(f"`{label}`" for label in LEARNED_ROUTE_LABELS) + " |",
            "| --- | " + " | ".join("---:" for _ in LEARNED_ROUTE_LABELS) + " |",
        ]
    )
    for label, row in zip(LEARNED_ROUTE_LABELS, metrics["confusion_matrix"], strict=True):
        lines.append(f"| `{label}` | " + " | ".join(str(value) for value in row) + " |")
    lines.append("")
    return lines


def _summary_row(split: str, metrics: dict[str, Any]) -> str:
    return (
        f"| {split} | {metrics['accuracy']:.4f} | {metrics['macro_precision']:.4f} | "
        f"{metrics['macro_recall']:.4f} | {metrics['macro_f1']:.4f} | {metrics['weighted_f1']:.4f} | "
        f"{metrics['top2_accuracy']:.4f} | {metrics['log_loss']:.4f} | "
        f"{metrics['expected_calibration_error']:.4f} | {metrics['brier_score']:.4f} |"
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
