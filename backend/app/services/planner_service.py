from __future__ import annotations

from app.schemas import PlanStep, TaskRoute
from app.services.agent_manifest import get_route_definition


class PlannerService:
    def build_plan(self, route: TaskRoute) -> list[PlanStep]:
        route_definition = get_route_definition(route.name)
        plan_steps: list[PlanStep] = []
        for index, template in enumerate(route_definition.step_templates, start=1):
            plan_steps.append(
                PlanStep(
                    index=index,
                    tool_name=template.tool_name,
                    input=dict(template.input_overrides),
                    status="pending",
                    retryable=template.retryable,
                    description=template.description,
                    stage=template.stage,
                    metadata={"workspace_id": route.workspace_id, "route_name": route.name},
                )
            )
        return plan_steps
