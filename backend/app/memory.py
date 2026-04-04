from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from app.config import settings
from app.state import AgentChatRequest, ConversationTurn, LastRunContext, MemorySnapshot, RecognitionResult, UploadedAsset
from app.utils.path_utils import ensure_directory


def _compact_text(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 1, 1)].rstrip()}…"


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


class MemoryStore:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = ensure_directory((root_dir or settings.tmp_dir) / settings.memory_dir_name)

    @staticmethod
    def _normalize_conversation_id(conversation_id: str) -> str:
        raw = conversation_id.strip() or "default"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
        if safe == raw and safe:
            return safe
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        prefix = safe[:36] or "conversation"
        return f"{prefix}_{digest}"

    def _path_for(self, conversation_id: str) -> Path:
        normalized = self._normalize_conversation_id(conversation_id)
        return self.root_dir / f"{normalized}.json"

    def load(self, conversation_id: str) -> MemorySnapshot:
        path = self._path_for(conversation_id)
        if not path.exists():
            return MemorySnapshot(conversation_id=conversation_id or "default")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return MemorySnapshot.model_validate(raw)

    def save(self, snapshot: MemorySnapshot) -> Path:
        path = self._path_for(snapshot.conversation_id)
        ensure_directory(path.parent)
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        return path

    def delete(self, conversation_id: str) -> bool:
        path = self._path_for(conversation_id)
        if not path.exists():
            return False
        path.unlink()
        return True

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
        summary = self.summarize(
            merged_messages,
            last_run_context,
            recognition_result=recognition_result,
        )
        return MemorySnapshot(
            conversation_id=request.conversation_id,
            messages=merged_messages,
            uploaded_assets=merged_assets,
            recognition_result=recognition_result,
            last_run_context=last_run_context,
            session_title=_build_session_title(merged_messages, last_run_context),
            last_user_message=_latest_user_message(merged_messages),
            message_count=len(merged_messages),
            asset_count=len(merged_assets),
            current_context_summary=summary,
        )

    def build_next_snapshot(
        self,
        *,
        conversation_id: str,
        messages: list[ConversationTurn],
        uploaded_assets: list[UploadedAsset],
        recognition_result: RecognitionResult | None,
        last_run_context: LastRunContext,
        current_context_summary: str,
    ) -> MemorySnapshot:
        summary = current_context_summary or self.summarize(
            messages,
            last_run_context,
            recognition_result=recognition_result,
        )
        return MemorySnapshot(
            conversation_id=conversation_id,
            messages=messages[-20:],
            uploaded_assets=uploaded_assets[-6:],
            recognition_result=recognition_result,
            last_run_context=last_run_context,
            session_title=_build_session_title(messages, last_run_context),
            last_user_message=_latest_user_message(messages),
            message_count=len(messages),
            asset_count=len(uploaded_assets),
            current_context_summary=summary,
        )

    def summarize(
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
