from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.artifacts import ArtifactService
from app.mcp_server import MaterialsMcpServer
from app.state import AgentRunResponse, ArtifactRef, TaskRoute


def _build_run_response(*, run_id: str, route_name: str, compute_domain: str, final_message: str) -> AgentRunResponse:
    return AgentRunResponse(
        success=True,
        run_id=run_id,
        conversation_id="mcp-test",
        route=TaskRoute(
            name=route_name,
            reason="MCP tool invocation",
            selected_tool=route_name,
            intent=route_name,
            decision_source="mcp_server",
            decision_confidence=1.0,
            compute_domain=compute_domain,
        ),
        final_message=final_message,
        html_content=None,
        html_path=None,
        artifacts=[ArtifactRef(kind="json", name="summary.json", path=f"/tmp/{run_id}/summary.json")],
        termination_reason="completed",
        metadata={"source": "mcp_test"},
    )


class _FakeRuntime:
    def __init__(self, response: AgentRunResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.structured_calls: list[dict[str, object]] = []

    def run(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.response

    def run_structured(self, **kwargs):  # noqa: ANN003
        self.structured_calls.append(kwargs)
        return self.response


class McpServerTests(unittest.TestCase):
    def _build_server(self) -> tuple[MaterialsMcpServer, _FakeRuntime, _FakeRuntime]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            phase_runtime = _FakeRuntime(
                _build_run_response(
                    run_id="phase-run-1",
                    route_name="phase_diagram.generate",
                    compute_domain="phase_diagram",
                    final_message="phase diagram completed",
                )
            )
            lammps_runtime = _FakeRuntime(
                _build_run_response(
                    run_id="lammps-run-1",
                    route_name="lammps.generate",
                    compute_domain="lammps",
                    final_message="lammps completed",
                )
            )
            deps = SimpleNamespace(
                artifact_service=artifact_service,
                phase_diagram_runtime=phase_runtime,
                lammps_runtime=lammps_runtime,
            )
            return MaterialsMcpServer(dependencies=deps), phase_runtime, lammps_runtime

    def test_initialize_returns_protocol_metadata(self) -> None:
        server, _, _ = self._build_server()
        response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIsNotNone(response)
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["name"], "materials-agent-mcp")

    def test_tools_list_exposes_phase_diagram_and_lammps_tools(self) -> None:
        server, _, _ = self._build_server()
        response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertIsNotNone(response)
        tools = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("phase_diagram.run", tools)
        self.assertIn("phase_diagram.run_structured", tools)
        self.assertIn("phase_diagram.registry_search", tools)
        self.assertIn("phase_diagram.rag_search", tools)
        self.assertIn("lammps.run", tools)
        self.assertIn("lammps.run_structured", tools)
        self.assertIn("lammps.registry_get", tools)
        self.assertIn("system.diagnostics", tools)

    def test_phase_diagram_run_calls_runtime_and_preserves_overrides(self) -> None:
        server, phase_runtime, _ = self._build_server()
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "phase_diagram.run",
                    "arguments": {
                        "message": "请生成一张 Al-Zn 二元相图。",
                        "conversation_id": "mcp-conv",
                        "system_name": "Al-Zn",
                        "temperature_min": 300.0,
                        "temperature_max": 1000.0,
                    },
                },
            }
        )
        self.assertIsNotNone(response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["run_id"], "phase-run-1")
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(len(phase_runtime.calls), 1)
        request = phase_runtime.calls[0]["request"]
        self.assertEqual(request.conversation_id, "mcp-conv")
        self.assertEqual(request.system_name, "Al-Zn")
        self.assertEqual(request.temperature_min, 300.0)
        self.assertEqual(request.temperature_max, 1000.0)

    def test_lammps_run_accepts_json_string_arguments(self) -> None:
        server, _, lammps_runtime = self._build_server()
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "lammps.run",
                    "arguments": json.dumps(
                        {
                            "message": "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps。",
                            "conversation_id": "mcp-lammps",
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        )
        self.assertIsNotNone(response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["run_id"], "lammps-run-1")
        self.assertEqual(len(lammps_runtime.calls), 1)
        request = lammps_runtime.calls[0]["request"]
        self.assertEqual(request.conversation_id, "mcp-lammps")
        self.assertIn("LAMMPS", request.message)

    def test_phase_diagram_run_structured_calls_runtime_directly(self) -> None:
        server, phase_runtime, _ = self._build_server()
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "phase_diagram.run_structured",
                    "arguments": {
                        "conversation_id": "mcp-phase-structured",
                        "message": "structured call",
                        "request": {
                            "system_name": "Al-Zn",
                            "diagram_type": "binary",
                            "temperature_min": 300.0,
                            "temperature_max": 1000.0,
                            "pressure": 101325.0,
                            "step_size": 25.0,
                            "notes": "focus on liquidus",
                        },
                    },
                },
            }
        )
        self.assertIsNotNone(response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["run_id"], "phase-run-1")
        self.assertEqual(len(phase_runtime.structured_calls), 1)
        call = phase_runtime.structured_calls[0]
        self.assertEqual(call["conversation_id"], "mcp-phase-structured")
        self.assertEqual(call["diagram_request"]["system_name"], "Al-Zn")
        self.assertEqual(call["diagram_request"]["step_size"], 25.0)

    def test_lammps_run_structured_calls_runtime_directly(self) -> None:
        server, _, lammps_runtime = self._build_server()
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "lammps.run_structured",
                    "arguments": {
                        "conversation_id": "mcp-lammps-structured",
                        "message": "structured call",
                        "request": {
                            "material": "Cu",
                            "potential_family": "eam",
                            "task_type": "heating",
                            "temperature": 800,
                            "steps": 4000,
                            "ensemble": "NVT",
                            "box_size": 4,
                            "time_step": 0.001,
                            "dump_file": "dump.atom",
                            "notes": "structured path",
                        },
                    },
                },
            }
        )
        self.assertIsNotNone(response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["run_id"], "lammps-run-1")
        self.assertEqual(len(lammps_runtime.structured_calls), 1)
        call = lammps_runtime.structured_calls[0]
        self.assertEqual(call["conversation_id"], "mcp-lammps-structured")
        self.assertEqual(call["structured_request"]["material"], "Cu")

    def test_registry_and_rag_tools_return_payloads(self) -> None:
        server, _, _ = self._build_server()

        registry_response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "phase_diagram.registry_search", "arguments": {"query": "Al-Zn"}},
            }
        )
        rag_response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "phase_diagram.rag_search", "arguments": {"query": "铝锌二元相图", "top_k": 3}},
            }
        )

        registry_payload = json.loads(registry_response["result"]["content"][0]["text"])
        rag_payload = json.loads(rag_response["result"]["content"][0]["text"])
        self.assertTrue(registry_payload["matched"])
        self.assertEqual(registry_payload["card"]["system_name"], "Al-Zn")
        self.assertTrue(rag_payload["matched"])
        self.assertGreaterEqual(len(rag_payload["candidates"]), 1)
        self.assertEqual(rag_payload["candidates"][0]["system_name"], "Al-Zn")

    def test_stdio_round_trip_returns_framed_response(self) -> None:
        server, _, _ = self._build_server()
        request_payload = {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}
        body = json.dumps(request_payload).encode("utf-8")
        framed = b"".join([f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"), body])
        input_stream = io.BytesIO(framed)
        output_stream = io.BytesIO()

        server.serve_stdio(input_stream=input_stream, output_stream=output_stream)

        output_stream.seek(0)
        raw = output_stream.read().decode("utf-8")
        self.assertIn("Content-Length:", raw)
        _, _, json_body = raw.partition("\r\n\r\n")
        payload = json.loads(json_body)
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["result"], {})

    def test_system_diagnostics_tool_returns_checks(self) -> None:
        server, _, _ = self._build_server()
        response = server.handle_request(
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "system.diagnostics", "arguments": {}}}
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn(payload["overall_status"], {"ok", "warning", "error"})
        self.assertGreaterEqual(len(payload["checks"]), 5)


if __name__ == "__main__":
    unittest.main()
