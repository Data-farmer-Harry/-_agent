from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.config import PROJECT_ROOT


LEARNED_ROUTE_LABELS = ("fast", "balanced", "strong", "vision")

_FEATURE_NAMES = (
    "bias_hint",
    "log_total_chars",
    "log_user_chars",
    "max_tokens_norm",
    "temperature",
    "multimodal",
    "json_marker_count",
    "code_marker_count",
    "lammps_marker_count",
    "materials_marker_count",
    "research_marker_count",
    "vision_marker_count",
    "repair_marker_count",
    "judge_marker_count",
    "memory_marker_count",
    "query_marker_count",
    "supervisor_marker_count",
    "long_context_flag",
    "very_long_context_flag",
    "large_generation_flag",
    "cap_memory",
    "cap_prompt",
    "cap_query",
    "cap_rag",
    "cap_literature",
    "cap_supervisor",
    "cap_phase",
    "cap_thermo",
    "cap_lammps",
    "cap_code",
    "cap_repair",
    "cap_review",
    "cap_judge",
    "cap_vision",
)

_MARKERS: dict[str, tuple[str, ...]] = {
    "json": ("json", "schema", "structured", "return only", "return exactly", "model_dump_json", "解析", "结构化"),
    "code": ("python", "code", "代码", "脚本", "traceback", "exception", "debug", "函数", "报错"),
    "lammps": ("lammps", "molecular dynamics", "分子动力学", "eam", "nvt", "npt", "thermo.csv", "timestep", "time_step"),
    "materials": ("phase diagram", "相图", "pycalphad", "tdb", "calphad", "solidus", "liquidus", "共晶", "包晶", "材料"),
    "research": ("rag", "retrieval", "citation", "引用", "literature", "论文", "benchmark", "evaluate", "review", "事实"),
    "vision": ("image_url", "data:image/", "screenshot", "vision", "multimodal", "识别", "截图", "图片", "图像"),
    "repair": ("repair", "fix", "patch", "修复", "modify", "verify", "delete", "add"),
    "judge": ("judge", "评分", "评测", "一致性", "幻觉", "accuracy", "quality"),
    "memory": ("memory", "summary", "compress", "记忆", "摘要", "压缩"),
    "query": ("query", "rewrite", "检索", "改写", "搜索"),
    "supervisor": ("supervisor", "route", "router", "intent", "路由", "意图"),
}

_CAPABILITY_MARKERS: dict[str, tuple[str, ...]] = {
    "memory": ("memory", "summary"),
    "prompt": ("prompt", "suggest"),
    "query": ("query", "rewrite"),
    "rag": ("rag", "retrieval"),
    "literature": ("literature", "citation"),
    "supervisor": ("supervisor", "router", "route"),
    "phase": ("phase", "diagram"),
    "thermo": ("thermo", "calphad"),
    "lammps": ("lammps", "molecular", "simulation"),
    "code": ("code", "codegen"),
    "repair": ("repair", "fix"),
    "review": ("review", "red", "blue"),
    "judge": ("judge", "evaluate"),
    "vision": ("vision", "recognition", "multimodal"),
}

_ROUTING_FOCUS_PREFIXES = (
    "User message:\n",
    "Current user message:\n",
    "Request message:\n",
)
_ROUTING_FOCUS_SUFFIXES = (
    "\n\nCurrent summary:\n",
    "\n\nRetrieved long-term memory:\n",
    "\n\nShared memory context:\n",
    "\n\nSelected skill guidance:\n",
    "\n\nTool results from this turn:\n",
    "\n\nLast run context:\n",
    "\n\nConversation history:\n",
)


@dataclass(frozen=True)
class RouteFeatureVector:
    names: tuple[str, ...]
    values: tuple[float, ...]
    debug: dict[str, object]


@dataclass(frozen=True)
class LearnedPolicyConfig:
    enabled: bool = False
    mode: str = "shadow"
    model_path: str = "backend/models/llm_route_mlp/model.json"
    confidence_threshold: float = 0.62
    min_probability_margin: float = 0.12
    max_normalized_entropy: float = 0.78
    reject_ood: bool = True
    allow_downgrade: bool = False

    def normalized_mode(self) -> str:
        mode = self.mode.strip().lower()
        return mode if mode in {"shadow", "guarded"} else "shadow"


@dataclass(frozen=True)
class LearnedRouteRecommendation:
    tier: str
    confidence: float
    probability_margin: float
    normalized_entropy: float
    ood_score: float
    is_ood: bool
    probabilities: dict[str, float]
    feature_debug: dict[str, object]

    def public_payload(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "confidence": self.confidence,
            "probability_margin": self.probability_margin,
            "normalized_entropy": self.normalized_entropy,
            "ood_score": self.ood_score,
            "is_ood": self.is_ood,
            "probabilities": self.probabilities,
            "feature_debug": self.feature_debug,
        }


def extract_route_features(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    capability: str = "",
    multimodal: bool = False,
) -> RouteFeatureVector:
    text = f"{system_prompt}\n{user_prompt}"
    focus_text = routing_focus_text(user_prompt)
    lowered_focus = focus_text.lower()
    capability_lower = capability.strip().lower().replace("_", ".").replace("-", ".")
    total_chars = len(text)
    user_chars = len(focus_text)
    system_chars = len(system_prompt)

    # Task markers come from the actual request rather than prompt-wrapper
    # headings such as "Memory", "Tool results", or "RAG context". The full
    # prompt length remains a separate load signal.
    marker_counts = {name: _count_markers(lowered_focus, markers) for name, markers in _MARKERS.items()}
    cap_flags = {
        name: float(any(marker in capability_lower for marker in markers))
        for name, markers in _CAPABILITY_MARKERS.items()
    }

    values = (
        1.0,
        _log_norm(total_chars),
        _log_norm(user_chars),
        min(max_tokens, 6000) / 6000.0,
        max(0.0, min(float(temperature), 1.0)),
        float(multimodal),
        _squash_count(marker_counts["json"]),
        _squash_count(marker_counts["code"]),
        _squash_count(marker_counts["lammps"]),
        _squash_count(marker_counts["materials"]),
        _squash_count(marker_counts["research"]),
        _squash_count(marker_counts["vision"]),
        _squash_count(marker_counts["repair"]),
        _squash_count(marker_counts["judge"]),
        _squash_count(marker_counts["memory"]),
        _squash_count(marker_counts["query"]),
        _squash_count(marker_counts["supervisor"]),
        float(total_chars > 6000),
        float(total_chars > 16000),
        float(max_tokens >= 3000),
        cap_flags["memory"],
        cap_flags["prompt"],
        cap_flags["query"],
        cap_flags["rag"],
        cap_flags["literature"],
        cap_flags["supervisor"],
        cap_flags["phase"],
        cap_flags["thermo"],
        cap_flags["lammps"],
        cap_flags["code"],
        cap_flags["repair"],
        cap_flags["review"],
        cap_flags["judge"],
        cap_flags["vision"],
    )
    return RouteFeatureVector(
        names=_FEATURE_NAMES,
        values=tuple(float(value) for value in values),
        debug={
            "total_chars": total_chars,
            "user_chars": user_chars,
            "raw_user_prompt_chars": len(user_prompt),
            "routing_focus_extracted": focus_text != user_prompt,
            "system_chars": system_chars,
            "marker_counts": marker_counts,
            "capability": capability,
            "multimodal": multimodal,
        },
    )


def routing_focus_text(user_prompt: str) -> str:
    """Extract the active user task from a structured agent prompt wrapper."""

    for prefix in _ROUTING_FOCUS_PREFIXES:
        start = user_prompt.find(prefix)
        if start < 0:
            continue
        start += len(prefix)
        end_candidates = [
            position
            for suffix in _ROUTING_FOCUS_SUFFIXES
            if (position := user_prompt.find(suffix, start)) >= 0
        ]
        end = min(end_candidates) if end_candidates else len(user_prompt)
        focused = user_prompt[start:end].strip()
        if focused:
            return focused
    return user_prompt


class NeuralRouteModel:
    """Tiny one-hidden-layer MLP used for local route recommendation."""

    def __init__(
        self,
        *,
        labels: tuple[str, ...],
        feature_names: tuple[str, ...],
        feature_mean: np.ndarray,
        feature_std: np.ndarray,
        weights1: np.ndarray,
        bias1: np.ndarray,
        weights2: np.ndarray,
        bias2: np.ndarray,
        calibration_temperature: float = 1.0,
        ood_threshold: float = 6.0,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.labels = labels
        self.feature_names = feature_names
        self.feature_mean = feature_mean.astype(float)
        self.feature_std = np.maximum(feature_std.astype(float), 1e-8)
        self.weights1 = weights1.astype(float)
        self.bias1 = bias1.astype(float)
        self.weights2 = weights2.astype(float)
        self.bias2 = bias2.astype(float)
        self.calibration_temperature = max(float(calibration_temperature), 1e-4)
        self.ood_threshold = max(float(ood_threshold), 0.0)
        self.metadata = metadata or {}

    def predict_proba(self, features: RouteFeatureVector) -> dict[str, float]:
        if features.names != self.feature_names:
            raise ValueError("Feature schema mismatch for learned route model.")
        x = np.asarray(features.values, dtype=float)[None, :]
        x_norm = (x - self.feature_mean) / self.feature_std
        hidden = np.maximum(0.0, x_norm @ self.weights1 + self.bias1)
        logits = hidden @ self.weights2 + self.bias2
        probs = _softmax(logits / self.calibration_temperature)[0]
        return {label: float(prob) for label, prob in zip(self.labels, probs, strict=True)}

    def ood_score(self, features: RouteFeatureVector) -> float:
        if features.names != self.feature_names:
            raise ValueError("Feature schema mismatch for learned route model.")
        x = np.asarray(features.values, dtype=float)
        standardized = np.abs((x - self.feature_mean) / self.feature_std)
        top_count = min(5, len(standardized))
        top_values = np.partition(standardized, -top_count)[-top_count:]
        return float(np.sqrt(np.mean(np.square(np.minimum(top_values, 25.0)))))

    def recommend(self, features: RouteFeatureVector) -> LearnedRouteRecommendation:
        probabilities = self.predict_proba(features)
        ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
        tier, confidence = ranked[0]
        second_probability = ranked[1][1] if len(ranked) > 1 else 0.0
        probability_margin = max(0.0, confidence - second_probability)
        probability_values = np.asarray(list(probabilities.values()), dtype=float)
        normalized_entropy = float(
            -np.sum(probability_values * np.log(np.clip(probability_values, 1e-12, 1.0)))
            / max(math.log(len(probability_values)), 1e-12)
        )
        ood_score = self.ood_score(features)
        return LearnedRouteRecommendation(
            tier=tier,
            confidence=float(confidence),
            probability_margin=float(probability_margin),
            normalized_entropy=normalized_entropy,
            ood_score=ood_score,
            is_ood=bool(ood_score > self.ood_threshold),
            probabilities=probabilities,
            feature_debug={
                **features.debug,
                "calibration_temperature": self.calibration_temperature,
                "ood_threshold": self.ood_threshold,
            },
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": "llm-route-mlp/v1",
            "labels": list(self.labels),
            "feature_names": list(self.feature_names),
            "feature_mean": self.feature_mean.tolist(),
            "feature_std": self.feature_std.tolist(),
            "hidden_activation": "relu",
            "weights1": self.weights1.tolist(),
            "bias1": self.bias1.tolist(),
            "weights2": self.weights2.tolist(),
            "bias2": self.bias2.tolist(),
            "calibration": {
                "method": "temperature_scaling",
                "temperature": self.calibration_temperature,
                "ood_threshold": self.ood_threshold,
            },
            "metadata": self.metadata,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "NeuralRouteModel":
        if payload.get("schema_version") != "llm-route-mlp/v1":
            raise ValueError("Unsupported learned route model schema.")
        calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {}
        return cls(
            labels=tuple(str(label) for label in payload["labels"]),  # type: ignore[index]
            feature_names=tuple(str(name) for name in payload["feature_names"]),  # type: ignore[index]
            feature_mean=np.asarray(payload["feature_mean"], dtype=float),  # type: ignore[index]
            feature_std=np.asarray(payload["feature_std"], dtype=float),  # type: ignore[index]
            weights1=np.asarray(payload["weights1"], dtype=float),  # type: ignore[index]
            bias1=np.asarray(payload["bias1"], dtype=float),  # type: ignore[index]
            weights2=np.asarray(payload["weights2"], dtype=float),  # type: ignore[index]
            bias2=np.asarray(payload["bias2"], dtype=float),  # type: ignore[index]
            calibration_temperature=float(calibration.get("temperature") or 1.0),
            ood_threshold=float(calibration.get("ood_threshold") or 6.0),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )

    @classmethod
    def load(cls, path: Path) -> "NeuralRouteModel":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Learned route model payload must be a JSON object.")
        return cls.from_payload(payload)


class LearnedRouteRecommender:
    def __init__(self, config: LearnedPolicyConfig) -> None:
        self.config = config
        self.model_path = resolve_project_path(config.model_path)
        self.model: NeuralRouteModel | None = None
        self.load_error = ""
        if config.enabled:
            self._load_model()

    @property
    def available(self) -> bool:
        return self.model is not None

    def _load_model(self) -> None:
        try:
            self.model = NeuralRouteModel.load(self.model_path)
        except Exception as exc:  # noqa: BLE001 - router should degrade safely.
            self.model = None
            self.load_error = str(exc)

    def recommend(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        capability: str = "",
        multimodal: bool = False,
    ) -> LearnedRouteRecommendation | None:
        if self.model is None:
            return None
        features = extract_route_features(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
            multimodal=multimodal,
        )
        return self.model.recommend(features)

    def public_payload(self) -> dict[str, object]:
        return {
            "enabled": self.config.enabled,
            "mode": self.config.normalized_mode(),
            "model_path": str(self.model_path),
            "available": self.available,
            "load_error": self.load_error,
            "confidence_threshold": self.config.confidence_threshold,
            "min_probability_margin": self.config.min_probability_margin,
            "max_normalized_entropy": self.config.max_normalized_entropy,
            "reject_ood": self.config.reject_ood,
            "allow_downgrade": self.config.allow_downgrade,
            "model_metadata": self.model.metadata if self.model else {},
        }


def feature_names() -> tuple[str, ...]:
    return _FEATURE_NAMES


def resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def _count_markers(text: str, markers: tuple[str, ...]) -> int:
    return sum(text.count(marker) for marker in markers)


def _log_norm(value: int) -> float:
    return math.log1p(max(value, 0)) / math.log1p(20000)


def _squash_count(value: int) -> float:
    return min(float(value), 6.0) / 6.0


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)
