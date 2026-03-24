from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app, agent_chat, agent_chat_stream
from app.schemas import AgentChatRequest, AgentStreamEvent
from tests.test_backend_contracts import make_agent_response


def sample_chat_request() -> AgentChatRequest:
    return AgentChatRequest(
        message="我想生成一个 Fe-Cu 的二维相图，温度范围 300K-2700K。",
        workspace_hint="phase_diagram",
        system_name="Fe-Cu",
        chart_title="Fe-Cu agent chat",
        diagram_type="binary",
        temperature_min=300.0,
        temperature_max=2700.0,
        pressure=101325.0,
        step_size=50.0,
        notes="http route smoke test",
        filename="",
    )


async def read_stream(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


class HttpRouteRegistrationTests(unittest.TestCase):
    def test_agent_http_paths_are_registered(self) -> None:
        registered_paths = {route.path for route in app.routes}

        self.assertIn("/api/agent/catalog", registered_paths)
        self.assertIn("/api/agent/manifest", registered_paths)
        self.assertIn("/api/agent/chat", registered_paths)
        self.assertIn("/api/agent/chat/stream", registered_paths)

    def test_agent_chat_endpoint_returns_serialized_response(self) -> None:
        fake_response = make_agent_response()

        with patch("app.main.agent_runtime.run", return_value=fake_response) as run_mock:
            response = agent_chat(sample_chat_request())

        payload = response.model_dump()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["route"]["name"], "phase_diagram.generate")
        self.assertEqual(payload["route"]["selected_tool"], "phase_diagram_codegen")
        run_mock.assert_called_once()

    def test_agent_chat_stream_endpoint_emits_events(self) -> None:
        fake_response = make_agent_response()

        def fake_run(request, event_sink=None):
            if event_sink is not None:
                event_sink(
                    AgentStreamEvent(
                        type="run_started",
                        run_id=fake_response.run_id,
                        payload={
                            "route": fake_response.route.model_dump(mode="json"),
                            "plan_steps": [step.model_dump(mode="json") for step in fake_response.plan_steps],
                        },
                    )
                )
                event_sink(
                    AgentStreamEvent(
                        type="run_completed",
                        run_id=fake_response.run_id,
                        payload={
                            "response": fake_response.model_dump(mode="json"),
                            "html_ready": True,
                        },
                    )
                )
            return fake_response

        with patch("app.main.agent_runtime.run", side_effect=fake_run) as run_mock:
            response = agent_chat_stream(sample_chat_request())
            body = asyncio.run(read_stream(response))

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertIn("event: run_started", body)
        self.assertIn("event: run_completed", body)
        data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
        self.assertGreaterEqual(len(data_lines), 2)
        completed_payload = json.loads(data_lines[-1][6:])
        self.assertEqual(completed_payload["run_id"], fake_response.run_id)
        self.assertTrue(completed_payload["payload"]["html_ready"])
        run_mock.assert_called_once()
