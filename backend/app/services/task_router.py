from __future__ import annotations

from app.schemas import AgentRunRequest, TaskRoute
from app.services.agent_decision_service import AgentDecisionService
from app.services.agent_catalog import AgentCatalogService


class TaskRouter:
    def __init__(self, catalog_service: AgentCatalogService, decision_service: AgentDecisionService | None = None) -> None:
        self.catalog_service = catalog_service
        self.decision_service = decision_service or AgentDecisionService()

    def route(self, request: AgentRunRequest) -> TaskRoute:
        decision = self.decision_service.decide(request)
        route = self.catalog_service.build_route_decision(decision.route_name, decision.reason)
        return TaskRoute(
            name=route.name,
            workspace_id=route.workspace_id,
            reason=route.reason,
            selected_tool=route.selected_tool,
            available_tools=route.available_tools,
            reserved_tools=route.reserved_tools,
            entry_tool=route.entry_tool,
            input_channels=route.input_channels,
            deliverable=route.deliverable,
            narrative=route.narrative,
            intent=decision.intent,
            decision_source=decision.source,
            decision_confidence=decision.confidence,
        )
