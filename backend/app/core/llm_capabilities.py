from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModelCapability(StrEnum):
    TEXT = "text"
    STRUCTURED_OUTPUT = "structured_output"
    CODE = "code"
    REASONING = "reasoning"
    VISION = "vision"


class LLMCapability(StrEnum):
    CHAT_ANSWER = "chat.answer"
    PROMPT_SUGGEST = "prompt.suggest"
    MEMORY_SUMMARY = "memory.summary"
    SUPERVISOR_ROUTE = "supervisor.route"
    RAG_ANSWER = "rag.answer"
    PHASE_REQUEST_PARSE = "phase.request.parse"
    PHASE_CODEGEN = "phase.codegen"
    PHASE_CODEGEN_REPAIR = "phase.codegen.repair"
    PHASE_REVIEW = "phase.review"
    LAMMPS_REQUEST_PARSE = "lammps.request.parse"
    LAMMPS_REQUEST_REPAIR = "lammps.request.repair"
    LAMMPS_REVIEW = "lammps.review"
    VISION_RECOGNITION = "vision.recognition"


@dataclass(frozen=True)
class LLMCapabilitySpec:
    capability: LLMCapability
    minimum_tier: str
    required_model_capabilities: frozenset[ModelCapability]
    risk_level: str


def _spec(
    capability: LLMCapability,
    minimum_tier: str,
    *required: ModelCapability,
    risk_level: str,
) -> LLMCapabilitySpec:
    return LLMCapabilitySpec(
        capability=capability,
        minimum_tier=minimum_tier,
        required_model_capabilities=frozenset(required),
        risk_level=risk_level,
    )


LLM_CAPABILITY_REGISTRY: dict[str, LLMCapabilitySpec] = {
    spec.capability.value: spec
    for spec in (
        _spec(LLMCapability.CHAT_ANSWER, "fast", ModelCapability.TEXT, risk_level="low"),
        _spec(LLMCapability.PROMPT_SUGGEST, "fast", ModelCapability.TEXT, risk_level="low"),
        _spec(LLMCapability.MEMORY_SUMMARY, "fast", ModelCapability.TEXT, risk_level="low"),
        _spec(
            LLMCapability.SUPERVISOR_ROUTE,
            "balanced",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            risk_level="medium",
        ),
        _spec(LLMCapability.RAG_ANSWER, "balanced", ModelCapability.TEXT, risk_level="medium"),
        _spec(
            LLMCapability.PHASE_REQUEST_PARSE,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.REASONING,
            risk_level="high",
        ),
        _spec(
            LLMCapability.PHASE_CODEGEN,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
            risk_level="high",
        ),
        _spec(
            LLMCapability.PHASE_CODEGEN_REPAIR,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
            risk_level="high",
        ),
        _spec(
            LLMCapability.PHASE_REVIEW,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.REASONING,
            risk_level="high",
        ),
        _spec(
            LLMCapability.LAMMPS_REQUEST_PARSE,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.REASONING,
            risk_level="high",
        ),
        _spec(
            LLMCapability.LAMMPS_REQUEST_REPAIR,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
            risk_level="critical",
        ),
        _spec(
            LLMCapability.LAMMPS_REVIEW,
            "strong",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.REASONING,
            risk_level="critical",
        ),
        _spec(
            LLMCapability.VISION_RECOGNITION,
            "vision",
            ModelCapability.TEXT,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.VISION,
            risk_level="high",
        ),
    )
}


def normalize_capability(value: str | LLMCapability) -> str:
    return str(value).strip().lower().replace("_", ".").replace("-", ".")


def get_capability_spec(value: str | LLMCapability) -> LLMCapabilitySpec | None:
    return LLM_CAPABILITY_REGISTRY.get(normalize_capability(value))


def public_capability_registry() -> dict[str, dict[str, object]]:
    return {
        name: {
            "minimum_tier": spec.minimum_tier,
            "required_model_capabilities": sorted(item.value for item in spec.required_model_capabilities),
            "risk_level": spec.risk_level,
        }
        for name, spec in sorted(LLM_CAPABILITY_REGISTRY.items())
    }
