from __future__ import annotations

import json
from pathlib import Path

from app.thermo.registry import ThermoDatabaseCard


def _phase_group(phase_name: str) -> str:
    upper = phase_name.upper()
    if "LIQ" in upper:
        return "liquid"
    if any(token in upper for token in ("SIGMA", "LAVES", "CHI", "MU", "AL", "PT", "CO", "FE")) and "_" not in upper:
        return "intermetallic"
    if any(token in upper for token in ("L12", "B2", "D0", "C14", "C15", "C36")):
        return "intermetallic"
    return "solid_solution"


def build_thermo_rag_documents(cards: tuple[ThermoDatabaseCard, ...]) -> list[dict[str, object]]:
    docs: list[dict[str, object]] = []
    for card in cards:
        docs.append(
            {
                "id": f"system_card:{card.system_name}",
                "doc_type": "system_card",
                "system_name": card.system_name,
                "aliases": list(card.aliases),
                "database_name": card.database_name,
                "database_file": card.database_file,
                "family": card.family,
                "format": card.format,
                "components": list(card.components),
                "phases": list(card.phases),
                "tags": list(card.tags),
                "source_url": card.source_url,
                "documentation_url": card.documentation_url,
                "provenance": card.provenance,
                "accuracy_reference": dict(card.accuracy_reference),
                "content": (
                    f"{card.system_name} binary phase diagram database card. "
                    f"Components: {', '.join(component.title() for component in card.components)}. "
                    f"Selected phases: {', '.join(card.phases[:8])}. "
                    f"{card.summary}"
                ),
            }
        )

        for phase_name in card.phases[:2]:
            docs.append(
                {
                    "id": f"phase_card:{card.system_name}:{phase_name}",
                    "doc_type": "phase_card",
                    "system_name": card.system_name,
                    "database_name": card.database_name,
                    "database_file": card.database_file,
                    "family": card.family,
                    "format": card.format,
                    "components": list(card.components),
                    "phase_name": phase_name,
                    "phase_group": _phase_group(phase_name),
                    "source_url": card.source_url,
                    "provenance": card.provenance,
                    "content": (
                        f"Phase card for {phase_name} in the {card.system_name} thermodynamic database. "
                        f"Use when explaining phase selection, equilibrium behavior, or review evidence "
                        f"for {card.system_name} binary calculations."
                    ),
                }
            )

        docs.append(
            {
                "id": f"provenance_card:{card.system_name}",
                "doc_type": "provenance_card",
                "system_name": card.system_name,
                "database_name": card.database_name,
                "database_file": card.database_file,
                "family": card.family,
                "format": card.format,
                "source_url": card.source_url,
                "documentation_url": card.documentation_url,
                "provenance": card.provenance,
                "content": (
                    f"This {card.system_name} thermodynamic database entry uses {card.database_name}. "
                    f"Source: {card.source_url}. Provenance: {card.provenance}"
                ),
            }
        )

        docs.append(
            {
                "id": f"tdb_chunk:{card.system_name}:PHASE:{card.phases[0] if card.phases else 'UNKNOWN'}",
                "doc_type": "tdb_chunk",
                "system_name": card.system_name,
                "database_name": card.database_name,
                "database_file": card.database_file,
                "family": card.family,
                "format": card.format,
                "section_type": "PHASE",
                "section_name": card.phases[0] if card.phases else "UNKNOWN",
                "source_url": card.source_url,
                "provenance": card.provenance,
                "content": (
                    f"Representative TDB chunk for {card.system_name} using section "
                    f"{card.phases[0] if card.phases else 'UNKNOWN'}. "
                    "Use this chunk for explanation or debug support, not as the primary execution selector."
                ),
            }
        )
    return docs


def write_thermo_rag_documents(path: Path, cards: tuple[ThermoDatabaseCard, ...]) -> int:
    documents = build_thermo_rag_documents(cards)
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in documents) + "\n"
    path.write_text(payload, encoding="utf-8")
    return len(documents)
