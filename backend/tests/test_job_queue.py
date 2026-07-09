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
                self.assertEqual(latest.attempt, 1)
                self.assertEqual(latest.source_job_id, "")

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

    def test_store_persists_lifecycle_dag_and_checkpoint_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentJobStore(root_dir=Path(tmp))
            record = store.create_agent_chat_job(
                AgentChatRequest(
                    conversation_id="conv-life-events",
                    message="跑一个 LAMMPS preflight DAG",
                )
            )

            store.append_event(
                record.job_id,
                AgentStreamEvent(
                    type="lifecycle_event",
                    run_id="run-life-events",
                    payload={
                        "event_type": "plan.created",
                        "plan_version": 1,
                        "metadata": {
                            "plan_id": "lammps-preflight/v1",
                            "plan_version": 1,
                            "node_ids": ["constraint_extract", "materials_rag_search", "preflight_merge"],
                            "created_from": "unit_test",
                        },
                    },
                ),
            )
            store.append_event(
                record.job_id,
                AgentStreamEvent(
                    type="lifecycle_event",
                    run_id="run-life-events",
                    payload={
                        "event_type": "lifecycle.transition",
                        "from_state": "planning",
                        "to_state": "preflight",
                        "reason": "unit_test",
                    },
                ),
            )
            store.append_event(
                record.job_id,
                AgentStreamEvent(
                    type="dag_event",
                    run_id="run-life-events",
                    payload={"metadata": {"event": "node_completed", "node_id": "materials_rag_search"}},
                ),
            )
            store.append_event(
                record.job_id,
                AgentStreamEvent(
                    type="checkpoint_saved",
                    run_id="run-life-events",
                    payload={
                        "checkpoint_id": "after-node",
                        "stage": "after_node_materials_rag_search",
                        "plan_id": "lammps-preflight/v1",
                        "plan_version": 1,
                        "lifecycle_state": "preflight",
                        "completed_nodes": ["constraint_extract", "materials_rag_search"],
                        "pending_nodes": ["preflight_merge"],
                        "path": "/tmp/checkpoint.json",
                        "metadata": {
                            "node_input_hashes": {
                                "constraint_extract": "input-a",
                                "materials_rag_search": "input-b",
                            },
                            "node_result_hashes": {
                                "constraint_extract": "result-a",
                                "materials_rag_search": "result-b",
                            },
                            "node_content_hashes": {
                                "constraint_extract": "content-a",
                                "materials_rag_search": "content-b",
                            },
                            "node_fingerprints": {
                                "constraint_extract": "fingerprint-a",
                                "materials_rag_search": "fingerprint-b",
                            },
                            "node_reuse_status": {
                                "constraint_extract": "fresh",
                                "materials_rag_search": "reused",
                            },
                        },
                    },
                ),
            )

            latest = store.get(record.job_id)
            events = store.events_after(record.job_id)
            plans = store.list_job_plans(record.job_id)
            checkpoints = store.list_job_checkpoints(record.job_id)
            tasks = store.list_job_tasks(record.job_id)

        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.run_id, "run-life-events")
        self.assertEqual(latest.event_count, 4)
        self.assertEqual(latest.progress_stage, "after_node_materials_rag_search")
        self.assertEqual([item.event.type for item in events], ["lifecycle_event", "lifecycle_event", "dag_event", "checkpoint_saved"])
        self.assertEqual(plans[0]["plan_id"], "lammps-preflight/v1")
        self.assertEqual(plans[0]["node_ids"], ["constraint_extract", "materials_rag_search", "preflight_merge"])
        self.assertEqual(checkpoints[0]["checkpoint_id"], "after-node")
        self.assertEqual(checkpoints[0]["completed_nodes"], ["constraint_extract", "materials_rag_search"])
        task_status = {item["node_id"]: item["status"] for item in tasks}
        self.assertEqual(task_status["constraint_extract"], "completed")
        self.assertEqual(task_status["materials_rag_search"], "completed")
        self.assertEqual(task_status["preflight_merge"], "pending")
        task_by_node = {item["node_id"]: item for item in tasks}
        self.assertEqual(task_by_node["constraint_extract"]["input_hash"], "input-a")
        self.assertEqual(task_by_node["constraint_extract"]["result_hash"], "result-a")
        self.assertEqual(task_by_node["constraint_extract"]["content_hash"], "content-a")
        self.assertEqual(task_by_node["constraint_extract"]["node_fingerprint"], "fingerprint-a")
        self.assertEqual(task_by_node["materials_rag_search"]["reuse_status"], "reused")

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

    def test_resume_endpoint_creates_new_attempt_from_terminal_job(self) -> None:
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
            source = store.create_agent_chat_job(
                AgentChatRequest(
                    conversation_id="conv-resume",
                    message="跑一个会失败的 LAMMPS",
                    notes="original notes",
                )
            )
            source_response = AgentRunResponse(
                success=False,
                run_id="run-resume-source",
                conversation_id="conv-resume",
                route=TaskRoute(name="lammps.generate", compute_domain="lammps", selected_tool="lammps"),
                final_message="global timeout",
                metadata={},
                summary={"partial_report": {"last_checkpoint_id": "after_preflight_dag-1234"}},
                run_status="failed",
            )
            artifact_service.write_run_summary(source_response)
            store.mark_completed(source.job_id, source_response)

            with TestClient(app) as client:
                response = client.post(
                    f"/api/jobs/{source.job_id}/resume",
                    json={"strategy": "checkpoint_context"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            resumed_job_id = payload["resumed_job"]["job_id"]
            resumed_request = store.load_request(resumed_job_id)
            self.assertIsNotNone(resumed_request)
            assert resumed_request is not None
            self.assertEqual(payload["source_job"]["job_id"], source.job_id)
            self.assertEqual(payload["source_run_id"], "run-resume-source")
            self.assertEqual(payload["checkpoint_id"], "after_preflight_dag-1234")
            self.assertEqual(payload["resumed_job"]["job_type"], "agent_resume")
            self.assertEqual(payload["resumed_job"]["attempt"], 2)
            self.assertEqual(payload["resumed_job"]["source_job_id"], source.job_id)
            self.assertEqual(payload["resumed_job"]["source_run_id"], "run-resume-source")
            self.assertEqual(payload["resumed_job"]["source_checkpoint_id"], "after_preflight_dag-1234")
            self.assertEqual(payload["resumed_job"]["resume_mode"], "new_attempt_with_checkpoint_context")
            self.assertEqual(resumed_request.conversation_id, "conv-resume")
            self.assertIn("source_job_id", resumed_request.notes)
            self.assertIn("checkpoint_id=after_preflight_dag-1234", resumed_request.notes)
            self.assertIn("创建新的恢复 attempt", resumed_request.message)
            worker.stop()

    def test_resume_endpoint_rejects_non_terminal_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AgentJobStore(root_dir=root / "jobs")
            worker = AgentJobWorker(store=store, runner=_fake_runner, poll_interval_seconds=0.01)
            dependencies = build_app_dependencies(
                artifact_service=ArtifactService(root_dir=root / "outputs"),
                memory_store=MemoryStore(root_dir=root / "memory"),
                job_store=store,
                job_worker=worker,
            )
            app = create_app(dependencies)
            source = store.create_agent_chat_job(
                AgentChatRequest(conversation_id="conv-resume-running", message="still queued")
            )
            claimed = store.claim_next()
            self.assertIsNotNone(claimed)

            with TestClient(app) as client:
                response = client.post(f"/api/jobs/{source.job_id}/resume", json={})

            self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
