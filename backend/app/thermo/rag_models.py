from __future__ import annotations

from dataclasses import dataclass

from app.thermo.registry import ThermoDatabaseCard


@dataclass(frozen=True)
class ThermoCardDocument:
    card: ThermoDatabaseCard
    text: str
    normalized_names: tuple[str, ...]
    tokens: tuple[str, ...]
    component_aliases: tuple[str, ...]
    phase_aliases: tuple[str, ...]
    tag_aliases: tuple[str, ...]


@dataclass(frozen=True)
class ThermoRagCandidateRecord:
    card: ThermoDatabaseCard
    score: float
    selection_strategy: str
    match_reasons: tuple[str, ...]
    matched_terms: tuple[str, ...]

    def public_payload(self) -> dict[str, object]:
        return {
            "system_name": self.card.system_name,
            "score": round(self.score, 3),
            "selection_strategy": self.selection_strategy,
            "match_reasons": list(self.match_reasons),
            "matched_terms": list(self.matched_terms),
            "aliases": list(self.card.aliases),
            "components": list(self.card.components),
            "phases": list(self.card.phases),
            "tags": list(self.card.tags),
            "database_name": self.card.database_name,
            "summary": self.card.summary,
            "source_url": self.card.source_url,
        }
