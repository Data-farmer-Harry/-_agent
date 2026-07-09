from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Condition, Event, Lock, Thread
import sqlite3
import time
import uuid
from typing import Any, Callable

from app.core.cancellation import cancel_run
from app.core.observability import log_event
from app.state import AgentChatRequest, AgentJobEventRecord, AgentJobRecord, AgentRunResponse, AgentStreamEvent, RunStatus
from app.utils.path_utils import ensure_directory


TerminalJobStatus = {"completed", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_request_summary(request: AgentChatRequest) -> str:
    text = " ".join((request.message or "").strip().split())
    if len(text) <= 160:
        return text
    return f"{text[:157].rstrip()}..."


def _status_from_response(response: AgentRunResponse) -> RunStatus:
    if response.run_status in TerminalJobStatus:
        return response.run_status
    return "completed" if response.success else "failed"


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _payload_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _metadata_mapping(metadata: dict[str, Any], key: str) -> dict[str, Any]:
    value = metadata.get(key)
    return value if isinstance(value, dict) else {}


def _checkpoint_node_hashes(metadata: dict[str, Any], node_id: str) -> dict[str, str]:
    result_hashes = _metadata_mapping(metadata, "node_result_hashes")
    content_hashes = _metadata_mapping(metadata, "node_content_hashes")
    input_hashes = _metadata_mapping(metadata, "node_input_hashes")
    node_fingerprints = _metadata_mapping(metadata, "node_fingerprints")
    reuse_status = _metadata_mapping(metadata, "node_reuse_status")
    return {
        "input_hash": str(input_hashes.get(node_id) or ""),
        "result_hash": str(result_hashes.get(node_id) or ""),
        "content_hash": str(content_hashes.get(node_id) or result_hashes.get(node_id) or ""),
        "node_fingerprint": str(node_fingerprints.get(node_id) or ""),
        "reuse_status": str(reuse_status.get(node_id) or ""),
    }


def _event_progress(event: AgentStreamEvent, current_count: int) -> tuple[int | None, str, str]:
    payload = event.payload or {}
    if event.type == "run_started":
        return None, "running", "任务已被 worker 接管，正在进入 agent 编排。"
    if event.type == "step_started":
        step = payload.get("plan_step") if isinstance(payload, dict) else None
        if isinstance(step, dict):
            tool = str(step.get("tool_name") or "agent_step")
            description = str(step.get("description") or "")
            return None, tool, description or f"正在执行 {tool}。"
        return None, "running", "正在执行下一步。"
    if event.type in {"step_completed", "step_failed"}:
        observation = payload.get("observation") if isinstance(payload, dict) else None
        if isinstance(observation, dict):
            tool = str(observation.get("tool_name") or "agent_step")
            summary = str(observation.get("summary") or "")
            return None, tool, summary or f"{tool} 已更新。"
        return None, "running", f"已收到第 {current_count + 1} 条执行事件。"
    if event.type == "lifecycle_event":
        event_type = str(payload.get("event_type") or "lifecycle.event")
        to_state = str(payload.get("to_state") or payload.get("metadata", {}).get("to_state") or "").strip()
        reason = str(payload.get("reason") or payload.get("metadata", {}).get("reason") or "").strip()
        stage = to_state or event_type
        message = f"{event_type}{f' → {to_state}' if to_state else ''}{f': {reason}' if reason else ''}"
        return None, stage, message
    if event.type == "dag_event":
        event_type = str(payload.get("metadata", {}).get("event") or payload.get("event_type") or "dag.event")
        node_id = str(payload.get("metadata", {}).get("node_id") or payload.get("node_id") or "").strip()
        message = f"{event_type}{f' {node_id}' if node_id else ''}"
        return None, node_id or event_type, message
    if event.type == "checkpoint_saved":
        stage = str(payload.get("stage") or "checkpoint")
        checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
        message = f"已保存 checkpoint{f' {checkpoint_id}' if checkpoint_id else ''}（{stage}）。"
        return None, stage, message
    if event.type == "run_completed":
        return 100, "completed", "任务已完成，结果已写入 run artifact。"
    if event.type == "run_error":
        return 100, "failed", str(payload.get("message") or "任务执行失败。")
    return None, "running", "任务状态已更新。"


class AgentJobStore:
    """SQLite-backed durable queue and event log for long-running agent jobs."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = ensure_directory(root_dir)
        self.db_path = self.root_dir / "agent_jobs.sqlite3"
        self._lock = Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        ensure_directory(self.db_path.parent)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL DEFAULT '',
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    progress_percent INTEGER,
                    progress_stage TEXT NOT NULL DEFAULT '',
                    progress_message TEXT NOT NULL DEFAULT '',
                    request_summary TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL,
                    result_run_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    event_count INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    source_job_id TEXT NOT NULL DEFAULT '',
                    source_run_id TEXT NOT NULL DEFAULT '',
                    source_checkpoint_id TEXT NOT NULL DEFAULT '',
                    resume_mode TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_jobs_status_created
                ON agent_jobs(status, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_jobs_conversation_updated
                ON agent_jobs(conversation_id, updated_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    emitted_at TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_job_events_job_event
                ON agent_job_events(job_id, event_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_job_plans (
                    job_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    created_from TEXT NOT NULL DEFAULT '',
                    node_ids_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (job_id, plan_id, plan_version),
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_job_checkpoints (
                    job_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    plan_id TEXT NOT NULL DEFAULT '',
                    plan_version INTEGER NOT NULL DEFAULT 1,
                    stage TEXT NOT NULL DEFAULT '',
                    lifecycle_state TEXT NOT NULL DEFAULT '',
                    node_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    result_status TEXT NOT NULL DEFAULT '',
                    completed_nodes_json TEXT NOT NULL DEFAULT '[]',
                    failed_nodes_json TEXT NOT NULL DEFAULT '[]',
                    timed_out_nodes_json TEXT NOT NULL DEFAULT '[]',
                    skipped_nodes_json TEXT NOT NULL DEFAULT '[]',
                    pending_nodes_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (job_id, checkpoint_id),
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_job_tasks (
                    job_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL DEFAULT '',
                    plan_version INTEGER NOT NULL DEFAULT 1,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    checkpoint_id TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    input_hash TEXT NOT NULL DEFAULT '',
                    result_hash TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    node_fingerprint TEXT NOT NULL DEFAULT '',
                    reuse_status TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (job_id, plan_id, plan_version, node_id),
                    FOREIGN KEY(job_id) REFERENCES agent_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_job_checkpoints_job_created
                ON agent_job_checkpoints(job_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_job_tasks_job_status
                ON agent_job_tasks(job_id, status)
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(agent_jobs)").fetchall()
            }
            if "request_id" not in existing_columns:
                connection.execute("ALTER TABLE agent_jobs ADD COLUMN request_id TEXT NOT NULL DEFAULT ''")
            migrations = {
                "attempt": "ALTER TABLE agent_jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1",
                "source_job_id": "ALTER TABLE agent_jobs ADD COLUMN source_job_id TEXT NOT NULL DEFAULT ''",
                "source_run_id": "ALTER TABLE agent_jobs ADD COLUMN source_run_id TEXT NOT NULL DEFAULT ''",
                "source_checkpoint_id": "ALTER TABLE agent_jobs ADD COLUMN source_checkpoint_id TEXT NOT NULL DEFAULT ''",
                "resume_mode": "ALTER TABLE agent_jobs ADD COLUMN resume_mode TEXT NOT NULL DEFAULT ''",
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    connection.execute(statement)
            existing_task_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(agent_job_tasks)").fetchall()
            }
            task_migrations = {
                "input_hash": "ALTER TABLE agent_job_tasks ADD COLUMN input_hash TEXT NOT NULL DEFAULT ''",
                "result_hash": "ALTER TABLE agent_job_tasks ADD COLUMN result_hash TEXT NOT NULL DEFAULT ''",
                "content_hash": "ALTER TABLE agent_job_tasks ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
                "node_fingerprint": "ALTER TABLE agent_job_tasks ADD COLUMN node_fingerprint TEXT NOT NULL DEFAULT ''",
                "reuse_status": "ALTER TABLE agent_job_tasks ADD COLUMN reuse_status TEXT NOT NULL DEFAULT ''",
            }
            for column, statement in task_migrations.items():
                if column not in existing_task_columns:
                    connection.execute(statement)
            connection.commit()

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> AgentJobRecord:
        return AgentJobRecord(
            job_id=row["job_id"],
            request_id=row["request_id"],
            job_type=row["job_type"],
            status=row["status"],
            conversation_id=row["conversation_id"],
            run_id=row["run_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            progress_percent=row["progress_percent"],
            progress_stage=row["progress_stage"],
            progress_message=row["progress_message"],
            request_summary=row["request_summary"],
            result_run_id=row["result_run_id"],
            error=row["error"],
            event_count=row["event_count"],
            attempt=row["attempt"],
            source_job_id=row["source_job_id"],
            source_run_id=row["source_run_id"],
            source_checkpoint_id=row["source_checkpoint_id"],
            resume_mode=row["resume_mode"],
        )

    def create_agent_chat_job(
        self,
        request: AgentChatRequest,
        *,
        job_type: str = "agent_chat",
        attempt: int = 1,
        source_job_id: str = "",
        source_run_id: str = "",
        source_checkpoint_id: str = "",
        resume_mode: str = "",
    ) -> AgentJobRecord:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        normalized_attempt = max(1, int(attempt or 1))
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO agent_jobs (
                    job_id,
                    request_id,
                    job_type,
                    status,
                    conversation_id,
                    created_at,
                    updated_at,
                    progress_percent,
                    progress_stage,
                    progress_message,
                    request_summary,
                    request_json,
                    attempt,
                    source_job_id,
                    source_run_id,
                    source_checkpoint_id,
                    resume_mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.request_id,
                    job_type,
                    "queued",
                    request.conversation_id or "default",
                    now,
                    now,
                    None,
                    "queued",
                    "等待后台 worker 执行。",
                    _compact_request_summary(request),
                    request.model_dump_json(),
                    normalized_attempt,
                    source_job_id,
                    source_run_id,
                    source_checkpoint_id,
                    resume_mode,
                ),
            )
            connection.commit()
        log_event(
            "job.created",
            request_id=request.request_id,
            job_id=job_id,
            conversation_id=request.conversation_id,
            message=_compact_request_summary(request),
            job_type=job_type,
            attempt=normalized_attempt,
            source_job_id=source_job_id,
            source_run_id=source_run_id,
            source_checkpoint_id=source_checkpoint_id,
            resume_mode=resume_mode,
        )
        record = self.get(job_id)
        if record is None:
            raise RuntimeError("Failed to create agent job.")
        return record

    def get(self, job_id: str) -> AgentJobRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._record_from_row(row) if row else None

    def load_request(self, job_id: str) -> AgentChatRequest | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT request_json FROM agent_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return AgentChatRequest.model_validate_json(row["request_json"])

    def list_job_plans(self, job_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_job_plans
                WHERE job_id = ?
                ORDER BY plan_version ASC, created_at ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "plan_id": row["plan_id"],
                "plan_version": row["plan_version"],
                "created_at": row["created_at"],
                "created_from": row["created_from"],
                "node_ids": _json_loads(row["node_ids_json"], []),
                "metadata": _json_loads(row["metadata_json"], {}),
            }
            for row in rows
        ]

    def list_job_checkpoints(self, job_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_job_checkpoints
                WHERE job_id = ?
                ORDER BY created_at ASC, checkpoint_id ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "checkpoint_id": row["checkpoint_id"],
                "run_id": row["run_id"],
                "plan_id": row["plan_id"],
                "plan_version": row["plan_version"],
                "stage": row["stage"],
                "lifecycle_state": row["lifecycle_state"],
                "node_id": row["node_id"],
                "created_at": row["created_at"],
                "path": row["path"],
                "result_status": row["result_status"],
                "completed_nodes": _json_loads(row["completed_nodes_json"], []),
                "failed_nodes": _json_loads(row["failed_nodes_json"], []),
                "timed_out_nodes": _json_loads(row["timed_out_nodes_json"], []),
                "skipped_nodes": _json_loads(row["skipped_nodes_json"], []),
                "pending_nodes": _json_loads(row["pending_nodes_json"], []),
                "metadata": _json_loads(row["metadata_json"], {}),
            }
            for row in rows
        ]

    def list_job_tasks(self, job_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_job_tasks
                WHERE job_id = ?
                ORDER BY plan_version ASC, node_id ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "plan_id": row["plan_id"],
                "plan_version": row["plan_version"],
                "node_id": row["node_id"],
                "status": row["status"],
                "checkpoint_id": row["checkpoint_id"],
                "updated_at": row["updated_at"],
                "input_hash": row["input_hash"],
                "result_hash": row["result_hash"],
                "content_hash": row["content_hash"],
                "node_fingerprint": row["node_fingerprint"],
                "reuse_status": row["reuse_status"],
                "metadata": _json_loads(row["metadata_json"], {}),
            }
            for row in rows
        ]

    def list_recent(self, *, limit: int = 50, conversation_id: str | None = None) -> list[AgentJobRecord]:
        limit = max(1, min(limit, 200))
        with closing(self._connect()) as connection:
            if conversation_id:
                rows = connection.execute(
                    """
                    SELECT * FROM agent_jobs
                    WHERE conversation_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM agent_jobs
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def claim_next(self) -> AgentJobRecord | None:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM agent_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE agent_jobs
                SET status = 'running',
                    started_at = ?,
                    updated_at = ?,
                    progress_percent = NULL,
                    progress_stage = 'running',
                    progress_message = '任务已进入后台 worker。'
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, now, row["job_id"]),
            )
            connection.commit()
        return self.get(row["job_id"])

    def append_event(self, job_id: str, event: AgentStreamEvent) -> int:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            current_row = connection.execute(
                "SELECT event_count, run_id, request_id, conversation_id FROM agent_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if current_row is None:
                raise KeyError(f"Job not found: {job_id}")
            progress_percent, progress_stage, progress_message = _event_progress(event, int(current_row["event_count"]))
            run_id = event.run_id or current_row["run_id"] or ""
            cursor = connection.execute(
                """
                INSERT INTO agent_job_events (job_id, event_type, run_id, emitted_at, event_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    event.type,
                    run_id,
                    event.emitted_at,
                    event.model_dump_json(),
                ),
            )
            connection.execute(
                """
                UPDATE agent_jobs
                SET run_id = CASE WHEN run_id = '' THEN ? ELSE run_id END,
                    updated_at = ?,
                    progress_percent = ?,
                    progress_stage = ?,
                    progress_message = ?,
                    event_count = event_count + 1
                WHERE job_id = ?
                """,
                (run_id, now, progress_percent, progress_stage, progress_message, job_id),
            )
            self._persist_structured_job_event(connection, job_id=job_id, run_id=run_id, event=event)
            connection.commit()
            log_event(
                "job.event",
                request_id=current_row["request_id"],
                job_id=job_id,
                run_id=run_id,
                conversation_id=current_row["conversation_id"],
                message=progress_message,
                stream_event_type=event.type,
                progress_stage=progress_stage,
                progress_percent=progress_percent,
            )
            return int(cursor.lastrowid)

    def _persist_structured_job_event(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        run_id: str,
        event: AgentStreamEvent,
    ) -> None:
        payload = event.payload or {}
        if event.type == "lifecycle_event" and payload.get("event_type") == "plan.created":
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            plan_id = str(metadata.get("plan_id") or "").strip()
            if not plan_id:
                return
            try:
                plan_version = int(metadata.get("plan_version") or payload.get("plan_version") or 1)
            except (TypeError, ValueError):
                plan_version = 1
            node_ids = _payload_list(metadata, "node_ids")
            created_from = str(metadata.get("created_from") or "").strip()
            connection.execute(
                """
                INSERT OR REPLACE INTO agent_job_plans (
                    job_id, plan_id, plan_version, created_at, created_from, node_ids_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    plan_id,
                    plan_version,
                    event.emitted_at,
                    created_from,
                    _json_dumps(node_ids),
                    _json_dumps(metadata),
                ),
            )
            for node_id in node_ids:
                self._upsert_job_task(
                    connection,
                    job_id=job_id,
                    plan_id=plan_id,
                    plan_version=plan_version,
                    node_id=node_id,
                    status="pending",
                    checkpoint_id="",
                    updated_at=event.emitted_at,
                    metadata={"source": "plan.created"},
                )
            return

        if event.type != "checkpoint_saved":
            return
        checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
        if not checkpoint_id:
            return
        try:
            plan_version = int(payload.get("plan_version") or 1)
        except (TypeError, ValueError):
            plan_version = 1
        plan_id = str(payload.get("plan_id") or "").strip()
        created_at = str(payload.get("created_at") or event.emitted_at)
        completed = _payload_list(payload, "completed_nodes")
        failed = _payload_list(payload, "failed_nodes")
        timed_out = _payload_list(payload, "timed_out_nodes")
        skipped = _payload_list(payload, "skipped_nodes")
        pending = _payload_list(payload, "pending_nodes")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        connection.execute(
            """
            INSERT OR REPLACE INTO agent_job_checkpoints (
                job_id,
                checkpoint_id,
                run_id,
                plan_id,
                plan_version,
                stage,
                lifecycle_state,
                node_id,
                created_at,
                path,
                result_status,
                completed_nodes_json,
                failed_nodes_json,
                timed_out_nodes_json,
                skipped_nodes_json,
                pending_nodes_json,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                checkpoint_id,
                run_id or str(payload.get("run_id") or ""),
                plan_id,
                plan_version,
                str(payload.get("stage") or ""),
                str(payload.get("lifecycle_state") or ""),
                str(payload.get("node_id") or ""),
                created_at,
                str(payload.get("path") or ""),
                str(payload.get("result_status") or ""),
                _json_dumps(completed),
                _json_dumps(failed),
                _json_dumps(timed_out),
                _json_dumps(skipped),
                _json_dumps(pending),
                _json_dumps(metadata),
            ),
        )
        for status, nodes in (
            ("completed", completed),
            ("failed", failed),
            ("timed_out", timed_out),
            ("skipped", skipped),
            ("pending", pending),
        ):
            for node_id in nodes:
                node_hashes = _checkpoint_node_hashes(metadata, node_id)
                self._upsert_job_task(
                    connection,
                    job_id=job_id,
                    plan_id=plan_id,
                    plan_version=plan_version,
                    node_id=node_id,
                    status=status,
                    checkpoint_id=checkpoint_id,
                    updated_at=created_at,
                    input_hash=node_hashes["input_hash"],
                    result_hash=node_hashes["result_hash"],
                    content_hash=node_hashes["content_hash"],
                    node_fingerprint=node_hashes["node_fingerprint"],
                    reuse_status=node_hashes["reuse_status"],
                    metadata={
                        "source": "checkpoint_saved",
                        "stage": str(payload.get("stage") or ""),
                        "lifecycle_state": str(payload.get("lifecycle_state") or ""),
                        "fingerprint": node_hashes,
                    },
                )

    def _upsert_job_task(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        plan_id: str,
        plan_version: int,
        node_id: str,
        status: str,
        checkpoint_id: str,
        updated_at: str,
        input_hash: str = "",
        result_hash: str = "",
        content_hash: str = "",
        node_fingerprint: str = "",
        reuse_status: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO agent_job_tasks (
                job_id,
                plan_id,
                plan_version,
                node_id,
                status,
                checkpoint_id,
                updated_at,
                input_hash,
                result_hash,
                content_hash,
                node_fingerprint,
                reuse_status,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, plan_id, plan_version, node_id)
            DO UPDATE SET
                status = excluded.status,
                checkpoint_id = excluded.checkpoint_id,
                updated_at = excluded.updated_at,
                input_hash = excluded.input_hash,
                result_hash = excluded.result_hash,
                content_hash = excluded.content_hash,
                node_fingerprint = excluded.node_fingerprint,
                reuse_status = excluded.reuse_status,
                metadata_json = excluded.metadata_json
            """,
            (
                job_id,
                plan_id,
                plan_version,
                node_id,
                status,
                checkpoint_id,
                updated_at,
                input_hash,
                result_hash,
                content_hash,
                node_fingerprint,
                reuse_status,
                _json_dumps(metadata or {}),
            ),
        )

    def mark_completed(self, job_id: str, response: AgentRunResponse) -> AgentJobRecord:
        status = _status_from_response(response)
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE agent_jobs
                SET status = ?,
                    run_id = CASE WHEN run_id = '' THEN ? ELSE run_id END,
                    result_run_id = ?,
                    finished_at = ?,
                    updated_at = ?,
                    progress_percent = 100,
                    progress_stage = ?,
                    progress_message = ?,
                    error = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    response.run_id,
                    response.run_id,
                    now,
                    now,
                    status,
                    "任务已完成。" if status == "completed" else response.final_message or "任务执行失败。",
                    "" if status == "completed" else response.final_message,
                    job_id,
                ),
            )
            connection.commit()
        record = self.get(job_id)
        if record is None:
            raise KeyError(f"Job not found: {job_id}")
        log_event(
            "job.completed",
            request_id=record.request_id,
            job_id=job_id,
            run_id=record.result_run_id or record.run_id,
            conversation_id=record.conversation_id,
            message=record.progress_message,
            status=record.status,
        )
        return record

    def mark_failed(self, job_id: str, error: str) -> AgentJobRecord:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE agent_jobs
                SET status = 'failed',
                    finished_at = ?,
                    updated_at = ?,
                    progress_percent = 100,
                    progress_stage = 'failed',
                    progress_message = ?,
                    error = ?
                WHERE job_id = ?
                """,
                (now, now, error, error, job_id),
            )
            connection.commit()
        record = self.get(job_id)
        if record is None:
            raise KeyError(f"Job not found: {job_id}")
        log_event(
            "job.failed",
            level="error",
            request_id=record.request_id,
            job_id=job_id,
            run_id=record.run_id,
            conversation_id=record.conversation_id,
            message=error,
        )
        return record

    def mark_cancelled(self, job_id: str) -> AgentJobRecord:
        now = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE agent_jobs
                SET status = 'cancelled',
                    finished_at = CASE WHEN finished_at = '' THEN ? ELSE finished_at END,
                    updated_at = ?,
                    progress_percent = 100,
                    progress_stage = 'cancelled',
                    progress_message = '已发送取消请求。',
                    error = CASE WHEN error = '' THEN 'cancelled_requested' ELSE error END
                WHERE job_id = ?
                """,
                (now, now, job_id),
            )
            connection.commit()
        record = self.get(job_id)
        if record is None:
            raise KeyError(f"Job not found: {job_id}")
        log_event(
            "job.cancelled",
            request_id=record.request_id,
            job_id=job_id,
            run_id=record.run_id,
            conversation_id=record.conversation_id,
            message=record.progress_message,
        )
        return record

    def events_after(self, job_id: str, after_event_id: int = 0, *, limit: int = 100) -> list[AgentJobEventRecord]:
        limit = max(1, min(limit, 500))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id, job_id, event_json
                FROM agent_job_events
                WHERE job_id = ? AND event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (job_id, after_event_id, limit),
            ).fetchall()
        return [
            AgentJobEventRecord(
                event_id=int(row["event_id"]),
                job_id=row["job_id"],
                event=AgentStreamEvent.model_validate_json(row["event_json"]),
            )
            for row in rows
        ]


class AgentJobWorker:
    def __init__(
        self,
        *,
        store: AgentJobStore,
        runner: Callable[..., AgentRunResponse],
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.runner = runner
        self.poll_interval_seconds = poll_interval_seconds
        self._condition = Condition()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(target=self._loop, name="agent-job-worker", daemon=True)
            self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def submit_agent_chat(
        self,
        request: AgentChatRequest,
        *,
        job_type: str = "agent_chat",
        attempt: int = 1,
        source_job_id: str = "",
        source_run_id: str = "",
        source_checkpoint_id: str = "",
        resume_mode: str = "",
    ) -> AgentJobRecord:
        record = self.store.create_agent_chat_job(
            request,
            job_type=job_type,
            attempt=attempt,
            source_job_id=source_job_id,
            source_run_id=source_run_id,
            source_checkpoint_id=source_checkpoint_id,
            resume_mode=resume_mode,
        )
        with self._condition:
            self._condition.notify_all()
        return record

    def cancel(self, job_id: str) -> AgentJobRecord:
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(f"Job not found: {job_id}")
        if record.run_id:
            cancel_run(record.run_id)
        cancelled = self.store.mark_cancelled(job_id)
        with self._condition:
            self._condition.notify_all()
        return cancelled

    def notify_events(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def wait_for_events(self, timeout: float = 1.0) -> None:
        with self._condition:
            self._condition.wait(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            record = self.store.claim_next()
            if record is None:
                with self._condition:
                    self._condition.wait(timeout=self.poll_interval_seconds)
                continue
            self._run_record(record)

    def _run_record(self, record: AgentJobRecord) -> None:
        if record.status == "cancelled":
            return
        request = self.store.load_request(record.job_id)
        if request is None:
            self.store.mark_failed(record.job_id, "Job request payload is missing.")
            self.notify_events()
            return

        def emit(event: AgentStreamEvent) -> None:
            self.store.append_event(record.job_id, event)
            self.notify_events()

        try:
            current = self.store.get(record.job_id)
            if current and current.status == "cancelled":
                return
            response = self.runner(request, event_sink=emit)
            latest = self.store.get(record.job_id)
            if latest and latest.status == "cancelled":
                return
            self.store.mark_completed(record.job_id, response)
        except Exception as exc:  # noqa: BLE001
            latest = self.store.get(record.job_id)
            if latest and latest.status == "cancelled":
                return
            fallback_run_id = latest.run_id if latest else record.run_id or "pending"
            error_event = AgentStreamEvent(type="run_error", run_id=fallback_run_id, payload={"message": str(exc)})
            self.store.append_event(record.job_id, error_event)
            self.store.mark_failed(record.job_id, str(exc))
        finally:
            self.notify_events()
            time.sleep(0)
