from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from app.config import settings
from app.core.llm import LLMClient
from app.core.llm_capabilities import LLMCapability
from app.state import (
    AgentChatRequest,
    ConversationTurn,
    LastRunContext,
    LongTermMemorySnapshot,
    MemorySnapshot,
    RecognitionResult,
    ShortTermMemorySnapshot,
    UploadedAsset,
)
from app.utils.path_utils import ensure_directory


def _compact_text(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 1, 1)].rstrip()}…"


def _dedupe_keep_order(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = _compact_text(value, limit=180)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
        if len(unique) >= limit:
            break
    return unique


_ELEMENT_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "al": ("al", "aluminum", "aluminium", "铝"),
    "mg": ("mg", "magnesium", "镁"),
    "ni": ("ni", "nickel", "镍"),
    "zn": ("zn", "zinc", "锌"),
    "pb": ("pb", "lead", "铅"),
    "sn": ("sn", "tin", "锡"),
    "fe": ("fe", "iron", "铁"),
    "cu": ("cu", "copper", "铜"),
    "cr": ("cr", "chromium", "铬"),
    "nb": ("nb", "niobium", "铌"),
    "ti": ("ti", "titanium", "钛"),
    "v": (" v ", "vanadium", "钒"),
    "co": ("co", "cobalt", "钴"),
    "pt": ("pt", "platinum", "铂"),
    "pd": ("pd", "palladium", "钯"),
    "ru": ("ru", "ruthenium", "钌"),
    "tc": ("tc", "technetium", "锝"),
    "mo": ("mo", "molybdenum", "钼"),
    "re": ("re", "rhenium", "铼"),
}


def _expand_material_alias_tokens(text: str) -> list[str]:
    if not text:
        return []
    lowered = f" {text.lower()} "
    expanded: list[str] = []
    for symbol, aliases in _ELEMENT_ALIAS_GROUPS.items():
        for alias in aliases:
            candidate = alias if alias.startswith(" ") else alias.lower()
            haystack = lowered if alias.startswith(" ") else text.lower()
            if candidate in haystack:
                expanded.append(symbol)
                break
    return _dedupe_keep_order(expanded, limit=24)


def _cjk_ngrams(text: str) -> list[str]:
    grams: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        grams.append(chunk)
        for size in (2, 3):
            if len(chunk) < size:
                continue
            for index in range(len(chunk) - size + 1):
                grams.append(chunk[index : index + size])
    return _dedupe_keep_order(grams, limit=64)


def _tokenize_for_retrieval(text: str) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    ascii_tokens = re.findall(r"[a-z0-9_+\-\.]{2,}", lowered)
    cjk_tokens = _cjk_ngrams(text)
    alias_tokens = _expand_material_alias_tokens(text)
    return _dedupe_keep_order([*ascii_tokens, *cjk_tokens, *alias_tokens], limit=64)


def _summarize_messages(messages: list[ConversationTurn], *, max_turns: int = 8) -> str:
    if not messages:
        return ""
    sampled = messages[-max_turns:]
    parts: list[str] = []
    for turn in sampled:
        role = "U" if turn.role == "user" else "A"
        text = _compact_text(turn.content, limit=140)
        parts.append(f"{role}: {text}")
    return " | ".join(parts)


def _latest_user_message(messages: list[ConversationTurn]) -> str:
    for turn in reversed(messages):
        if turn.role == "user" and turn.content.strip():
            return _compact_text(turn.content, limit=220)
    return ""


def _build_session_title(messages: list[ConversationTurn], last_run_context: LastRunContext) -> str:
    for turn in messages:
        if turn.role == "user" and turn.content.strip():
            return _compact_text(turn.content, limit=56)
    fallback = (
        last_run_context.request_summary
        or last_run_context.system_name
        or last_run_context.route_name
        or "新会话"
    )
    return _compact_text(fallback, limit=56) or "新会话"


def _summarize_recognition_result(recognition_result: RecognitionResult | None) -> str:
    if recognition_result is None:
        return ""
    parts: list[str] = []
    if recognition_result.system:
        parts.append(f"system={recognition_result.system}")
    if recognition_result.diagram_type:
        parts.append(f"type={recognition_result.diagram_type}")
    if recognition_result.phases:
        parts.append(f"phases={', '.join(recognition_result.phases[:4])}")
    if recognition_result.critical_points:
        parts.append(f"critical_points={len(recognition_result.critical_points)}")
    if recognition_result.confidence:
        parts.append(f"confidence={recognition_result.confidence:.2f}")
    return ", ".join(parts)


def _summarize_last_run(last_run_context: LastRunContext) -> str:
    if not last_run_context.run_id:
        return ""
    run_summary = (
        last_run_context.request_summary
        or last_run_context.system_name
        or last_run_context.route_name
        or last_run_context.compute_domain
    )
    parts = [part for part in [run_summary, last_run_context.generation_source or "", last_run_context.selected_tool or ""] if part]
    if last_run_context.review_passed is True:
        parts.append("review=passed")
    elif last_run_context.review_passed is False:
        parts.append("review=failed")
    if last_run_context.artifact_names:
        parts.append(f"artifacts={', '.join(last_run_context.artifact_names[:4])}")
    return ", ".join(parts)


def _extract_user_preferences(messages: list[ConversationTurn], previous_snapshot: LongTermMemorySnapshot) -> list[str]:
    preference_markers = ("希望", "需要", "不要", "最好", "优先", "尽量", "必须", "记得", "只要", "先别", "保持")
    candidates: list[str] = []
    for turn in messages[-12:]:
        if turn.role != "user":
            continue
        text = _compact_text(turn.content, limit=180)
        if any(marker in text for marker in preference_markers):
            candidates.append(text)
    candidates.extend(previous_snapshot.user_preferences)
    return _dedupe_keep_order(candidates, limit=8)


def _build_retrieval_hints(
    *,
    previous_snapshot: LongTermMemorySnapshot,
    recognition_result: RecognitionResult | None,
    last_run_context: LastRunContext,
) -> list[str]:
    hints: list[str] = []
    if recognition_result and recognition_result.system:
        hints.append(recognition_result.system)
    if last_run_context.system_name:
        hints.append(last_run_context.system_name)
    if last_run_context.compute_domain and last_run_context.compute_domain != "none":
        hints.append(last_run_context.compute_domain)
    hints.extend(previous_snapshot.research_topics)
    hints.extend(previous_snapshot.preferred_tools)
    return _dedupe_keep_order(hints, limit=12)


def build_long_term_memory_hits(
    *,
    query: str,
    snapshot: LongTermMemorySnapshot,
    limit: int = 6,
) -> list[str]:
    tokens = _tokenize_for_retrieval(query)
    if not tokens:
        return _dedupe_keep_order(
            [
                snapshot.strategic_summary,
                *snapshot.research_topics[:2],
                *snapshot.salient_facts[:2],
                *snapshot.open_questions[:2],
            ],
            limit=limit,
        )

    candidates: list[tuple[float, str]] = []
    weighted_sections: list[tuple[float, list[str]]] = [
        (1.4, [snapshot.strategic_summary]),
        (1.2, snapshot.research_topics),
        (1.15, snapshot.user_preferences),
        (1.05, snapshot.salient_facts),
        (1.0, snapshot.completed_run_summaries),
        (0.95, snapshot.open_questions),
        (0.9, snapshot.preferred_tools),
        (0.85, snapshot.retrieval_hints),
    ]
    for base_weight, items in weighted_sections:
        for item in items:
            text = _compact_text(item, limit=220)
            if not text:
                continue
            lowered = text.lower()
            overlap = sum(1 for token in tokens if token in lowered or token in text)
            if overlap == 0:
                continue
            bonus = 0.0
            if any(token == lowered for token in tokens):
                bonus += 0.4
            if len(text) <= 80:
                bonus += 0.08
            candidates.append((base_weight * overlap + bonus, text))
    ranked = [text for _, text in sorted(candidates, key=lambda item: item[0], reverse=True)]
    return _dedupe_keep_order(ranked, limit=limit)


class _MemoryPaths:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = ensure_directory(root_dir)
        self.short_term_dir = ensure_directory(self.root_dir / "short_term")
        self.long_term_dir = ensure_directory(self.root_dir / "long_term")
        self.sqlite_path = self.root_dir / "memory.sqlite3"

    @staticmethod
    def normalize_conversation_id(conversation_id: str) -> str:
        raw = conversation_id.strip() or "default"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
        if safe == raw and safe:
            return safe
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        prefix = safe[:36] or "conversation"
        return f"{prefix}_{digest}"

    def short_path_for(self, conversation_id: str) -> Path:
        return self.short_term_dir / f"{self.normalize_conversation_id(conversation_id)}.json"

    def long_path_for(self, conversation_id: str) -> Path:
        return self.long_term_dir / f"{self.normalize_conversation_id(conversation_id)}.json"

    def legacy_path_for(self, conversation_id: str) -> Path:
        return self.root_dir / f"{self.normalize_conversation_id(conversation_id)}.json"


class SQLiteMemoryStore:
    def __init__(self, paths: _MemoryPaths) -> None:
        self.paths = paths
        self.db_path = paths.sqlite_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        ensure_directory(self.db_path.parent)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_snapshots (
                    conversation_id TEXT PRIMARY KEY,
                    short_term_json TEXT NOT NULL,
                    long_term_json TEXT NOT NULL,
                    session_title TEXT NOT NULL DEFAULT '',
                    last_user_message TEXT NOT NULL DEFAULT '',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    asset_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_snapshots_updated_at
                ON memory_snapshots(updated_at)
                """
            )
            connection.commit()

    def load(self, conversation_id: str) -> MemorySnapshot | None:
        normalized_id = conversation_id or "default"
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT short_term_json, long_term_json
                FROM memory_snapshots
                WHERE conversation_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            return None
        short_term = ShortTermMemorySnapshot.model_validate_json(row["short_term_json"])
        long_term = LongTermMemorySnapshot.model_validate_json(row["long_term_json"])
        return MemorySnapshot(conversation_id=normalized_id, short_term=short_term, long_term=long_term)

    def save(self, snapshot: MemorySnapshot) -> Path:
        short_term = snapshot.short_term
        long_term = snapshot.long_term
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO memory_snapshots (
                    conversation_id,
                    short_term_json,
                    long_term_json,
                    session_title,
                    last_user_message,
                    message_count,
                    asset_count,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    short_term_json = excluded.short_term_json,
                    long_term_json = excluded.long_term_json,
                    session_title = excluded.session_title,
                    last_user_message = excluded.last_user_message,
                    message_count = excluded.message_count,
                    asset_count = excluded.asset_count,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.conversation_id or "default",
                    short_term.model_dump_json(),
                    long_term.model_dump_json(),
                    short_term.session_title,
                    short_term.last_user_message,
                    short_term.message_count,
                    short_term.asset_count,
                    short_term.updated_at,
                ),
            )
            connection.commit()
        return self.db_path

    def delete(self, conversation_id: str) -> bool:
        normalized_id = conversation_id or "default"
        with closing(self._connect()) as connection:
            cursor = connection.execute("DELETE FROM memory_snapshots WHERE conversation_id = ?", (normalized_id,))
            connection.commit()
        return cursor.rowcount > 0


class ShortTermMemoryStore:
    def __init__(self, paths: _MemoryPaths) -> None:
        self.paths = paths

    def load(self, conversation_id: str) -> ShortTermMemorySnapshot:
        path = self.paths.short_path_for(conversation_id)
        if not path.exists():
            return ShortTermMemorySnapshot(conversation_id=conversation_id or "default")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ShortTermMemorySnapshot.model_validate(raw)

    def save(self, snapshot: ShortTermMemorySnapshot) -> Path:
        path = self.paths.short_path_for(snapshot.conversation_id)
        ensure_directory(path.parent)
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        return path

    def delete(self, conversation_id: str) -> bool:
        path = self.paths.short_path_for(conversation_id)
        if not path.exists():
            return False
        path.unlink()
        return True


class LongTermMemoryStore:
    def __init__(self, paths: _MemoryPaths, llm_client: LLMClient | None = None) -> None:
        self.paths = paths
        self.llm_client = llm_client or LLMClient()

    def load(self, conversation_id: str) -> LongTermMemorySnapshot:
        path = self.paths.long_path_for(conversation_id)
        if not path.exists():
            return LongTermMemorySnapshot(conversation_id=conversation_id or "default")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LongTermMemorySnapshot.model_validate(raw)

    def save(self, snapshot: LongTermMemorySnapshot) -> Path:
        path = self.paths.long_path_for(snapshot.conversation_id)
        ensure_directory(path.parent)
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        return path

    def delete(self, conversation_id: str) -> bool:
        path = self.paths.long_path_for(conversation_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def summarize(
        self,
        *,
        conversation_id: str,
        messages: list[ConversationTurn],
        recognition_result: RecognitionResult | None,
        last_run_context: LastRunContext,
        previous_snapshot: LongTermMemorySnapshot | None = None,
        use_llm: bool = True,
    ) -> LongTermMemorySnapshot:
        previous = previous_snapshot or self.load(conversation_id)
        heuristics = self._build_heuristic_payload(messages, recognition_result, last_run_context, previous)
        llm_summary = self._maybe_build_llm_summary(messages, recognition_result, last_run_context, previous) if use_llm else ""
        compression_method = "llm_compaction" if llm_summary else "heuristic_compaction"
        return LongTermMemorySnapshot(
            conversation_id=conversation_id,
            summary_version="v1",
            strategic_summary=llm_summary or heuristics["strategic_summary"],
            salient_facts=heuristics["salient_facts"],
            research_topics=heuristics["research_topics"],
            completed_run_summaries=heuristics["completed_run_summaries"],
            open_questions=heuristics["open_questions"],
            preferred_tools=heuristics["preferred_tools"],
            user_preferences=heuristics["user_preferences"],
            retrieval_hints=heuristics["retrieval_hints"],
            compression_method=compression_method,
            source_message_count=len(messages),
        )

    @staticmethod
    def _build_heuristic_payload(
        messages: list[ConversationTurn],
        recognition_result: RecognitionResult | None,
        last_run_context: LastRunContext,
        previous_snapshot: LongTermMemorySnapshot,
    ) -> dict[str, list[str] | str]:
        user_messages = [turn.content for turn in messages if turn.role == "user" and turn.content.strip()]
        latest_user = _latest_user_message(messages)
        run_summary = _summarize_last_run(last_run_context)
        recognition_summary = _summarize_recognition_result(recognition_result)

        research_topics = _dedupe_keep_order(
            [
                last_run_context.system_name,
                last_run_context.request_summary,
                recognition_result.system if recognition_result else "",
                *previous_snapshot.research_topics,
            ],
            limit=8,
        )
        preferred_tools = _dedupe_keep_order(
            [
                last_run_context.selected_tool,
                last_run_context.generation_source,
                *previous_snapshot.preferred_tools,
            ],
            limit=6,
        )
        user_preferences = _extract_user_preferences(messages, previous_snapshot)
        completed_runs = _dedupe_keep_order(
            [
                run_summary,
                *previous_snapshot.completed_run_summaries,
            ],
            limit=8,
        )
        open_questions = _dedupe_keep_order(
            [
                latest_user if latest_user and "?" in latest_user else "",
                *[item for item in user_messages[-3:] if len(item) <= 180],
                *previous_snapshot.open_questions,
            ],
            limit=6,
        )
        salient_facts = _dedupe_keep_order(
            [
                recognition_summary,
                run_summary,
                f"recent_user={latest_user}" if latest_user else "",
                *user_preferences[:3],
                *previous_snapshot.salient_facts,
            ],
            limit=12,
        )
        retrieval_hints = _build_retrieval_hints(
            previous_snapshot=previous_snapshot,
            recognition_result=recognition_result,
            last_run_context=last_run_context,
        )
        strategic_summary_parts = [
            previous_snapshot.strategic_summary,
            f"Latest focus: {latest_user}" if latest_user else "",
            f"Recognized: {recognition_summary}" if recognition_summary else "",
            f"Last run: {run_summary}" if run_summary else "",
            f"Preferences: {' | '.join(user_preferences[:2])}" if user_preferences else "",
        ]
        strategic_summary = " | ".join(part for part in strategic_summary_parts if part).strip(" |")
        strategic_summary = _compact_text(strategic_summary, limit=520)
        return {
            "strategic_summary": strategic_summary,
            "salient_facts": salient_facts,
            "research_topics": research_topics,
            "completed_run_summaries": completed_runs,
            "open_questions": open_questions,
            "preferred_tools": preferred_tools,
            "user_preferences": user_preferences,
            "retrieval_hints": retrieval_hints,
        }

    def retrieve(
        self,
        *,
        query: str,
        snapshot: LongTermMemorySnapshot,
        limit: int = 6,
    ) -> list[str]:
        return build_long_term_memory_hits(query=query, snapshot=snapshot, limit=limit)

    def _maybe_build_llm_summary(
        self,
        messages: list[ConversationTurn],
        recognition_result: RecognitionResult | None,
        last_run_context: LastRunContext,
        previous_snapshot: LongTermMemorySnapshot,
    ) -> str:
        if not self.llm_client.is_configured():
            return ""
        try:
            content = self.llm_client.chat_text(
                system_prompt=(
                    "Compress this materials-research conversation into a long-term memory summary. "
                    "Write concise Chinese prose under 180 characters. "
                    "Keep only durable facts: research systems, validated runs, user preferences, unresolved questions. "
                    "Do not repeat transient phrasing."
                ),
                user_prompt=(
                    f"Previous long-term summary:\n{previous_snapshot.strategic_summary or '(none)'}\n\n"
                    f"Recent conversation:\n{json.dumps([turn.model_dump(mode='json') for turn in messages[-8:]], ensure_ascii=False)}\n\n"
                    f"Recognition summary:\n{_summarize_recognition_result(recognition_result) or '(none)'}\n\n"
                    f"Last run summary:\n{_summarize_last_run(last_run_context) or '(none)'}\n\n"
                    "Return only the compressed long-term memory summary."
                ),
                max_tokens=180,
                temperature=0.1,
                capability=LLMCapability.MEMORY_SUMMARY,
            )
        except Exception:  # noqa: BLE001
            return ""
        return _compact_text(content, limit=180)


class MemoryStore:
    def __init__(self, root_dir: Path | None = None, llm_client: LLMClient | None = None) -> None:
        self.paths = _MemoryPaths((root_dir or settings.tmp_dir) / settings.memory_dir_name)
        self.sqlite_store = SQLiteMemoryStore(self.paths)
        self.short_term_store = ShortTermMemoryStore(self.paths)
        self.long_term_store = LongTermMemoryStore(self.paths, llm_client=llm_client)

    def _legacy_to_short_term(self, conversation_id: str, raw: dict[str, object]) -> ShortTermMemorySnapshot:
        payload = {**raw}
        payload["conversation_id"] = conversation_id
        return ShortTermMemorySnapshot.model_validate(payload)

    def load(self, conversation_id: str) -> MemorySnapshot:
        sqlite_snapshot = self.sqlite_store.load(conversation_id)
        if sqlite_snapshot is not None:
            return sqlite_snapshot

        short_path = self.paths.short_path_for(conversation_id)
        long_path = self.paths.long_path_for(conversation_id)
        legacy_path = self.paths.legacy_path_for(conversation_id)

        short_term = self.short_term_store.load(conversation_id) if short_path.exists() else None
        long_term = self.long_term_store.load(conversation_id) if long_path.exists() else None

        if short_term is None and legacy_path.exists():
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
            short_term = self._legacy_to_short_term(conversation_id, raw)
            long_term = self.long_term_store.summarize(
                conversation_id=conversation_id,
                messages=short_term.messages,
                recognition_result=short_term.recognition_result,
                last_run_context=short_term.last_run_context,
                previous_snapshot=LongTermMemorySnapshot(conversation_id=conversation_id),
            )

        snapshot = MemorySnapshot(
            conversation_id=conversation_id or "default",
            short_term=short_term or ShortTermMemorySnapshot(conversation_id=conversation_id or "default"),
            long_term=long_term or LongTermMemorySnapshot(conversation_id=conversation_id or "default"),
        )
        if short_term is not None or long_term is not None:
            self.sqlite_store.save(snapshot)
        return snapshot

    def save(self, snapshot: MemorySnapshot) -> dict[str, Path]:
        sqlite_path = self.sqlite_store.save(snapshot)
        short_path = self.short_term_store.save(snapshot.short_term)
        long_path = self.long_term_store.save(snapshot.long_term)
        legacy_path = self.paths.legacy_path_for(snapshot.conversation_id)
        if legacy_path.exists():
            legacy_path.unlink()
        return {"sqlite": sqlite_path, "short_term": short_path, "long_term": long_path}

    def delete(self, conversation_id: str) -> bool:
        deleted_sqlite = self.sqlite_store.delete(conversation_id)
        deleted_short = self.short_term_store.delete(conversation_id)
        deleted_long = self.long_term_store.delete(conversation_id)
        legacy_path = self.paths.legacy_path_for(conversation_id)
        if legacy_path.exists():
            legacy_path.unlink()
            return True
        return deleted_sqlite or deleted_short or deleted_long

    def profile(self, conversation_id: str = "default") -> dict[str, object]:
        snapshot = self.load(conversation_id)
        short_path = self.paths.short_path_for(conversation_id)
        long_path = self.paths.long_path_for(conversation_id)
        sqlite_exists = self.paths.sqlite_path.exists()
        return {
            "conversation_id": snapshot.conversation_id,
            "storage": {
                "primary": "sqlite",
                "sqlite_path": str(self.paths.sqlite_path),
                "sqlite_exists": sqlite_exists,
                "json_backups": {
                    "short_term_path": str(short_path),
                    "short_term_exists": short_path.exists(),
                    "long_term_path": str(long_path),
                    "long_term_exists": long_path.exists(),
                },
            },
            "short_term": {
                "module": "ShortTermMemoryStore",
                "purpose": "recent turns, uploaded assets, recognition result, last run context",
                "retention_policy": {
                    "messages": "last 200 messages",
                    "uploaded_assets": "last 6 assets",
                },
                "message_count": snapshot.short_term.message_count,
                "stored_message_count": len(snapshot.short_term.messages),
                "asset_count": snapshot.short_term.asset_count,
                "stored_asset_count": len(snapshot.short_term.uploaded_assets),
                "has_recognition_result": snapshot.short_term.recognition_result is not None,
                "has_last_run_context": bool(snapshot.short_term.last_run_context.run_id),
                "summary_version": snapshot.short_term.summary_version,
                "updated_at": snapshot.short_term.updated_at,
            },
            "long_term": {
                "module": "LongTermMemoryStore",
                "purpose": "compressed durable research facts, topics, completed run summaries, preferences, open questions",
                "compression_method": snapshot.long_term.compression_method,
                "summary_version": snapshot.long_term.summary_version,
                "source_message_count": snapshot.long_term.source_message_count,
                "strategic_summary_length": len(snapshot.long_term.strategic_summary),
                "salient_fact_count": len(snapshot.long_term.salient_facts),
                "research_topic_count": len(snapshot.long_term.research_topics),
                "completed_run_summary_count": len(snapshot.long_term.completed_run_summaries),
                "open_question_count": len(snapshot.long_term.open_questions),
                "user_preference_count": len(snapshot.long_term.user_preferences),
                "retrieval_hint_count": len(snapshot.long_term.retrieval_hints),
                "updated_at": snapshot.long_term.updated_at,
            },
        }

    def merge_request(self, request: AgentChatRequest) -> MemorySnapshot:
        snapshot = self.load(request.conversation_id)
        merged_messages = [*snapshot.messages]
        if request.conversation_history:
            merged_messages = request.conversation_history
        merged_assets = [*snapshot.uploaded_assets]
        if request.uploaded_assets:
            merged_assets = request.uploaded_assets
        last_run_context = request.last_run_context if request.last_run_context.run_id else snapshot.last_run_context
        recognition_result = snapshot.recognition_result
        short_summary = self.summarize_short_term(
            merged_messages,
            last_run_context,
            recognition_result=recognition_result,
        )
        # Loading a request must be read-only and cheap. Long-term memory was
        # already persisted after the previous turn; recomputing it here caused
        # an unnecessary LLM call before routing even started.
        long_term = snapshot.long_term
        return MemorySnapshot(
            conversation_id=request.conversation_id,
            short_term=ShortTermMemorySnapshot(
                conversation_id=request.conversation_id,
                messages=merged_messages,
                uploaded_assets=merged_assets,
                recognition_result=recognition_result,
                last_run_context=last_run_context,
                session_title=_build_session_title(merged_messages, last_run_context),
                last_user_message=_latest_user_message(merged_messages),
                message_count=len(merged_messages),
                asset_count=len(merged_assets),
                current_context_summary=self._combine_summaries(short_summary, long_term),
            ),
            long_term=long_term,
        )

    def retrieve_long_term_context(
        self,
        *,
        query: str,
        snapshot: MemorySnapshot | None = None,
        conversation_id: str = "default",
        limit: int = 6,
    ) -> list[str]:
        active_snapshot = snapshot or self.load(conversation_id)
        return self.long_term_store.retrieve(query=query, snapshot=active_snapshot.long_term, limit=limit)

    def build_next_snapshot(
        self,
        *,
        conversation_id: str,
        messages: list[ConversationTurn],
        uploaded_assets: list[UploadedAsset],
        recognition_result: RecognitionResult | None,
        last_run_context: LastRunContext,
        current_context_summary: str,
        previous_snapshot: MemorySnapshot | None = None,
        long_term_snapshot: LongTermMemorySnapshot | None = None,
    ) -> MemorySnapshot:
        previous = previous_snapshot or self.load(conversation_id)
        short_summary = self.summarize_short_term(
            messages,
            last_run_context,
            recognition_result=recognition_result,
        )
        long_term = long_term_snapshot or self.long_term_store.summarize(
            conversation_id=conversation_id,
            messages=messages,
            recognition_result=recognition_result,
            last_run_context=last_run_context,
            previous_snapshot=previous.long_term,
            use_llm=False,
        )
        combined_summary = self._combine_summaries(short_summary, long_term)
        return MemorySnapshot(
            conversation_id=conversation_id,
            short_term=ShortTermMemorySnapshot(
                conversation_id=conversation_id,
                messages=messages[-200:],
                uploaded_assets=uploaded_assets[-6:],
                recognition_result=recognition_result,
                last_run_context=last_run_context,
                session_title=_build_session_title(messages, last_run_context),
                last_user_message=_latest_user_message(messages),
                message_count=len(messages),
                asset_count=len(uploaded_assets),
                current_context_summary=combined_summary,
            ),
            long_term=long_term,
        )

    @staticmethod
    def _combine_summaries(short_summary: str, long_term: LongTermMemorySnapshot) -> str:
        parts: list[str] = []
        if short_summary:
            parts.append(f"ShortTerm: {short_summary}")
        if long_term.strategic_summary:
            parts.append(f"LongTerm: {long_term.strategic_summary}")
        if long_term.research_topics:
            parts.append(f"Topics: {', '.join(long_term.research_topics[:6])}")
        if long_term.salient_facts:
            parts.append(f"Facts: {' | '.join(long_term.salient_facts[:4])}")
        if long_term.user_preferences:
            parts.append(f"Preferences: {' | '.join(long_term.user_preferences[:3])}")
        return _compact_text(" | ".join(parts), limit=3800)

    def summarize_short_term(
        self,
        messages: list[ConversationTurn],
        last_run_context: LastRunContext,
        *,
        recognition_result: RecognitionResult | None = None,
    ) -> str:
        parts: list[str] = []
        title = _build_session_title(messages, last_run_context)
        if title:
            parts.append(f"Session: {title}")
        base = _summarize_messages(messages)
        if base:
            parts.append(f"RecentTurns: {base}")
        recognition_summary = _summarize_recognition_result(recognition_result)
        if recognition_summary:
            parts.append(f"Recognition: {recognition_summary}")
        run_summary = _summarize_last_run(last_run_context)
        if run_summary:
            parts.append(f"LastRun: {run_summary}")
        return " | ".join(parts)

    def summarize(
        self,
        messages: list[ConversationTurn],
        last_run_context: LastRunContext,
        *,
        recognition_result: RecognitionResult | None = None,
        previous_long_term: LongTermMemorySnapshot | None = None,
        conversation_id: str = "default",
    ) -> str:
        summary, _long_term = self.summarize_with_snapshot(
            messages,
            last_run_context,
            recognition_result=recognition_result,
            previous_long_term=previous_long_term,
            conversation_id=conversation_id,
        )
        return summary

    def summarize_with_snapshot(
        self,
        messages: list[ConversationTurn],
        last_run_context: LastRunContext,
        *,
        recognition_result: RecognitionResult | None = None,
        previous_long_term: LongTermMemorySnapshot | None = None,
        conversation_id: str = "default",
    ) -> tuple[str, LongTermMemorySnapshot]:
        short_summary = self.summarize_short_term(messages, last_run_context, recognition_result=recognition_result)
        previous = previous_long_term or LongTermMemorySnapshot(conversation_id=conversation_id)
        new_message_count = max(0, len(messages) - previous.source_message_count)
        total_chars = sum(len(turn.content) for turn in messages[-24:])
        # LLM compaction is a periodic maintenance operation, not a per-request
        # dependency. Heuristic summaries remain available on every turn.
        use_llm = len(messages) >= 12 and (new_message_count >= 6 or total_chars >= 12_000)
        long_term = self.long_term_store.summarize(
            conversation_id=conversation_id,
            messages=messages,
            recognition_result=recognition_result,
            last_run_context=last_run_context,
            previous_snapshot=previous,
            use_llm=use_llm,
        )
        return self._combine_summaries(short_summary, long_term), long_term
