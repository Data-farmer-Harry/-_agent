from __future__ import annotations

import unittest

from app.schemas import AgentChatRequest
from app.services.agent_chat_service import AgentChatService
from app.services.codegen_service import CodeGenerationService
from app.services.phase_diagram_agent_service import PhaseDiagramAgentService
from app.services.prompt_builder import PromptBuilder


class AgentChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        codegen_service = CodeGenerationService(prompt_builder=PromptBuilder())
        phase_agent_service = PhaseDiagramAgentService(codegen_service=codegen_service)
        self.service = AgentChatService(phase_agent_service=phase_agent_service)

    def test_chat_service_builds_phase_run_request_from_text(self) -> None:
        run_request = self.service.build_run_request(
            AgentChatRequest(
                message="请生成一张 Fe-Cu 二元相图，温度范围 300-1800K，步长 25。",
                temperature_min=300.0,
                temperature_max=1800.0,
                pressure=101325.0,
                step_size=50.0,
            )
        )

        self.assertIsNone(run_request.task_type_hint)
        self.assertIsNone(run_request.workspace_hint)
        self.assertIsNotNone(run_request.diagram_request)
        self.assertEqual(run_request.diagram_request.system_name, "Fe-Cu")
        self.assertEqual(run_request.diagram_request.step_size, 25.0)
        self.assertEqual(run_request.context["chat_mode"], "conversation")

    def test_chat_service_builds_image_run_request_when_attachment_present(self) -> None:
        run_request = self.service.build_run_request(
            AgentChatRequest(
                message="请识别这张相图截图。",
                image_data_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ+wP+2xqB9QAAAABJRU5ErkJggg==",
                filename="phase.png",
                system_name="Fe-C",
                chart_title="Fe-C screenshot",
                temperature_min=300.0,
                temperature_max=1800.0,
                pressure=101325.0,
                step_size=50.0,
                x_axis={"label": "Composition", "minimum": 0.0, "maximum": 6.7},
                y_axis={"label": "Temperature", "minimum": 0.0, "maximum": 1600.0},
            )
        )

        self.assertIsNone(run_request.task_type_hint)
        self.assertIsNotNone(run_request.image_diagram_request)
        self.assertEqual(run_request.image_diagram_request.filename, "phase.png")
        self.assertEqual(run_request.context["chat_mode"], "multimodal_dialog")

    def test_chat_service_routes_lammps_messages_to_lammps_generate(self) -> None:
        run_request = self.service.build_run_request(
            AgentChatRequest(
                message="请帮我准备一个 LAMMPS 分子动力学模拟流程。",
                temperature_min=300.0,
                temperature_max=1800.0,
                pressure=101325.0,
                step_size=50.0,
            )
        )

        self.assertIsNone(run_request.task_type_hint)
        self.assertIsNone(run_request.workspace_hint)
        self.assertIsNotNone(run_request.diagram_request)
        self.assertIn("LAMMPS", run_request.user_input)

    def test_chat_service_builds_html_redraw_request_for_explanation_prompt(self) -> None:
        run_request = self.service.build_run_request(
            AgentChatRequest(
                message="请把下面的相图讲解整理成一个适合组会展示的 HTML 页面。",
                system_name="Fe-Cu",
                chart_title="Fe-Cu explanation",
                temperature_min=300.0,
                temperature_max=1800.0,
                pressure=101325.0,
                step_size=50.0,
            )
        )

        self.assertIsNotNone(run_request.html_redraw_request)
        self.assertEqual(run_request.html_redraw_request.system_name, "Fe-Cu")
        self.assertEqual(run_request.context["chat_mode"], "conversation")


if __name__ == "__main__":
    unittest.main()
