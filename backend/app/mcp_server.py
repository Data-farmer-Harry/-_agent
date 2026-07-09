from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from app.diagnostics import build_system_diagnostics
from app.state import AgentChatRequest
from app.thermo.rag_service import ThermoRagService
from app.thermo.registry import load_thermo_database_cards, retrieve_thermo_database
from app.tools import ToolExecutor, build_default_tool_registry
from app.tools.models import ToolCall

if TYPE_CHECKING:
    from app.api import AppDependencies


JSONDict = dict[str, Any]


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _tool_text_result(payload: Any, *, is_error: bool = False) -> JSONDict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            }
        ],
        "isError": is_error,
    }


@dataclass
class McpTool:
    name: str
    description: str
    input_schema: JSONDict
    handler: Callable[[JSONDict], JSONDict]


class MaterialsMcpFacade:
    def __init__(self, dependencies: AppDependencies | None = None) -> None:
        if dependencies is None:
            from app.api import build_app_dependencies

            dependencies = build_app_dependencies()
        self.deps = dependencies
        self.tool_registry = build_default_tool_registry()
        self.tool_executor = ToolExecutor(self.tool_registry)

    def phase_diagram_run(self, arguments: JSONDict) -> JSONDict:
        message = str(arguments.get("message") or "").strip()
        if not message:
            raise ValueError("phase_diagram.run requires a non-empty `message`.")
        request = AgentChatRequest(
            conversation_id=str(arguments.get("conversation_id") or "mcp-phase-diagram"),
            message=message,
            system_name=str(arguments.get("system_name") or ""),
            diagram_type=str(arguments.get("diagram_type") or "binary"),
            temperature_min=float(arguments.get("temperature_min", 300.0)),
            temperature_max=float(arguments.get("temperature_max", 1800.0)),
            pressure=float(arguments.get("pressure", 101325.0)),
            step_size=float(arguments.get("step_size", 50.0)),
            notes=str(arguments.get("notes") or "Triggered via MCP phase_diagram.run"),
            uploaded_assets=[],
            conversation_history=[],
        )
        response = self.deps.phase_diagram_runtime.run(
            run_id=self.deps.artifact_service.create_run_id(),
            request=request,
            decision={
                "route_name": "phase_diagram.generate",
                "reason": "Triggered through MCP phase_diagram.run tool.",
                "intent": "generate_phase_diagram",
                "source": "mcp_server",
                "confidence": 1.0,
            },
        )
        return response.model_dump(mode="json")

    def phase_diagram_run_structured(self, arguments: JSONDict) -> JSONDict:
        payload = arguments.get("request") if isinstance(arguments.get("request"), dict) else arguments
        if not isinstance(payload, dict):
            raise ValueError("phase_diagram.run_structured requires a `request` object or direct structured fields.")
        response = self.deps.phase_diagram_runtime.run_structured(
            run_id=self.deps.artifact_service.create_run_id(),
            diagram_request=payload,
            conversation_id=str(arguments.get("conversation_id") or "mcp-phase-diagram-structured"),
            request_message=str(arguments.get("message") or ""),
            decision={
                "route_name": "phase_diagram.generate",
                "reason": "Triggered through MCP phase_diagram.run_structured tool.",
                "intent": "generate_phase_diagram",
                "source": "mcp_server_structured",
                "confidence": 1.0,
            },
        )
        return response.model_dump(mode="json")

    def lammps_run(self, arguments: JSONDict) -> JSONDict:
        message = str(arguments.get("message") or "").strip()
        if not message:
            raise ValueError("lammps.run requires a non-empty `message`.")
        request = AgentChatRequest(
            conversation_id=str(arguments.get("conversation_id") or "mcp-lammps"),
            message=message,
            notes=str(arguments.get("notes") or "Triggered via MCP lammps.run"),
            uploaded_assets=[],
            conversation_history=[],
        )
        response = self.deps.lammps_runtime.run(
            run_id=self.deps.artifact_service.create_run_id(),
            request=request,
            decision={
                "route_name": "lammps.generate",
                "reason": "Triggered through MCP lammps.run tool.",
                "intent": "run_lammps_simulation",
                "source": "mcp_server",
                "confidence": 1.0,
            },
        )
        return response.model_dump(mode="json")

    def lammps_run_structured(self, arguments: JSONDict) -> JSONDict:
        payload = arguments.get("request") if isinstance(arguments.get("request"), dict) else arguments
        if not isinstance(payload, dict):
            raise ValueError("lammps.run_structured requires a `request` object or direct structured fields.")
        response = self.deps.lammps_runtime.run_structured(
            run_id=self.deps.artifact_service.create_run_id(),
            structured_request=payload,
            conversation_id=str(arguments.get("conversation_id") or "mcp-lammps-structured"),
            original_query=str(arguments.get("message") or ""),
            decision={
                "route_name": "lammps.generate",
                "reason": "Triggered through MCP lammps.run_structured tool.",
                "intent": "run_lammps_simulation",
                "source": "mcp_server_structured",
                "confidence": 1.0,
            },
        )
        return response.model_dump(mode="json")

    def phase_diagram_registry_search(self, arguments: JSONDict) -> JSONDict:
        query = str(arguments.get("query") or "").strip()
        cards = load_thermo_database_cards()
        if not query:
            return {
                "matched": False,
                "count": len(cards),
                "systems": [card.public_payload() for card in cards],
            }
        card, retrieval = retrieve_thermo_database(query)
        return {
            "matched": card is not None,
            "query": query,
            "selection_strategy": retrieval.get("selection_strategy", "exact"),
            "card": card.public_payload() if card else None,
        }

    def phase_diagram_rag_search(self, arguments: JSONDict) -> JSONDict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("phase_diagram.rag_search requires a non-empty `query`.")
        top_k = int(arguments.get("top_k", 5))
        return ThermoRagService.search(query, top_k=top_k)

    def lammps_registry_get(self, arguments: JSONDict) -> JSONDict:
        _ = arguments
        from app.lammps import get_lammps_registry_payload

        return get_lammps_registry_payload()

    def system_diagnostics(self, arguments: JSONDict) -> JSONDict:
        _ = arguments
        return build_system_diagnostics().model_dump(mode="json")

    def generic_tool_call(self, tool_name: str, arguments: JSONDict) -> JSONDict:
        message = str(arguments.get("message") or arguments.get("query") or f"MCP call {tool_name}")
        request = AgentChatRequest(
            conversation_id=str(arguments.get("conversation_id") or "mcp-tools"),
            message=message,
            uploaded_assets=[],
            conversation_history=[],
        )
        run_id = str(arguments.get("run_id") or self.deps.artifact_service.create_run_id())
        state = {
            "run_id": run_id,
            "conversation_id": request.conversation_id,
            "request": request,
            "uploaded_assets": [],
            "last_run_context": request.last_run_context,
            "tool_results": [],
            "artifact_messages": [],
            "trace": [],
            "plan_steps": [],
        }
        context = self.tool_executor.build_context(state, self.deps.artifact_service)
        payload = {key: value for key, value in arguments.items() if key not in {"message", "conversation_id", "run_id"}}
        result = self.tool_executor.execute(ToolCall(tool_name=tool_name, arguments=payload, reason="mcp_tool_call"), context)
        return result.model_dump()


class MaterialsMcpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, dependencies: AppDependencies | None = None) -> None:
        self.facade = MaterialsMcpFacade(dependencies)
        self.tools = self._build_tools()

    def _build_tools(self) -> dict[str, McpTool]:
        tools = {
            "phase_diagram.run": McpTool(
                name="phase_diagram.run",
                description="Run the existing phase diagram runtime using the provided natural-language request and optional structured overrides.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "conversation_id": {"type": "string"},
                        "system_name": {"type": "string"},
                        "diagram_type": {"type": "string", "enum": ["binary", "ternary"]},
                        "temperature_min": {"type": "number"},
                        "temperature_max": {"type": "number"},
                        "pressure": {"type": "number"},
                        "step_size": {"type": "number"},
                        "notes": {"type": "string"},
                    },
                    "required": ["message"],
                },
                handler=self.facade.phase_diagram_run,
            ),
            "phase_diagram.run_structured": McpTool(
                name="phase_diagram.run_structured",
                description="Run the phase diagram runtime directly from a structured DiagramRequest without natural-language parsing.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string"},
                        "message": {"type": "string"},
                        "request": {
                            "type": "object",
                            "properties": {
                                "system_name": {"type": "string"},
                                "diagram_type": {"type": "string", "enum": ["binary", "ternary"]},
                                "temperature_min": {"type": "number"},
                                "temperature_max": {"type": "number"},
                                "pressure": {"type": "number"},
                                "step_size": {"type": "number"},
                                "notes": {"type": "string"},
                            },
                            "required": ["system_name"],
                        },
                    },
                    "required": ["request"],
                },
                handler=self.facade.phase_diagram_run_structured,
            ),
            "phase_diagram.registry_search": McpTool(
                name="phase_diagram.registry_search",
                description="Search the deterministic thermo registry by system name or alias.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                },
                handler=self.facade.phase_diagram_registry_search,
            ),
            "phase_diagram.rag_search": McpTool(
                name="phase_diagram.rag_search",
                description="Search the thermo RAG enhancement layer for candidate databases and system cards.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
                handler=self.facade.phase_diagram_rag_search,
            ),
            "lammps.run": McpTool(
                name="lammps.run",
                description="Run the existing LAMMPS runtime using the provided natural-language MD request.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "conversation_id": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["message"],
                },
                handler=self.facade.lammps_run,
            ),
            "lammps.run_structured": McpTool(
                name="lammps.run_structured",
                description="Run the LAMMPS runtime directly from a structured LammpsRequest without natural-language parsing.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string"},
                        "message": {"type": "string"},
                        "request": {
                            "type": "object",
                            "properties": {
                                "material": {"type": "string"},
                                "potential_family": {"type": "string"},
                                "task_type": {"type": "string"},
                                "temperature": {"type": "integer"},
                                "steps": {"type": "integer"},
                                "ensemble": {"type": "string"},
                                "box_size": {"type": "integer"},
                                "initial_temp": {"type": "integer"},
                                "time_step": {"type": "number"},
                                "dump_file": {"type": "string"},
                                "custom_potential_path": {"type": "string"},
                                "custom_structure_path": {"type": "string"},
                                "custom_structure_format": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": ["material", "task_type"],
                        },
                    },
                    "required": ["request"],
                },
                handler=self.facade.lammps_run_structured,
            ),
            "lammps.registry_get": McpTool(
                name="lammps.registry_get",
                description="Return the supported materials, potentials, and tasks from the LAMMPS registry.",
                input_schema={"type": "object", "properties": {}},
                handler=self.facade.lammps_registry_get,
            ),
            "system.diagnostics": McpTool(
                name="system.diagnostics",
                description="Return the current system diagnostics payload used by the backend settings panel.",
                input_schema={"type": "object", "properties": {}},
                handler=self.facade.system_diagnostics,
            ),
        }
        for spec in self.facade.tool_registry.list_specs():
            tools[spec.name] = McpTool(
                name=spec.name,
                description=f"[Generic tool] {spec.description}",
                input_schema=spec.input_schema,
                handler=lambda arguments, tool_name=spec.name: self.facade.generic_tool_call(tool_name, arguments),
            )
        return tools

    def handle_request(self, request: JSONDict) -> JSONDict | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "serverInfo": {"name": "materials-agent-mcp", "version": "0.1.0"},
                    "capabilities": {"tools": {}, "resources": {}},
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.input_schema,
                        }
                        for tool in self.tools.values()
                    ]
                },
            }

        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}

        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = self._coerce_arguments(params.get("arguments") or {})
            tool = self.tools.get(name)
            if tool is None:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"Unknown MCP tool: {name}"},
                }
            try:
                payload = tool.handler(arguments)
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_text_result(payload, is_error=False)}
            except Exception as exc:  # noqa: BLE001
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_text_result({"error": str(exc)}, is_error=True)}

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown MCP method: {method}"},
        }

    @staticmethod
    def _read_message(stream) -> JSONDict | None:
        headers: dict[str, str] = {}
        while True:
            line = stream.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("utf-8").partition(":")
            headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        payload = stream.read(length)
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    @staticmethod
    def _coerce_arguments(arguments: Any) -> JSONDict:
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            parsed = json.loads(arguments)
            if not isinstance(parsed, dict):
                raise ValueError("MCP tool arguments must decode to a JSON object.")
            return parsed
        raise ValueError("MCP tool arguments must be an object or JSON object string.")

    @staticmethod
    def _write_message(stream, payload: JSONDict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        stream.write(header)
        stream.write(body)
        stream.flush()

    def serve_stdio(self, input_stream=None, output_stream=None) -> None:
        instream = input_stream or sys.stdin.buffer
        outstream = output_stream or sys.stdout.buffer
        while True:
            request = self._read_message(instream)
            if request is None:
                break
            response = self.handle_request(request)
            if response is not None:
                self._write_message(outstream, response)


def main() -> None:
    MaterialsMcpServer().serve_stdio()


if __name__ == "__main__":
    main()
