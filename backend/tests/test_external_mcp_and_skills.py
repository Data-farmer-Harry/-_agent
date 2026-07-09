from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from app.core.artifacts import ArtifactService
from app.skills import SkillRouter, build_default_skill_registry
from app.tools.executor import ToolExecutor
from app.tools.mcp_adapter import register_external_mcp_tools
from app.tools.models import ToolCall
from app.tools.registry import ToolRegistry
from tests.support import build_request


class ExternalMcpAndSkillsTests(unittest.TestCase):
    def test_external_mcp_adapter_registers_and_calls_stdio_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            server_path = root / "fake_mcp_server.py"
            server_path.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys

                    def send(payload):
                        sys.stdout.write(json.dumps(payload) + "\\n")
                        sys.stdout.flush()

                    for line in sys.stdin:
                        request = json.loads(line)
                        method = request.get("method")
                        request_id = request.get("id")
                        if method == "initialize":
                            send({"jsonrpc": "2.0", "id": request_id, "result": {"serverInfo": {"name": "fake"}, "capabilities": {"tools": {}}}})
                        elif method == "notifications/initialized":
                            continue
                        elif method == "tools/list":
                            send({
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": {
                                    "tools": [
                                        {
                                            "name": "echo",
                                            "description": "Echo arguments",
                                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                                        }
                                    ]
                                },
                            })
                        elif method == "tools/call":
                            arguments = request.get("params", {}).get("arguments", {})
                            send({
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": {
                                    "content": [{"type": "text", "text": json.dumps({"echo": arguments}, ensure_ascii=False)}],
                                    "isError": False,
                                },
                            })
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            config_path = root / "external_mcp_tools.json"
            config_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "fake",
                                "enabled": True,
                                "transport": "stdio",
                                "command": sys.executable,
                                "args": [str(server_path)],
                                "tool_prefix": "mcp.fake",
                                "risk": "safe",
                                "read_only": True,
                                "timeout_seconds": 5,
                                "message_framing": "newline",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            registry = ToolRegistry()
            registrations = register_external_mcp_tools(registry, config_path=config_path, strict=True)
            self.assertTrue(any(item.get("internal_name") == "mcp.fake.echo" for item in registrations))
            self.assertTrue(registry.has("mcp.fake.echo"))

            request = build_request("调用外部 echo 工具")
            state = {
                "run_id": "mcpfake",
                "conversation_id": request.conversation_id,
                "request": request,
                "uploaded_assets": [],
                "last_run_context": request.last_run_context,
                "tool_results": [],
                "artifact_messages": [],
                "trace": [],
                "plan_steps": [],
            }
            executor = ToolExecutor(registry)
            result = executor.execute(
                ToolCall(tool_name="mcp.fake.echo", arguments={"text": "hello"}),
                executor.build_context(state, ArtifactService(root_dir=root)),
            )
            self.assertTrue(result.success)
            self.assertEqual(result.output["parsed_content"][0]["echo"]["text"], "hello")

    def test_skill_router_selects_lammps_debugging_skill(self) -> None:
        registry = build_default_skill_registry()
        router = SkillRouter(registry)
        request = build_request("LAMMPS 报错 lost atoms，帮我看看 log.lammps")
        decision = router.decide({"request": request})
        self.assertTrue(decision.has_skills)
        self.assertEqual(decision.selected_skills[0].skill_id, "lammps_debugging")
        context = router.build_context(decision)
        self.assertIn("LAMMPS 调试助手", context)
        self.assertIn("Cause hypothesis", context)


if __name__ == "__main__":
    unittest.main()
