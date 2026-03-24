from __future__ import annotations

import json

from app.schemas import ArtifactRef
from app.tools.base import BaseTool, ToolExecutionResult


class LammpsCommandRouterTool(BaseTool):
    name = "lammps_command_router"
    description = "Accept a LAMMPS-oriented command and return the current stub routing plan"
    workspace_id = "lammps"
    supports_routes = ("lammps.generate", "lammps.repair")
    tags = ("lammps", "routing", "stub")
    produces_artifacts = ("text", "json")
    consumes = ("user_input", "context")

    @staticmethod
    def _infer_intent(user_input: str) -> str:
        lowered = user_input.lower()
        if any(keyword in lowered for keyword in ("repair", "debug", "fix", "报错", "错误")):
            return "repair"
        if any(keyword in lowered for keyword in ("relax", "minimize", "能量最小化")):
            return "minimize"
        if any(keyword in lowered for keyword in ("md", "dynamics", "动力学")):
            return "molecular_dynamics"
        return "generate"

    def run(self, input_data: dict, context: dict) -> ToolExecutionResult:
        user_input = (input_data.get("user_input") or context.get("user_input") or "").strip()
        request_context = input_data.get("context") or {}
        intent = self._infer_intent(user_input)
        future_tool_chain = ["lammps_command_router", "lammps_codegen", "lammps_execute", "lammps_repair"]
        next_actions = [
            "Choose the simulation target and potential family.",
            "Translate the request into a LAMMPS input template.",
            "Execute LAMMPS and collect structured artifacts.",
            "Add a repair/debug loop for failed simulation commands.",
        ]
        summary = "LAMMPS stub accepted the request and returned the reserved tool-chain outline."
        content_lines = [
            "LAMMPS workspace stub",
            f"User input: {user_input or '(empty)'}",
            f"Inferred intent: {intent}",
            "Next actions:",
            *[f"- {item}" for item in next_actions],
        ]
        if request_context:
            content_lines.append(f"Context keys: {', '.join(sorted(request_context.keys()))}")

        return ToolExecutionResult(
            success=True,
            summary=summary,
            output={
                "message": "LAMMPS workspace is connected through a stub router. No simulation has been launched yet.",
                "workspace_status": "stub_ready",
                "intent": intent,
                "tool_chain_outline": future_tool_chain,
                "next_actions": next_actions,
                "user_input": user_input,
                "context_keys": sorted(request_context.keys()),
            },
            artifacts=[
                ArtifactRef(
                    kind="text",
                    name="lammps_stub_summary.txt",
                    content="\n".join(content_lines),
                ),
                ArtifactRef(
                    kind="json",
                    name="lammps_stub_outline.json",
                    content=json.dumps(
                        {
                            "intent": intent,
                            "tool_chain_outline": future_tool_chain,
                            "next_actions": next_actions,
                            "context_keys": sorted(request_context.keys()),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            ],
            metadata={
                "workspace_status": "stub_ready",
                "intent": intent,
                "future_tool_chain": future_tool_chain,
            },
            state_delta={"lammps_plan_updated": True},
        )
