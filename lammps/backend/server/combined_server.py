from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config.supervisor_config import (
    load_supervisor_config,
    supervisor_config_public_payload,
    update_runtime_supervisor_config,
)
from src.config.llm_config import llm_config_public_payload, update_runtime_llm_config
from src.graphs.agent_workflow import AgentWorkflow
from src.reasoning.request_validator import validate_request
from src.schemas.state import AgentState
from src.tools.generate_lammps_in import get_lammps_form_schema
from src.utils.constants import DEFAULT_HOST, DEFAULT_PORT, HTML_DIR, OUTPUTS_DIR
from src.utils.path_utils import create_run_dir, read_json, write_json
from src.utils.cancellation import cancel_run

WORKFLOW = AgentWorkflow()
CONFIG = load_supervisor_config()
RUNS: Dict[str, AgentState] = {}
RUN_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _json_response(handler: "CombinedHandler", payload: Dict[str, object], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_request(handler: "CombinedHandler") -> Dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if not length:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw else {}


def _artifact_urls(run_id: str, summary: Dict[str, object]) -> Dict[str, str]:
    artifacts = summary.get("artifacts", {})
    return {name: f"/artifacts/{run_id}/{name}" for name in artifacts}


def _merge_artifacts_from_disk(run_id: str, summary: Dict[str, object]) -> Dict[str, object]:
    output_dir = OUTPUTS_DIR / run_id
    merged = dict(summary)
    artifacts = dict(merged.get("artifacts", {}))
    if output_dir.exists():
        for file_path in output_dir.iterdir():
            if file_path.is_file() and not file_path.name.startswith("."):
                artifacts.setdefault(file_path.name, str(file_path))
    merged["artifacts"] = artifacts
    return merged


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    if not range_header.startswith("bytes="):
        return None
    value = range_header[len("bytes="):].strip()
    if "," in value:
        return None
    start_str, _, end_str = value.partition("-")
    try:
        if start_str == "":
            suffix_length = int(end_str)
            if suffix_length <= 0:
                return None
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= file_size:
        return None
    return start, min(end, file_size - 1)


def _load_run_from_disk(run_id: str) -> AgentState | None:
    summary_path = OUTPUTS_DIR / run_id / "summary.json"
    request_path = OUTPUTS_DIR / run_id / "request.json"
    if not summary_path.exists():
        return None
    summary = _merge_artifacts_from_disk(run_id, read_json(summary_path))
    request = read_json(request_path) if request_path.exists() else {}
    request_payload = summary.get("request", {})
    if isinstance(request_payload, dict) and "normalized_request" in request_payload:
        normalized_request = request_payload.get("normalized_request", {}) or request
        user_query = str(request_payload.get("original_query") or _request_to_prompt(normalized_request))
    else:
        normalized_request = request or request_payload or {}
        user_query = _request_to_prompt(normalized_request)
    state = AgentState(
        user_query=user_query,
        normalized_request=normalized_request,
        run_id=run_id,
        artifacts=summary.get("artifacts", {}),
        error=str(summary.get("error", "")),
        mode=str(summary.get("mode", "mock")),
        status=str(summary.get("status", "completed")),
        summary=summary,
    )
    return state


def _queue_summary(
    run_id: str,
    request: Dict[str, object],
    output_dir: Path,
) -> None:
    summary = {
        "status": "queued",
        "mode": "real",
        "error": "",
        "request": {
            "original_query": "",
            "normalized_request": request,
        },
        "progress": {
            "stage": "queued",
            "percent": 0,
            "message": "任务已入队，等待开始执行。",
        },
        "artifacts": {},
    }
    write_json(output_dir / "summary.json", summary)


def _request_to_prompt(request: Dict[str, object]) -> str:
    material = request.get("material") or "材料"
    task_type = request.get("task_type") or "simulation"
    temperature = request.get("temperature")
    steps = request.get("steps")
    potential = request.get("potential_family") or "potential"
    if temperature and steps:
        return f"Run a {potential} {task_type} for {material} at {temperature}K for {steps} steps."
    return json.dumps(request, ensure_ascii=False)


def _latest_run_id() -> str | None:
    candidates = []
    if not OUTPUTS_DIR.exists():
        return None
    for path in OUTPUTS_DIR.iterdir():
        summary = path / "summary.json"
        if path.is_dir() and summary.exists():
            candidates.append((summary.stat().st_mtime, path.name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _list_runs(limit: int = 20) -> list[Dict[str, object]]:
    items: list[tuple[float, Dict[str, object]]] = []
    if not OUTPUTS_DIR.exists():
        return []
    for path in OUTPUTS_DIR.iterdir():
        summary_path = path / "summary.json"
        if not path.is_dir() or not summary_path.exists():
            continue
        try:
            summary = read_json(summary_path)
        except Exception:
            continue
        request_payload = summary.get("request", {})
        if isinstance(request_payload, dict) and "normalized_request" in request_payload:
            normalized_request = request_payload.get("normalized_request", {}) or {}
            original_query = str(request_payload.get("original_query") or _request_to_prompt(normalized_request))
        else:
            normalized_request = request_payload if isinstance(request_payload, dict) else {}
            original_query = _request_to_prompt(normalized_request)
        items.append(
            (
                summary_path.stat().st_mtime,
                {
                    "run_id": path.name,
                    "status": summary.get("status", "completed"),
                    "mode": summary.get("mode", "real"),
                    "updated_at": summary_path.stat().st_mtime,
                    "original_query": original_query,
                    "normalized_request": normalized_request,
                },
            )
        )
    items.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in items[:limit]]


def chat_response(payload: Dict[str, object]) -> Tuple[Dict[str, object], int]:
    query = str(payload.get("message", "")).strip()
    normalized = payload.get("normalized_request", {}) or {}
    if not query:
        return {"error": "message is required"}, 400
    state = WORKFLOW.handle_chat(query, normalized_request=normalized)
    reply = state.messages[-1]["content"] if state.messages else ""
    return {
        "reply": reply,
        "needs_input": bool(state.missing_fields),
        "can_run": not state.missing_fields and state.validation.get("is_reasonable", False),
        "state": state.to_dict(),
    }, 200


def run_response(payload: Dict[str, object]) -> Tuple[Dict[str, object], int]:
    query = str(payload.get("user_query", "")).strip()
    normalized = payload.get("normalized_request", {}) or {}
    if not normalized:
        return {"error": "normalized_request is required"}, 400
    validation = validate_request(normalized)
    if not validation["is_reasonable"]:
        return {
            "error": "normalized_request is not reasonable for execution",
            "validation": validation,
        }, 400
    run_dir = create_run_dir()
    run_id = run_dir.name
    state = AgentState(
        user_query=query or "Direct run request",
        normalized_request=normalized,
        run_id=run_id,
        mode="real",
        status="queued",
        validation=validation,
    )
    _queue_summary(run_id, normalized, run_dir)
    queued_summary = read_json(run_dir / "summary.json")
    queued_summary["request"]["original_query"] = state.user_query
    write_json(run_dir / "summary.json", queued_summary)
    with RUN_LOCK:
        RUNS[run_id] = state
    EXECUTOR.submit(_execute_run, state, run_dir)
    return {
        "run_id": run_id,
        "status": "queued",
        "state": state.to_dict(),
    }, HTTPStatus.ACCEPTED


def get_run_response(run_id: str) -> Tuple[Dict[str, object], int]:
    with RUN_LOCK:
        state = RUNS.get(run_id)
    if state is None:
        state = _load_run_from_disk(run_id)
    if state is None:
        return {"error": "run not found"}, 404
    summary = _merge_artifacts_from_disk(run_id, state.summary or read_json(OUTPUTS_DIR / run_id / "summary.json"))
    return {
        "run_id": run_id,
        "status": summary.get("status", state.status),
        "mode": summary.get("mode", state.mode),
        "error": summary.get("error", state.error),
        "summary": summary,
        "artifacts": _artifact_urls(run_id, summary),
    }, 200


def list_artifacts_response(run_id: str) -> Tuple[Dict[str, object], int]:
    summary_path = OUTPUTS_DIR / run_id / "summary.json"
    if not summary_path.exists():
        return {"error": "run not found"}, 404
    summary = _merge_artifacts_from_disk(run_id, read_json(summary_path))
    return {
        "run_id": run_id,
        "artifacts": _artifact_urls(run_id, summary),
    }, 200


def latest_run_response() -> Tuple[Dict[str, object], int]:
    run_id = _latest_run_id()
    if not run_id:
        return {"error": "no runs found"}, 404
    body, status = get_run_response(run_id)
    body["latest"] = True
    return body, status


def list_runs_response() -> Tuple[Dict[str, object], int]:
    return {"runs": _list_runs()}, 200


def get_llm_config_response() -> Tuple[Dict[str, object], int]:
    return llm_config_public_payload(), 200


def update_llm_config_response(payload: Dict[str, object]) -> Tuple[Dict[str, object], int]:
    config = update_runtime_llm_config(payload)
    public = llm_config_public_payload()
    public["updated"] = True
    public["provider"] = config.provider
    return public, 200


def get_lammps_config_response() -> Tuple[Dict[str, object], int]:
    return supervisor_config_public_payload(), 200


def get_lammps_template_schema_response() -> Tuple[Dict[str, object], int]:
    return {"schema": get_lammps_form_schema()}, 200


def update_lammps_config_response(payload: Dict[str, object]) -> Tuple[Dict[str, object], int]:
    config = update_runtime_supervisor_config(payload)
    public = supervisor_config_public_payload()
    public["updated"] = True
    public["lammps_command"] = config.lammps_command
    public["potentials_dir"] = config.potentials_dir
    return public, 200


def _execute_run(state: AgentState, output_dir: Path) -> None:
    with RUN_LOCK:
        RUNS[state.run_id or ""] = state
    output_dir.mkdir(parents=True, exist_ok=True)
    current_summary = read_json(output_dir / "summary.json")
    current_summary["status"] = "running"
    current_summary["mode"] = state.mode
    current_summary["request"] = {
        "original_query": state.user_query,
        "normalized_request": state.normalized_request,
    }
    current_summary["progress"] = {
        "stage": "starting",
        "percent": 5,
        "message": "任务已开始，正在初始化执行环境。",
    }
    write_json(output_dir / "summary.json", current_summary)
    try:
        result = WORKFLOW.run(state, output_dir)
    except Exception as exc:  # pragma: no cover - defensive path
        result = state
        result.status = "failed"
        result.error = str(exc)
        result.summary = {
            "status": "failed",
            "mode": result.mode,
            "error": result.error,
            "request": {
                "original_query": result.user_query,
                "normalized_request": result.normalized_request,
            },
            "artifacts": {},
        }
        write_json(output_dir / "summary.json", result.summary)

    with RUN_LOCK:
        RUNS[result.run_id or ""] = result


class CombinedHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs) -> None:
        super().__init__(*args, directory=directory or str(HTML_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
            return
        if parsed.path == "/api/run":
            self._handle_run()
            return
        if parsed.path.startswith("/api/run/") and parsed.path.endswith("/cancel"):
            self._handle_cancel_run(parsed.path.split("/")[3])
            return
        if parsed.path == "/api/config/llm":
            self._handle_update_llm_config()
            return
        if parsed.path == "/api/config/lammps":
            self._handle_update_lammps_config()
            return
        _json_response(self, {"error": "Not found"}, status=404)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.path = "/poros_chat.html"
            return super().do_GET()
        if parsed.path == "/api/runs":
            self._handle_list_runs()
            return
        if parsed.path == "/api/run/latest":
            self._handle_get_latest_run()
            return
        if parsed.path.startswith("/api/run/"):
            self._handle_get_run(parsed.path.rsplit("/", 1)[-1])
            return
        if parsed.path.startswith("/api/artifacts/"):
            self._handle_list_artifacts(parsed.path.rsplit("/", 1)[-1])
            return
        if parsed.path == "/api/config/llm":
            self._handle_get_llm_config()
            return
        if parsed.path == "/api/config/lammps":
            self._handle_get_lammps_config()
            return
        if parsed.path == "/api/template/lammps":
            self._handle_get_lammps_template_schema()
            return
        if parsed.path.startswith("/artifacts/"):
            self._serve_artifact(parsed.path)
            return
        return super().do_GET()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _handle_chat(self) -> None:
        payload = _read_json_request(self)
        body, status = chat_response(payload)
        _json_response(self, body, status=status)

    def _handle_run(self) -> None:
        payload = _read_json_request(self)
        body, status = run_response(payload)
        _json_response(self, body, status=status)

    def _handle_cancel_run(self, run_id: str) -> None:
        cancel_run(run_id)
        _json_response(self, {"message": f"Cancel signal sent for run {run_id}"})

    def _handle_get_run(self, run_id: str) -> None:
        body, status = get_run_response(run_id)
        _json_response(self, body, status=status)

    def _handle_get_latest_run(self) -> None:
        body, status = latest_run_response()
        _json_response(self, body, status=status)

    def _handle_list_runs(self) -> None:
        body, status = list_runs_response()
        _json_response(self, body, status=status)

    def _handle_list_artifacts(self, run_id: str) -> None:
        body, status = list_artifacts_response(run_id)
        _json_response(self, body, status=status)

    def _handle_get_llm_config(self) -> None:
        body, status = get_llm_config_response()
        _json_response(self, body, status=status)

    def _handle_update_llm_config(self) -> None:
        payload = _read_json_request(self)
        body, status = update_llm_config_response(payload)
        _json_response(self, body, status=status)

    def _handle_get_lammps_config(self) -> None:
        body, status = get_lammps_config_response()
        _json_response(self, body, status=status)

    def _handle_get_lammps_template_schema(self) -> None:
        body, status = get_lammps_template_schema_response()
        _json_response(self, body, status=status)

    def _handle_update_lammps_config(self) -> None:
        payload = _read_json_request(self)
        body, status = update_lammps_config_response(payload)
        _json_response(self, body, status=status)

    def _serve_artifact(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3:
            _json_response(self, {"error": "invalid artifact path"}, status=400)
            return
        _, run_id, *rest = parts
        artifact_name = "/".join(rest)
        file_path = OUTPUTS_DIR / run_id / artifact_name
        if not file_path.exists():
            _json_response(self, {"error": "artifact not found"}, status=404)
            return
        content_type = self.guess_type(str(file_path))
        file_size = file_path.stat().st_size
        range_header = self.headers.get("Range", "")
        byte_range = _parse_range_header(range_header, file_size) if range_header else None

        if range_header and byte_range is None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return

        if byte_range is None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with file_path.open("rb") as handle:
                self.wfile.write(handle.read())
            return

        start, end = byte_range
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with file_path.open("rb") as handle:
            handle.seek(start)
            self.wfile.write(handle.read(length))


def build_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    ensure_dir(OUTPUTS_DIR)
    ensure_dir(UPLOADS_DIR)
    handler = partial(CombinedHandler, directory=str(HTML_DIR))
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MD Agent combined server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    server = build_server(args.host, args.port)
    print(f"MD Agent demo server running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
