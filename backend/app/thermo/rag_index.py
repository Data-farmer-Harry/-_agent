from __future__ import annotations

import re
from functools import lru_cache

from app.core.bm25 import build_bm25_index
from app.rag.sqlite_vector_store import content_digest, get_vector_store
from app.thermo.rag_models import ThermoCardDocument
from app.thermo.rag_vector import build_embeddings, effective_embedding_backend, embedding_signature
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
    normalized_names = [normalize_system_key(name) for name in card.all_names() if normalize_system_key(name)]
    parts = [
        card.system_name,
        *card.aliases,
        *normalized_names,
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


def _bm25_tokens(card: ThermoDatabaseCard, text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    tokens.extend(_tokenize(text))
    tokens.extend(normalize_system_key(name) for name in card.all_names() if normalize_system_key(name))
    tokens.extend(alias.lower() for alias in _component_aliases(card))
    tokens.extend(f"component:{component.lower()}" for component in card.components)
    tokens.extend(f"phase:{phase.lower()}" for phase in card.phases)
    tokens.extend(f"tag:{tag.lower()}" for tag in card.tags)
    tokens.append(f"x_component:{card.x_component.lower()}")
    return tuple(token for token in tokens if token)


@lru_cache(maxsize=8)
def _build_thermo_card_index_cached(signature: str, store_path_key: str = "") -> tuple[ThermoCardDocument, ...]:
    documents: list[ThermoCardDocument] = []
    cards = list(load_thermo_database_cards())
    texts = [_document_text(card) for card in cards]
    document_ids = [card.system_name for card in cards]
    digest = content_digest(zip(document_ids, texts))
    bm25_token_sets = [_bm25_tokens(card, text) for card, text in zip(cards, texts)]
    bm25_index = build_bm25_index(bm25_token_sets)
    preferred_backend = signature.split(":", 1)[0]
    store = get_vector_store()
    store_current = preferred_backend != "disabled" and store.collection_is_current(
        "thermo_rag",
        embedding_signature=signature,
        content_digest_value=digest,
        document_ids=document_ids,
    )
    embedding_backend = preferred_backend
    if store_current:
        status = store.collection_status("thermo_rag")
        if status is not None:
            embedding_backend = status.embedding_backend
    elif preferred_backend != "disabled":
        vectors, embedding_backend = build_embeddings(texts, backend=preferred_backend)
        actual_signature = embedding_signature(embedding_backend)
        if vectors and all(vector for vector in vectors):
            store.replace_collection(
                "thermo_rag",
                embedding_signature=actual_signature,
                embedding_backend=embedding_backend,
                content_digest_value=digest,
                documents=[(document_id, vector) for document_id, vector in zip(document_ids, vectors)],
            )
    actual_signature = embedding_signature(embedding_backend)
    if actual_signature != signature:
        return _build_thermo_card_index_cached(actual_signature, store_path_key)
    for index, (card, text) in enumerate(zip(cards, texts)):
        documents.append(
            ThermoCardDocument(
                card=card,
                text=text,
                normalized_names=tuple(normalize_system_key(name) for name in card.all_names() if normalize_system_key(name)),
                tokens=_tokenize(text),
                component_aliases=_component_aliases(card),
                phase_aliases=_phase_aliases(card),
                tag_aliases=_tag_aliases(card),
                bm25_tokens=bm25_token_sets[index],
                bm25_stats=bm25_index.documents[index],
                embedding_backend=embedding_backend,
                embedding_signature=actual_signature,
            )
        )
    return tuple(documents)


def build_thermo_card_index(preferred_backend: str | None = None) -> tuple[ThermoCardDocument, ...]:
    backend = effective_embedding_backend(preferred_backend)
    store = get_vector_store()
    return _build_thermo_card_index_cached(embedding_signature(backend), str(store.path))
