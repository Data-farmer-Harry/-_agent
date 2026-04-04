from __future__ import annotations

import re
from functools import lru_cache

from app.thermo.rag_models import ThermoCardDocument
from app.thermo.registry import ThermoDatabaseCard, load_thermo_database_cards
from app.utils.constants import normalize_system_key


ELEMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "AL": ("al", "aluminum", "aluminium", "铝"),
    "CU": ("cu", "copper", "铜"),
    "FE": ("fe", "iron", "铁"),
    "ZN": ("zn", "zinc", "锌"),
    "MG": ("mg", "magnesium", "镁"),
    "NI": ("ni", "nickel", "镍"),
    "PB": ("pb", "lead", "铅"),
    "SN": ("sn", "tin", "锡"),
}

TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]{1,6}", flags=re.IGNORECASE)


def _tokenize(text: str) -> tuple[str, ...]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text or "")]
    return tuple(dict.fromkeys(token for token in tokens if token))


def _component_aliases(card: ThermoDatabaseCard) -> tuple[str, ...]:
    aliases: list[str] = []
    for component in card.components:
        normalized = component.upper()
        aliases.append(component.lower())
        aliases.extend(ELEMENT_ALIASES.get(normalized, (component.lower(),)))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _phase_aliases(card: ThermoDatabaseCard) -> tuple[str, ...]:
    aliases = [phase.lower() for phase in card.phases]
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _tag_aliases(card: ThermoDatabaseCard) -> tuple[str, ...]:
    aliases = [tag.lower() for tag in card.tags]
    aliases.extend(normalize_system_key(tag) for tag in card.tags if normalize_system_key(tag))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _document_text(card: ThermoDatabaseCard) -> str:
    parts = [
        card.system_name,
        *card.aliases,
        card.summary,
        " ".join(card.components),
        " ".join(card.phases),
        " ".join(card.tags),
        card.database_name,
        card.provenance,
        card.x_axis_label,
    ]
    for component in card.components:
        parts.extend(ELEMENT_ALIASES.get(component.upper(), ()))
    return " ".join(part for part in parts if part).strip()


@lru_cache(maxsize=1)
def build_thermo_card_index() -> tuple[ThermoCardDocument, ...]:
    documents: list[ThermoCardDocument] = []
    for card in load_thermo_database_cards():
        text = _document_text(card)
        documents.append(
            ThermoCardDocument(
                card=card,
                text=text,
                normalized_names=tuple(normalize_system_key(name) for name in card.all_names() if normalize_system_key(name)),
                tokens=_tokenize(text),
                component_aliases=_component_aliases(card),
                phase_aliases=_phase_aliases(card),
                tag_aliases=_tag_aliases(card),
            )
        )
    return tuple(documents)
