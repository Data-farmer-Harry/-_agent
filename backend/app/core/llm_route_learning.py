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
    "log_system_chars",
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


@dataclass(frozen=True)
class RouteFeatureVector:
    names: tuple[str, ...]
    values: tuple[float, ...]
    debug: dict[str, object]


@dataclass(frozen=True)
class LearnedPolicyConfig:
    enabled: bool = False
    mode: str = "shadow"
    model_path: str = "backend/outputs/llm_route_mlp/model.json"
    confidence_threshold: float = 0.62
    allow_downgrade: bool = False

    def normalized_mode(self) -> str:
        mode = self.mode.strip().lower()
        return mode if mode in {"shadow", "guarded"} else "shadow"


@dataclass(frozen=True)
class LearnedRouteRecommendation:
    tier: str
    confidence: float
    probabilities: dict[str, float]
    feature_debug: dict[str, object]

    def public_payload(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "confidence": self.confidence,
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
    lowered = text.lower()
    capability_lower = capability.strip().lower().replace("_", ".").replace("-", ".")
    total_chars = len(text)
    user_chars = len(user_prompt)
    system_chars = len(system_prompt)

    marker_counts = {name: _count_markers(lowered, markers) for name, markers in _MARKERS.items()}
    cap_flags = {
        name: float(any(marker in capability_lower for marker in markers))
        for name, markers in _CAPABILITY_MARKERS.items()
    }

    values = (
        1.0,
        _log_norm(total_chars),
        _log_norm(user_chars),
        _log_norm(system_chars),
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
            "system_chars": system_chars,
            "marker_counts": marker_counts,
            "capability": capability,
            "multimodal": multimodal,
        },
    )


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
        self.metadata = metadata or {}

    def predict_proba(self, features: RouteFeatureVector) -> dict[str, float]:
        if features.names != self.feature_names:
            raise ValueError("Feature schema mismatch for learned route model.")
        x = np.asarray(features.values, dtype=float)[None, :]
        x_norm = (x - self.feature_mean) / self.feature_std
        hidden = np.maximum(0.0, x_norm @ self.weights1 + self.bias1)
        logits = hidden @ self.weights2 + self.bias2
        probs = _softmax(logits)[0]
        return {label: float(prob) for label, prob in zip(self.labels, probs, strict=True)}

    def recommend(self, features: RouteFeatureVector) -> LearnedRouteRecommendation:
        probabilities = self.predict_proba(features)
        tier = max(probabilities, key=probabilities.get)
        return LearnedRouteRecommendation(
            tier=tier,
            confidence=float(probabilities[tier]),
            probabilities=probabilities,
            feature_debug=features.debug,
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
            "metadata": self.metadata,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "NeuralRouteModel":
        if payload.get("schema_version") != "llm-route-mlp/v1":
            raise ValueError("Unsupported learned route model schema.")
        return cls(
            labels=tuple(str(label) for label in payload["labels"]),  # type: ignore[index]
            feature_names=tuple(str(name) for name in payload["feature_names"]),  # type: ignore[index]
            feature_mean=np.asarray(payload["feature_mean"], dtype=float),  # type: ignore[index]
            feature_std=np.asarray(payload["feature_std"], dtype=float),  # type: ignore[index]
            weights1=np.asarray(payload["weights1"], dtype=float),  # type: ignore[index]
            bias1=np.asarray(payload["bias1"], dtype=float),  # type: ignore[index]
            weights2=np.asarray(payload["weights2"], dtype=float),  # type: ignore[index]
            bias2=np.asarray(payload["bias2"], dtype=float),  # type: ignore[index]
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
