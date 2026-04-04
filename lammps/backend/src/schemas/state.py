from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


Mode = Literal["real", "mock"]
Route = Literal["conversation", "md_run", "finalize", "retry_or_mock"]
RunStatus = Literal["draft", "queued", "running", "completed", "failed"]


@dataclass
class AgentState:
    user_query: str
    normalized_request: Dict[str, Any] = field(default_factory=dict)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    intent: str = "simulation_request"
    route: Route = "conversation"
    run_id: Optional[str] = None
    artifacts: Dict[str, str] = field(default_factory=dict)
    messages: List[Dict[str, str]] = field(default_factory=list)
    error: str = ""
    mode: Mode = "real"
    status: RunStatus = "draft"
    summary: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    parse_source: str = "heuristic"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if self.attachments:
            payload["attachments"] = [
                {
                    key: value
                    for key, value in attachment.items()
                    if key != "path" and not str(key).startswith("_")
                }
                for attachment in self.attachments
            ]
        else:
            payload.pop("attachments", None)
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AgentState":
        return cls(
            user_query=payload.get("user_query", ""),
            normalized_request=payload.get("normalized_request", {}) or {},
            attachments=payload.get("attachments", []) or [],
            missing_fields=payload.get("missing_fields", []) or [],
            intent=payload.get("intent", "simulation_request"),
            route=payload.get("route", "conversation"),
            run_id=payload.get("run_id"),
            artifacts=payload.get("artifacts", {}) or {},
            messages=payload.get("messages", []) or [],
            error=payload.get("error", ""),
            mode=payload.get("mode", "mock"),
            status=payload.get("status", "draft"),
            summary=payload.get("summary", {}) or {},
            validation=payload.get("validation", {}) or {},
            parse_source=payload.get("parse_source", "heuristic"),
        )
