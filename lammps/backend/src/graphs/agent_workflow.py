from __future__ import annotations

from pathlib import Path

from src.Multi_agents.conversation_agent import ConversationAgent
from src.Multi_agents.md_agent import MDAgent
from src.Multi_agents.supervisor import SupervisorAgent
from src.schemas.state import AgentState


class AgentWorkflow:
    def __init__(self) -> None:
        self.supervisor = SupervisorAgent()
        self.conversation = ConversationAgent()
        self.md_agent = MDAgent()

    def handle_chat(
        self,
        query: str,
        normalized_request: dict | None = None,
    ) -> AgentState:
        state = AgentState(
            user_query=query,
            normalized_request=normalized_request or {},
        )
        state = self.supervisor.route(state)
        state = self.conversation.handle(state)
        return self.supervisor.route(state)

    def run(self, state: AgentState, output_dir: Path) -> AgentState:
        state.status = "running"
        state = self.supervisor.route(state)
        if state.route == "conversation":
            return self.conversation.handle(state)
        return self.md_agent.run(state, output_dir)
