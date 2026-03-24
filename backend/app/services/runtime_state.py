from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas import ArtifactRef, TaskRoute


@dataclass
class RuntimeSessionState:
    diagram_request: dict[str, Any] | None = None
    generated_code: str | None = None
    prompt: str | None = None
    image_spec: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    html_content: str | None = None
    html_path: str | None = None
    termination_reason: str = "completed"
    repair_attempts: int = 0
    review_passed: bool | None = None
    review_summary: str = ""
    review_confidence: float | None = None
    review_issues: list[str] = field(default_factory=list)
    route_metadata: dict[str, Any] = field(default_factory=dict)
    tool_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: list[ArtifactRef] = field(default_factory=list)

    def snapshot(self, route: TaskRoute) -> dict[str, Any]:
        return {
            "workspace_id": route.workspace_id,
            "route_name": route.name,
            "deliverable": route.deliverable,
            "has_diagram_request": bool(self.diagram_request),
            "has_generated_code": bool(self.generated_code),
            "has_image_spec": bool(self.image_spec),
            "has_html": bool(self.html_content and self.html_path),
            "repair_attempts": self.repair_attempts,
            "review_passed": self.review_passed,
            "stdout_available": bool(self.stdout),
            "stderr_available": bool(self.stderr),
        }
