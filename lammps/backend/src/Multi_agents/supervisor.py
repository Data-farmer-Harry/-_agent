from __future__ import annotations

from src.config.supervisor_config import SupervisorConfig, load_supervisor_config
from src.schemas.state import AgentState


class SupervisorAgent:
    def __init__(self, config: SupervisorConfig | None = None) -> None:
        self.config = config

    def route(self, state: AgentState) -> AgentState:
        config = self.config or load_supervisor_config()
        if state.error:
            if config.allow_mock_fallback or state.mode == "mock":
                state.route = "retry_or_mock"
            else:
                state.route = "finalize"
            return state

        if state.missing_fields or (state.validation and not state.validation.get("is_reasonable", True)):
            state.route = "conversation"
        elif state.artifacts:
            state.route = "finalize"
        else:
            state.route = "md_run"
        return state
