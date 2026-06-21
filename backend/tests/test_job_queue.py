from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from app.api import build_app_dependencies, create_app
from app.core.artifacts import ArtifactService
from app.jobs import AgentJobStore, AgentJobWorker
from app.memory import MemoryStore
from app.state import AgentChatRequest, AgentRunResponse, AgentStreamEvent, TaskRoute


def _fake_runner(request: AgentChatRequest, event_sink=None) -> AgentRunResponse:
    route = TaskRoute(name="conversation.answer", compute_domain="none", selected_tool="chat")
    response = AgentRunResponse(
        success=True,
        run_id="run-job-test",
        conversation_id=request.conversation_id,
        route=route,
        final_message=f"queued response: {request.message}",
        metadata={"unit_test": True},
    )
    if event_sink:
        event_sink(
            AgentStreamEvent(
                type="run_started",
                run_id=response.run_id,
                payload={"route": route.model_dump(mode="json"), "message": request.message},
            )
        )
        event_sink(
            AgentStreamEvent(
                type="run_completed",
                run_id=response.run_id,
                payload={"response": response.model_dump(mode="json", exclude={"html_content"})},
            )
        )
    return response


class AgentJobQueueTests(unittest.TestCase):
    def test_worker_processes_agent_chat_job_and_persists_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentJobStore(root_dir=Path(tmp))
            worker = AgentJobWorker(store=store, runner=_fake_runner, poll_interval_seconds=0.01)
            worker.start()
            try:
                record = worker.submit_agent_chat(
                    AgentChatRequest(
                        conversation_id="conv-job",
                        message="解释一下 Al-Zn 相图",
                    )
                )

                deadline = time.time() + 3
                latest = store.get(record.job_id)
                while latest and latest.status not in {"completed", "failed", "cancelled"} and time.time() < deadline:
                    time.sleep(0.02)
                    latest = store.get(record.job_id)

                self.assertIsNotNone(latest)
                self.assertEqual(latest.status, "completed")
                self.assertEqual(latest.run_id, "run-job-test")
                self.assertEqual(latest.result_run_id, "run-job-test")
                self.assertEqual(latest.progress_percent, 100)

                events = store.events_after(record.job_id)
                self.assertEqual([item.event.type for item in events], ["run_started", "run_completed"])
                self.assertEqual(events[0].event.run_id, "run-job-test")
            finally:
                worker.stop()

    def test_cancel_marks_queued_job_without_running_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentJobStore(root_dir=Path(tmp))
            worker = AgentJobWorker(store=store, runner=_fake_runner, poll_interval_seconds=0.01)

            record = worker.submit_agent_chat(
                AgentChatRequest(
                    conversation_id="conv-cancel",
                    message="跑一个很长的 LAMMPS",
                )
            )
            cancelled = worker.cancel(record.job_id)

            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(cancelled.progress_percent, 100)
            self.assertEqual(store.events_after(record.job_id), [])

    def test_job_http_endpoints_submit_and_stream_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_service = ArtifactService(root_dir=root / "outputs")
            store = AgentJobStore(root_dir=root / "jobs")
            worker = AgentJobWorker(store=store, runner=_fake_runner, poll_interval_seconds=0.01)
            dependencies = build_app_dependencies(
                artifact_service=artifact_service,
                memory_store=MemoryStore(root_dir=root / "memory"),
                job_store=store,
                job_worker=worker,
            )
            app = create_app(dependencies)

            with TestClient(app) as client:
                response = client.post(
                    "/api/jobs/agent-chat",
                    json=AgentChatRequest(
                        conversation_id="conv-http",
                        message="用 job API 测试一次",
                    ).model_dump(mode="json"),
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["job_id"]

                deadline = time.time() + 3
                status_payload = client.get(f"/api/jobs/{job_id}").json()
                while status_payload["status"] not in {"completed", "failed", "cancelled"} and time.time() < deadline:
                    time.sleep(0.02)
                    status_payload = client.get(f"/api/jobs/{job_id}").json()

                self.assertEqual(status_payload["status"], "completed")
                events_response = client.get(f"/api/jobs/{job_id}/events")
                self.assertEqual(events_response.status_code, 200)
                self.assertIn("event: run_started", events_response.text)
                self.assertIn("event: run_completed", events_response.text)


if __name__ == "__main__":
    unittest.main()
