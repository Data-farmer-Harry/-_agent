from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.thermo.parser import TdbMetadata, parse_tdb_metadata
from app.utils.constants import normalize_system_key


PROJECT_ROOT = Path(__file__).resolve().parents[2]
THERMO_REGISTRY_PATH = PROJECT_ROOT / "configs" / "thermo_registry.json"


@dataclass(frozen=True)
class ThermoDatabaseCard:
    system_name: str
    aliases: tuple[str, ...]
    summary: str
    database_file: str
    documentation_url: str
    source_url: str
    provenance: str
    x_component: str
    x_axis_label: str
    component_selection: tuple[str, ...]
    phase_selection: tuple[str, ...]
    accuracy_reference: dict[str, object]
    tags: tuple[str, ...]
    family: str = "tdb_calculated_binary"
    format: str = "tdb"

    def all_names(self) -> tuple[str, ...]:
        return (self.system_name, *self.aliases)

    @property
    def database_path(self) -> Path:
        return (PROJECT_ROOT / self.database_file).resolve()

    @property
    def database_name(self) -> str:
        return self.database_path.name

    @property
    def metadata(self) -> TdbMetadata:
        return parse_tdb_metadata(str(self.database_path))

    @property
    def components(self) -> tuple[str, ...]:
        return self.component_selection or self.metadata.components

    @property
    def phases(self) -> tuple[str, ...]:
        return self.phase_selection or self.metadata.phases

    def prompt_context(self) -> str:
        return (
            f"System: {self.system_name}\n"
            "Mode: true TDB/CALPHAD calculation\n"
            f"Database file: {self.database_name}\n"
            f"Components: {', '.join(self.components)}\n"
            f"Selected phases: {', '.join(self.phases)}\n"
            f"Source URL: {self.source_url}\n"
            f"Documentation: {self.documentation_url}\n"
            f"Summary: {self.summary}\n"
            f"Provenance: {self.provenance}\n"
            "Project rule: generated code must call the local build_calculated_phase_diagram_report helper, "
            "which runs pycalphad against this registered TDB file."
        )

    def public_payload(self) -> dict[str, object]:
        return {
            "system_name": self.system_name,
            "aliases": list(self.aliases),
            "family": self.family,
            "format": self.format,
            "database_name": self.database_name,
            "database_file": self.database_file,
            "documentation_url": self.documentation_url,
            "source_url": self.source_url,
            "provenance": self.provenance,
            "components": list(self.components),
            "phases": list(self.phases),
            "x_component": self.x_component,
            "x_axis_label": self.x_axis_label,
            "summary": self.summary,
            "accuracy_reference": dict(self.accuracy_reference),
            "tags": list(self.tags),
        }


CalculatedBinarySystemCard = ThermoDatabaseCard


@lru_cache(maxsize=1)
def load_thermo_database_cards() -> tuple[ThermoDatabaseCard, ...]:
    payload = json.loads(THERMO_REGISTRY_PATH.read_text(encoding="utf-8"))
    cards: list[ThermoDatabaseCard] = []
    for item in payload:
        cards.append(
            ThermoDatabaseCard(
                system_name=str(item["system_name"]),
                aliases=tuple(item.get("aliases", [])),
                summary=str(item["summary"]),
                database_file=str(item["database_file"]),
                documentation_url=str(item["documentation_url"]),
                source_url=str(item["source_url"]),
                provenance=str(item["provenance"]),
                x_component=str(item["x_component"]),
                x_axis_label=str(item["x_axis_label"]),
                component_selection=tuple(item.get("component_selection", [])),
                phase_selection=tuple(item.get("phase_selection", [])),
                accuracy_reference=dict(item.get("accuracy_reference", {})),
                tags=tuple(item.get("tags", [])),
            )
        )
    return tuple(cards)


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, ThermoDatabaseCard]:
    alias_map: dict[str, ThermoDatabaseCard] = {}
    for card in load_thermo_database_cards():
        for name in card.all_names():
            alias_map[normalize_system_key(name)] = card
    return alias_map


def get_thermo_database_card(system_name: str) -> ThermoDatabaseCard | None:
    normalized = normalize_system_key(system_name or "")
    if not normalized:
        return None
    return _alias_map().get(normalized)


def list_thermo_database_systems() -> list[str]:
    return [card.system_name for card in load_thermo_database_cards()]


def build_thermo_prompt_hint(system_name: str) -> str:
    card = get_thermo_database_card(system_name)
    return card.prompt_context() if card is not None else ""


def load_calculated_binary_cards() -> tuple[CalculatedBinarySystemCard, ...]:
    return load_thermo_database_cards()


def get_calculated_binary_card(system_name: str) -> CalculatedBinarySystemCard | None:
    return get_thermo_database_card(system_name)


def list_calculated_binary_systems() -> list[str]:
    return list_thermo_database_systems()


def build_calculated_prompt_hint(system_name: str) -> str:
    return build_thermo_prompt_hint(system_name)


def resolve_tdb_path(card: CalculatedBinarySystemCard) -> Path:
    path = card.database_path
    if not path.exists():
        raise FileNotFoundError(f"Could not locate TDB file for {card.system_name}: {path}")
    return path


def retrieve_thermo_database(system_name: str) -> tuple[ThermoDatabaseCard | None, dict[str, object]]:
    card = get_thermo_database_card(system_name)
    if card is None:
        return None, {
            "matched": False,
            "query": system_name,
            "registry_count": len(load_thermo_database_cards()),
        }
    return card, {
        "matched": True,
        "query": system_name,
        "system_name": card.system_name,
        "database_name": card.database_name,
        "database_file": card.database_file,
        "source_url": card.source_url,
        "documentation_url": card.documentation_url,
        "components": list(card.components),
        "phases": list(card.phases),
        "registry_count": len(load_thermo_database_cards()),
    }
