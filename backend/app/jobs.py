from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Condition, Event, Lock, Thread
import sqlite3
import time
import uuid
from typing import Callable

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
                    event_count INTEGER NOT NULL DEFAULT 0
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
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(agent_jobs)").fetchall()
            }
            if "request_id" not in existing_columns:
                connection.execute("ALTER TABLE agent_jobs ADD COLUMN request_id TEXT NOT NULL DEFAULT ''")
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
        )

    def create_agent_chat_job(self, request: AgentChatRequest) -> AgentJobRecord:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
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
                    request_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request.request_id,
                    "agent_chat",
                    "queued",
                    request.conversation_id or "default",
                    now,
                    now,
                    None,
                    "queued",
                    "等待后台 worker 执行。",
                    _compact_request_summary(request),
                    request.model_dump_json(),
                ),
            )
            connection.commit()
        log_event(
            "job.created",
            request_id=request.request_id,
            job_id=job_id,
            conversation_id=request.conversation_id,
            message=_compact_request_summary(request),
            job_type="agent_chat",
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

    def submit_agent_chat(self, request: AgentChatRequest) -> AgentJobRecord:
        record = self.store.create_agent_chat_job(request)
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
