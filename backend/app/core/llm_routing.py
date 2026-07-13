from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import CONFIGS_ROOT, settings
from app.core.llm_route_learning import LearnedPolicyConfig, LearnedRouteRecommender, routing_focus_text


DEFAULT_LLM_ROUTING_CONFIG = CONFIGS_ROOT / "llm_routing.json"
LLM_ROUTING_CONFIG_ENV = "PHASE_DIAGRAM_LLM_ROUTING_CONFIG"
TIER_ORDER = ("fast", "balanced", "strong")
VISION_TIER = "vision"


@dataclass(frozen=True)
class LLMRoute:
    """Provider/runtime options for one LLM routing tier.

    Empty fields intentionally inherit the central LLM config. This keeps the
    router safe by default: without a routing config, all tiers still use the
    existing single model and provider.
    """

    model: str = ""
    api_base_url: str = ""
    api_key_env: str = "PHASE_DIAGRAM_LLM_API_KEY"
    timeout_seconds: int | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    enable_thinking: bool | None = None

    def effective_api_base_url(self) -> str:
        return (self.api_base_url or settings.llm_api_base_url).rstrip("/")

    def effective_api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") or settings.llm_api_key

    def effective_model(self) -> str:
        return self.model or settings.llm_model

    def effective_timeout_seconds(self) -> int:
        return int(self.timeout_seconds or settings.llm_request_timeout_seconds)

    def effective_token_budget(self, requested_max_tokens: int) -> int:
        configured_limit = int(self.max_tokens or settings.llm_max_tokens)
        return min(configured_limit, requested_max_tokens)

    def effective_temperature(self, requested_temperature: float) -> float:
        return float(self.temperature if self.temperature is not None else requested_temperature)

    def effective_enable_thinking(self) -> bool:
        return bool(settings.llm_enable_thinking if self.enable_thinking is None else self.enable_thinking)

    def public_payload(self) -> dict[str, object]:
        return {
            "model": self.effective_model(),
            "api_base_url": self.effective_api_base_url(),
            "api_key_env": self.api_key_env,
            "api_key_set": bool(self.effective_api_key()),
            "timeout_seconds": self.effective_timeout_seconds(),
            "max_tokens": int(self.max_tokens or settings.llm_max_tokens),
            "temperature": self.temperature,
            "enable_thinking": self.effective_enable_thinking(),
        }


@dataclass(frozen=True)
class LLMRoutingConfig:
    enabled: bool = True
    fast_max_score: int = 34
    balanced_max_score: int = 69
    max_escalations: int = 1
    fallback_on_error: bool = True
    learned_policy: LearnedPolicyConfig = field(default_factory=LearnedPolicyConfig)
    routes: dict[str, LLMRoute] = field(default_factory=dict)
    fallbacks: dict[str, str] = field(default_factory=dict)
    capability_min_tiers: dict[str, str] = field(default_factory=dict)

    def route_for(self, tier: str) -> LLMRoute:
        return self.routes.get(tier) or self.routes.get("balanced") or LLMRoute()


@dataclass(frozen=True)
class LLMRoutingDecision:
    tier: str
    score: int
    reasons: tuple[str, ...]
    route: LLMRoute
    fallback_tier: str = ""
    capability: str = ""
    escalation_depth: int = 0
    policy_metadata: dict[str, object] = field(default_factory=dict)

    def public_payload(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "score": self.score,
            "reasons": list(self.reasons),
            "fallback_tier": self.fallback_tier,
            "capability": self.capability,
            "escalation_depth": self.escalation_depth,
            "policy_metadata": self.policy_metadata,
            "route": self.route.public_payload(),
        }


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _route_from_payload(payload: Any) -> LLMRoute:
    if not isinstance(payload, dict):
        return LLMRoute()
    return LLMRoute(
        model=str(payload.get("model") or "").strip(),
        api_base_url=str(payload.get("api_base_url") or payload.get("base_url") or "").strip().rstrip("/"),
        api_key_env=str(payload.get("api_key_env") or "PHASE_DIAGRAM_LLM_API_KEY").strip(),
        timeout_seconds=_as_int(payload.get("timeout_seconds"), 0) or None,
        max_tokens=_as_int(payload.get("max_tokens"), 0) or None,
        temperature=_as_float_or_none(payload.get("temperature")),
        enable_thinking=None if "enable_thinking" not in payload else _as_bool(payload.get("enable_thinking"), False),
    )


def _learned_policy_from_payload(payload: Any) -> LearnedPolicyConfig:
    if not isinstance(payload, dict):
        return LearnedPolicyConfig()
    return LearnedPolicyConfig(
        enabled=_as_bool(payload.get("enabled"), False),
        mode=str(payload.get("mode") or "shadow").strip().lower(),
        model_path=str(payload.get("model_path") or "backend/models/llm_route_mlp/model.json").strip(),
        confidence_threshold=max(0.0, min(_as_float(payload.get("confidence_threshold"), 0.62), 1.0)),
        allow_downgrade=_as_bool(payload.get("allow_downgrade"), False),
    )


def _normalize_tier(value: str, default: str = "balanced") -> str:
    tier = value.strip().lower()
    if tier in {*TIER_ORDER, VISION_TIER}:
        return tier
    return default


def _normalize_optional_tier(value: str) -> str:
    tier = value.strip().lower()
    if not tier:
        return ""
    return _normalize_tier(tier)


def load_llm_routing_config(
    config_file: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> LLMRoutingConfig:
    env = os.environ if environ is None else environ
    configured_path = config_file or Path(env.get(LLM_ROUTING_CONFIG_ENV, "") or DEFAULT_LLM_ROUTING_CONFIG)
    if not configured_path.exists():
        return _default_routing_config()

    try:
        raw = json.loads(configured_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_routing_config()
    if not isinstance(raw, dict):
        return _default_routing_config()

    routes_payload = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
    routes = {tier: _route_from_payload(payload) for tier, payload in routes_payload.items() if isinstance(tier, str)}
    if not routes:
        routes = _default_routes()

    fallbacks_payload = raw.get("fallbacks") if isinstance(raw.get("fallbacks"), dict) else {}
    fallbacks = {
        _normalize_tier(str(tier)): _normalize_optional_tier(str(fallback))
        for tier, fallback in fallbacks_payload.items()
        if isinstance(tier, str)
    }
    default_fallbacks = _default_fallbacks()
    default_fallbacks.update({tier: fallback for tier, fallback in fallbacks.items() if tier != fallback})

    capability_payload = raw.get("capability_min_tiers") if isinstance(raw.get("capability_min_tiers"), dict) else {}
    capability_min_tiers = {
        str(capability).strip().lower(): _normalize_tier(str(tier))
        for capability, tier in capability_payload.items()
        if str(capability).strip()
    }
    default_capabilities = _default_capability_min_tiers()
    default_capabilities.update(capability_min_tiers)

    return LLMRoutingConfig(
        enabled=_as_bool(raw.get("enabled"), True),
        fast_max_score=_as_int(raw.get("fast_max_score"), 34),
        balanced_max_score=_as_int(raw.get("balanced_max_score"), 69),
        max_escalations=max(0, _as_int(raw.get("max_escalations"), 1)),
        fallback_on_error=_as_bool(raw.get("fallback_on_error"), True),
        learned_policy=_learned_policy_from_payload(raw.get("learned_policy")),
        routes={**_default_routes(), **routes},
        fallbacks=default_fallbacks,
        capability_min_tiers=default_capabilities,
    )


def _default_routes() -> dict[str, LLMRoute]:
    inherited = LLMRoute()
    return {
        "fast": inherited,
        "balanced": inherited,
        "strong": inherited,
        "vision": inherited,
    }


def _default_fallbacks() -> dict[str, str]:
    return {
        "fast": "balanced",
        "balanced": "strong",
        "strong": "",
        "vision": "strong",
    }


def _default_capability_min_tiers() -> dict[str, str]:
    return {
        "memory": "fast",
        "summary": "fast",
        "prompt": "fast",
        "query": "balanced",
        "rewrite": "balanced",
        "rag": "balanced",
        "literature": "balanced",
        "citation": "balanced",
        "router": "balanced",
        "supervisor": "balanced",
        "phase": "strong",
        "thermo": "strong",
        "code": "strong",
        "codegen": "strong",
        "repair": "strong",
        "review": "strong",
        "judge": "strong",
        "lammps": "strong",
        "molecular": "strong",
        "simulation": "strong",
        "vision": "vision",
        "recognition": "vision",
        "multimodal": "vision",
    }


def _default_routing_config() -> LLMRoutingConfig:
    return LLMRoutingConfig(
        routes=_default_routes(),
        fallbacks=_default_fallbacks(),
        capability_min_tiers=_default_capability_min_tiers(),
    )


def llm_routing_public_payload(config: LLMRoutingConfig | None = None) -> dict[str, object]:
    routing_config = config or load_llm_routing_config()
    learned_recommender = LearnedRouteRecommender(routing_config.learned_policy)
    return {
        "enabled": routing_config.enabled,
        "fast_max_score": routing_config.fast_max_score,
        "balanced_max_score": routing_config.balanced_max_score,
        "fallback_on_error": routing_config.fallback_on_error,
        "max_escalations": routing_config.max_escalations,
        "config_file": str(Path(os.environ.get(LLM_ROUTING_CONFIG_ENV, "") or DEFAULT_LLM_ROUTING_CONFIG)),
        "learned_policy": learned_recommender.public_payload(),
        "routes": {tier: route.public_payload() for tier, route in routing_config.routes.items()},
        "fallbacks": routing_config.fallbacks,
        "capability_min_tiers": routing_config.capability_min_tiers,
    }


class LLMRouter:
    """Difficulty-aware, provider-agnostic router for chat completion calls."""

    _JSON_MARKERS = (
        "json",
        "schema",
        "model_dump_json",
        "return exactly",
        "return only",
        "extract",
        "parse",
        "structured",
    )
    _CODE_MARKERS = (
        "python",
        "code",
        "代码",
        "脚本",
        "traceback",
        "exception",
        "error",
        "repair",
        "fix",
        "patch",
        "debug",
    )
    _LAMMPS_MARKERS = (
        "lammps",
        "molecular dynamics",
        "分子动力学",
        "eam",
        "nvt",
        "npt",
        "thermo.csv",
        "dump",
        "timestep",
        "time_step",
        "potential",
        "ensemble",
    )
    _MATERIALS_MARKERS = (
        "phase diagram",
        "相图",
        "pycalphad",
        "tdb",
        "thermodynamic",
        "calphad",
        "solidus",
        "liquidus",
        "共晶",
        "包晶",
        "材料",
    )
    _RESEARCH_MARKERS = (
        "rag",
        "retrieval",
        "citation",
        "引用",
        "literature",
        "论文",
        "benchmark",
        "evaluate",
        "judge",
        "red",
        "blue",
        "review",
        "事实",
        "一致性",
    )
    _VISION_MARKERS = (
        "image_url",
        "data:image/",
        "screenshot",
        "vision",
        "multimodal",
        "识别",
        "截图",
        "图片",
        "图像",
    )

    def __init__(self, config: LLMRoutingConfig | None = None) -> None:
        self.config = config or load_llm_routing_config()
        self.learned_recommender = LearnedRouteRecommender(self.config.learned_policy)

    def decide(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        capability: str = "",
        multimodal: bool = False,
    ) -> LLMRoutingDecision:
        if not self.config.enabled:
            return self.decision_for_tier(
                "balanced",
                score=0,
                reasons=("routing_disabled",),
                capability=capability,
            )

        text = f"{system_prompt}\n{user_prompt}"
        focus_text = routing_focus_text(user_prompt)
        lowered_focus = focus_text.lower()
        capability_lower = capability.strip().lower()
        score = 0
        reasons: list[str] = []

        text_len = len(text)
        if text_len > 16000:
            score += 35
            reasons.append("very_long_context")
        elif text_len > 6000:
            score += 22
            reasons.append("long_context")
        elif text_len > 2000:
            score += 10
            reasons.append("medium_context")

        if max_tokens >= 3000:
            score += 12
            reasons.append("large_generation_budget")
        elif max_tokens >= 1600:
            score += 6
            reasons.append("moderate_generation_budget")

        # Generic system prompts often mention image/vision as a capability boundary.
        # Only real multimodal input, an explicit vision capability, or an actual
        # data/image payload should force the vision tier.
        has_image_payload = "data:image/" in lowered_focus or "image_url" in lowered_focus
        if multimodal or has_image_payload or "vision" in capability_lower or "recognition" in capability_lower:
            return self._decision_with_advanced_policies(
                rule_tier="vision",
                score=max(score, 75),
                reasons=tuple([*reasons, "vision_or_multimodal"]),
                capability=capability,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                multimodal=multimodal,
                min_tier=VISION_TIER,
            )

        if self._contains_any(lowered_focus, self._JSON_MARKERS):
            score += 8
            reasons.append("structured_output")

        if self._contains_any(lowered_focus, self._CODE_MARKERS):
            score += 22
            reasons.append("code_or_repair")

        if self._contains_any(lowered_focus, self._LAMMPS_MARKERS):
            score += 30
            reasons.append("lammps_or_md")

        if self._contains_any(lowered_focus, self._MATERIALS_MARKERS):
            score += 16
            reasons.append("materials_science")

        if self._contains_any(lowered_focus, self._RESEARCH_MARKERS):
            score += 16
            reasons.append("research_or_evaluation")

        if re.search(r"\b(add|delete|modify|verify)\b", lowered_focus) or "三层" in focus_text:
            score += 10
            reasons.append("protocol_sensitive")

        if temperature >= 0.35:
            score = max(0, score - 6)
            reasons.append("creative_low_risk")

        min_tier = self._minimum_tier_for_capability(capability_lower)
        scored_tier = self._tier_from_score(score)
        tier = self._max_tier(scored_tier, min_tier)
        if min_tier and min_tier != scored_tier:
            reasons.append(f"capability_min_tier:{min_tier}")
        if not reasons:
            reasons.append("simple_short_prompt")

        return self._decision_with_advanced_policies(
            rule_tier=tier,
            score=min(score, 100),
            reasons=tuple(reasons),
            capability=capability,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            multimodal=multimodal,
            min_tier=min_tier,
        )

    def decision_for_tier(
        self,
        tier: str,
        *,
        score: int,
        reasons: tuple[str, ...],
        capability: str = "",
        escalation_depth: int = 0,
        policy_metadata: dict[str, object] | None = None,
    ) -> LLMRoutingDecision:
        normalized = _normalize_tier(tier)
        fallback_tier = self.config.fallbacks.get(normalized, "")
        route = self.config.route_for(normalized)
        return LLMRoutingDecision(
            tier=normalized,
            score=max(0, min(int(score), 100)),
            reasons=reasons,
            route=route,
            fallback_tier=fallback_tier,
            capability=capability,
            escalation_depth=escalation_depth,
            policy_metadata=policy_metadata or {},
        )

    def _decision_with_advanced_policies(
        self,
        *,
        rule_tier: str,
        score: int,
        reasons: tuple[str, ...],
        capability: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        multimodal: bool,
        min_tier: str = "",
    ) -> LLMRoutingDecision:
        return self._decision_with_learned_policy(
            rule_tier=rule_tier,
            score=score,
            reasons=reasons,
            capability=capability,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            multimodal=multimodal,
            min_tier=min_tier,
        )

    def _decision_with_learned_policy(
        self,
        *,
        rule_tier: str,
        score: int,
        reasons: tuple[str, ...],
        capability: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        multimodal: bool,
        min_tier: str = "",
    ) -> LLMRoutingDecision:
        policy = self.config.learned_policy
        if not policy.enabled:
            return self.decision_for_tier(rule_tier, score=score, reasons=reasons, capability=capability)

        recommendation = self.learned_recommender.recommend(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
            multimodal=multimodal,
        )
        if recommendation is None:
            reason = self.learned_recommender.load_error or "model_not_loaded"
            return self.decision_for_tier(
                rule_tier,
                score=score,
                reasons=tuple([*reasons, f"learned_unavailable:{_compact_error(reason)}"]),
                capability=capability,
            )

        mode = policy.normalized_mode()
        learned_tier = _normalize_tier(recommendation.tier, default=rule_tier)
        confidence = recommendation.confidence
        augmented_reasons = tuple([*reasons, f"learned_{mode}:{learned_tier}:{confidence:.3f}"])
        if mode == "shadow":
            return self.decision_for_tier(rule_tier, score=score, reasons=augmented_reasons, capability=capability)
        if confidence < policy.confidence_threshold:
            return self.decision_for_tier(
                rule_tier,
                score=score,
                reasons=tuple([*augmented_reasons, "learned_below_threshold"]),
                capability=capability,
            )

        candidate_tier = learned_tier
        if rule_tier == VISION_TIER or min_tier == VISION_TIER:
            candidate_tier = VISION_TIER
        else:
            candidate_tier = self._max_tier(candidate_tier, min_tier)
            if not policy.allow_downgrade:
                candidate_tier = self._max_tier(candidate_tier, rule_tier)
        if candidate_tier != learned_tier:
            augmented_reasons = tuple([*augmented_reasons, f"learned_guard_clamped:{learned_tier}->{candidate_tier}"])
        if candidate_tier != rule_tier:
            augmented_reasons = tuple([*augmented_reasons, f"learned_override:{rule_tier}->{candidate_tier}"])
        return self.decision_for_tier(candidate_tier, score=score, reasons=augmented_reasons, capability=capability)

    def fallback_decision(self, decision: LLMRoutingDecision, *, error_message: str = "") -> LLMRoutingDecision | None:
        if not self.config.enabled or not self.config.fallback_on_error:
            return None
        if decision.escalation_depth >= self.config.max_escalations:
            return None
        if not decision.fallback_tier:
            return None
        reasons = tuple([*decision.reasons, f"fallback_after_error:{_compact_error(error_message)}"])
        return self.decision_for_tier(
            decision.fallback_tier,
            score=min(decision.score + 10, 100),
            reasons=reasons,
            capability=decision.capability,
            escalation_depth=decision.escalation_depth + 1,
        )

    @staticmethod
    def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
        return any(marker in text for marker in markers)

    def _tier_from_score(self, score: int) -> str:
        if score <= self.config.fast_max_score:
            return "fast"
        if score <= self.config.balanced_max_score:
            return "balanced"
        return "strong"

    def _minimum_tier_for_capability(self, capability: str) -> str:
        if not capability:
            return ""
        normalized = capability.replace("_", ".").replace("-", ".")
        for marker, tier in self.config.capability_min_tiers.items():
            if marker and marker in normalized:
                return tier
        return ""

    @staticmethod
    def _max_tier(tier: str, minimum: str) -> str:
        if minimum == VISION_TIER or tier == VISION_TIER:
            return VISION_TIER
        if not minimum:
            return tier
        tier_index = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
        minimum_index = TIER_ORDER.index(minimum) if minimum in TIER_ORDER else 1
        return TIER_ORDER[max(tier_index, minimum_index)]


def _compact_error(error_message: str) -> str:
    compact = " ".join(str(error_message).split())
    return compact[:96] if compact else "unknown"
