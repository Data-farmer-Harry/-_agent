from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import select
import subprocess
from typing import Any


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class ExternalMcpServerConfig:
    name: str
    enabled: bool
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    tool_prefix: str = ""
    risk: str = "network"
    read_only: bool = True
    timeout_seconds: float = 10.0
    message_framing: str = "newline"

    @property
    def resolved_tool_prefix(self) -> str:
        return self.tool_prefix or f"mcp.{self.name}"


class ExternalMcpProtocolError(RuntimeError):
    pass


class ExternalMcpClient:
    """Small synchronous MCP stdio client for optional external tools.

    It intentionally avoids a hard dependency on the Python MCP SDK. Each list
    or call opens a short-lived stdio subprocess, performs initialize plus the
    requested JSON-RPC operation, then terminates the process. This is not meant
    to be the fastest possible client; it is a safe adapter for optional tools.
    """

    def __init__(self, config: ExternalMcpServerConfig) -> None:
        if config.transport != "stdio":
            raise ValueError(f"Only stdio external MCP transport is currently supported, got {config.transport!r}.")
        if not config.command:
            raise ValueError(f"External MCP server {config.name!r} has no command.")
        self.config = config
        self._next_id = 1

    def list_tools(self) -> list[JSONDict]:
        with self._process() as process:
            self._initialize(process)
            response = self._request(process, "tools/list", {})
            tools = response.get("tools", [])
            return tools if isinstance(tools, list) else []

    def call_tool(self, tool_name: str, arguments: JSONDict) -> JSONDict:
        with self._process() as process:
            self._initialize(process)
            return self._request(process, "tools/call", {"name": tool_name, "arguments": arguments})

    def _process(self) -> "_ManagedMcpProcess":
        return _ManagedMcpProcess(self.config)

    def _initialize(self, process: subprocess.Popen[bytes]) -> None:
        response = self._request(
            process,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "matterpilot-tool-adapter", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        if not isinstance(response, dict):
            raise ExternalMcpProtocolError("MCP initialize response was not an object.")
        self._notify(process, "notifications/initialized", {})

    def _request(self, process: subprocess.Popen[bytes], method: str, params: JSONDict) -> JSONDict:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        )
        while True:
            message = self._read(process)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ExternalMcpProtocolError(json.dumps(message["error"], ensure_ascii=False))
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def _notify(self, process: subprocess.Popen[bytes], method: str, params: JSONDict) -> None:
        self._send(process, {"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, process: subprocess.Popen[bytes], payload: JSONDict) -> None:
        if process.stdin is None:
            raise ExternalMcpProtocolError("MCP process stdin is unavailable.")
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.config.message_framing == "content_length":
            process.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
        else:
            process.stdin.write(raw + b"\n")
        process.stdin.flush()

    def _read(self, process: subprocess.Popen[bytes]) -> JSONDict:
        if process.stdout is None:
            raise ExternalMcpProtocolError("MCP process stdout is unavailable.")
        if self.config.message_framing == "content_length":
            return self._read_content_length_message(process)
        line = self._readline(process).strip()
        if not line:
            raise ExternalMcpProtocolError("MCP process produced an empty response.")
        return json.loads(line.decode("utf-8"))

    def _readline(self, process: subprocess.Popen[bytes]) -> bytes:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], self.config.timeout_seconds)
        if not ready:
            stderr = _safe_stderr(process)
            raise TimeoutError(f"Timed out waiting for MCP server {self.config.name!r}. stderr={stderr}")
        line = process.stdout.readline()
        if not line:
            stderr = _safe_stderr(process)
            raise ExternalMcpProtocolError(f"MCP server {self.config.name!r} closed stdout. stderr={stderr}")
        return line

    def _read_exact(self, process: subprocess.Popen[bytes], length: int) -> bytes:
        assert process.stdout is not None
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            ready, _, _ = select.select([process.stdout], [], [], self.config.timeout_seconds)
            if not ready:
                raise TimeoutError(f"Timed out reading {remaining} bytes from MCP server {self.config.name!r}.")
            chunk = process.stdout.read(remaining)
            if not chunk:
                raise ExternalMcpProtocolError(f"MCP server {self.config.name!r} closed before full message.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_content_length_message(self, process: subprocess.Popen[bytes]) -> JSONDict:
        content_length: int | None = None
        while True:
            line = self._readline(process)
            stripped = line.strip()
            if not stripped:
                break
            key, _, value = stripped.decode("ascii", errors="replace").partition(":")
            if key.lower() == "content-length":
                content_length = int(value.strip())
        if content_length is None:
            raise ExternalMcpProtocolError("MCP content_length response missed Content-Length header.")
        raw = self._read_exact(process, content_length)
        return json.loads(raw.decode("utf-8"))


class _ManagedMcpProcess:
    def __init__(self, config: ExternalMcpServerConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> subprocess.Popen[bytes]:
        env = {**os.environ, **self.config.env}
        cwd = self.config.cwd or None
        self.process = subprocess.Popen(
            [self.config.command, *self.config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        return self.process

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


def _safe_stderr(process: subprocess.Popen[bytes], *, limit: int = 2000) -> str:
    if process.stderr is None:
        return ""
    try:
        ready, _, _ = select.select([process.stderr], [], [], 0)
        if not ready:
            return ""
        return process.stderr.read(limit).decode("utf-8", errors="replace")
    except Exception:
        return ""


def config_from_mapping(payload: JSONDict) -> ExternalMcpServerConfig:
    return ExternalMcpServerConfig(
        name=str(payload.get("name") or "").strip(),
        enabled=bool(payload.get("enabled", False)),
        transport=str(payload.get("transport") or "stdio").strip(),
        command=str(payload.get("command") or "").strip(),
        args=[str(item) for item in payload.get("args", [])] if isinstance(payload.get("args"), list) else [],
        cwd=str(payload.get("cwd") or "").strip(),
        env={str(key): str(value) for key, value in payload.get("env", {}).items()} if isinstance(payload.get("env"), dict) else {},
        tool_prefix=str(payload.get("tool_prefix") or "").strip(),
        risk=str(payload.get("risk") or "network").strip(),
        read_only=bool(payload.get("read_only", True)),
        timeout_seconds=float(payload.get("timeout_seconds", 10.0)),
        message_framing=str(payload.get("message_framing") or "newline").strip(),
    )


def load_external_mcp_configs(config_path: Path | None = None) -> list[ExternalMcpServerConfig]:
    from app.config import CONFIGS_ROOT

    env_path = os.environ.get("PHASE_DIAGRAM_EXTERNAL_MCP_CONFIG", "").strip()
    path = config_path or (Path(env_path).expanduser() if env_path else CONFIGS_ROOT / "external_mcp_tools.json")
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("servers", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    return [config_from_mapping(item) for item in rows if isinstance(item, dict)]
