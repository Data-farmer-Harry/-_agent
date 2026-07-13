from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal, Protocol


class RagHitLike(Protocol):
    score: float
    lexical_score: float
    bm25_score: float
    vector_score: float
    rerank_score: float | None
    graph_score: float


RetrievalAction = Literal["answer", "expand", "escalate", "abstain"]


@dataclass(frozen=True)
class RetrievalUncertainty:
    confidence: float
    estimated_risk: float
    action: RetrievalAction
    top_score: float
    score_margin: float
    channel_agreement: float
    evidence_count: int
    reasons: tuple[str, ...]

    def public_payload(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "estimated_risk": self.estimated_risk,
            "action": self.action,
            "top_score": self.top_score,
            "score_margin": self.score_margin,
            "channel_agreement": self.channel_agreement,
            "evidence_count": self.evidence_count,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ConformalRiskCalibrator:
    """Small split-conformal-style threshold wrapper for selective RAG."""

    answer_threshold: float = 0.72
    expand_threshold: float = 0.5
    abstain_threshold: float = 0.25
    target_risk: float = 0.1

    @classmethod
    def fit(cls, calibration_confidences: Iterable[float], correctness: Iterable[bool], *, target_risk: float = 0.1) -> "ConformalRiskCalibrator":
        pairs = sorted(zip(calibration_confidences, correctness), reverse=True)
        if not pairs:
            return cls(target_risk=target_risk)
        accepted = 0
        errors = 0
        threshold = 1.0
        for confidence, correct in pairs:
            accepted += 1
            errors += int(not correct)
            empirical_risk = (errors + 1) / (accepted + 1)
            if empirical_risk <= target_risk:
                threshold = float(confidence)
        return cls(
            answer_threshold=max(0.5, min(threshold, 0.95)),
            expand_threshold=max(0.3, min(threshold - 0.18, 0.7)),
            abstain_threshold=max(0.1, min(threshold - 0.42, 0.4)),
            target_risk=target_risk,
        )


def estimate_retrieval_uncertainty(
    hits: Iterable[RagHitLike],
    *,
    calibrator: ConformalRiskCalibrator | None = None,
) -> RetrievalUncertainty:
    items = list(hits)
    calibrator = calibrator or ConformalRiskCalibrator()
    if not items:
        return RetrievalUncertainty(
            confidence=0.0,
            estimated_risk=1.0,
            action="abstain",
            top_score=0.0,
            score_margin=0.0,
            channel_agreement=0.0,
            evidence_count=0,
            reasons=("no_evidence",),
        )
    top = items[0]
    second_score = items[1].score if len(items) > 1 else 0.0
    margin = max(float(top.score) - float(second_score), 0.0)
    active_channels = sum(
        value > 0.0
        for value in (
            top.lexical_score,
            top.bm25_score,
            top.vector_score,
            top.rerank_score or 0.0,
            top.graph_score,
        )
    )
    agreement = active_channels / 5.0
    normalized_top = 1.0 - math.exp(-max(float(top.score), 0.0) / 2.5)
    diversity = min(len(items) / 4.0, 1.0)
    confidence = max(0.0, min(0.5 * normalized_top + 0.2 * min(margin, 1.0) + 0.2 * agreement + 0.1 * diversity, 1.0))
    reasons: list[str] = []
    if margin < 0.08:
        reasons.append("small_top_score_margin")
    if agreement < 0.4:
        reasons.append("low_retrieval_channel_agreement")
    if len(items) < 2:
        reasons.append("insufficient_evidence_diversity")
    if confidence >= calibrator.answer_threshold:
        action: RetrievalAction = "answer"
    elif confidence >= calibrator.expand_threshold:
        action = "expand"
    elif confidence >= calibrator.abstain_threshold:
        action = "escalate"
    else:
        action = "abstain"
    return RetrievalUncertainty(
        confidence=round(confidence, 6),
        estimated_risk=round(1.0 - confidence, 6),
        action=action,
        top_score=round(float(top.score), 6),
        score_margin=round(margin, 6),
        channel_agreement=round(agreement, 6),
        evidence_count=len(items),
        reasons=tuple(reasons or ["calibrated_evidence_available"]),
    )
