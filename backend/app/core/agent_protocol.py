from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


AGENT_PROTOCOL_VERSION = "agent-protocol/v1"


class AgentEnvelope(BaseModel):
    """Stable envelope for agent-to-agent state transitions.

    The graph still carries Python/Pydantic objects internally, but every
    recorded transition can also be represented as JSON through this envelope.
    """

    protocol_version: str = AGENT_PROTOCOL_VERSION
    message_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    run_id: str = ""
    conversation_id: str = "default"
    sender: str
    receiver: str = "AgentGraph"
    message_type: str
    payload_schema: str
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def public_metadata(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "message_type": self.message_type,
            "payload_schema": self.payload_schema,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


def build_agent_envelope(
    *,
    run_id: str,
    conversation_id: str,
    sender: str,
    receiver: str = "AgentGraph",
    message_type: str,
    payload_schema: str,
    payload: dict[str, Any],
    confidence: float | None = None,
    warnings: list[str] | None = None,
) -> AgentEnvelope:
    return AgentEnvelope(
        run_id=run_id,
        conversation_id=conversation_id or "default",
        sender=sender,
        receiver=receiver,
        message_type=message_type,
        payload_schema=payload_schema,
        payload=payload,
        confidence=confidence,
        warnings=warnings or [],
    )


def summarize_protocol_messages(messages: list[AgentEnvelope]) -> dict[str, Any]:
    schemas = sorted({message.payload_schema for message in messages})
    senders = sorted({message.sender for message in messages})
    message_types = sorted({message.message_type for message in messages})
    return {
        "protocol_version": AGENT_PROTOCOL_VERSION,
        "message_count": len(messages),
        "senders": senders,
        "message_types": message_types,
        "payload_schemas": schemas,
    }
