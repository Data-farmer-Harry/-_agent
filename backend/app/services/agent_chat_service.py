from __future__ import annotations

from app.schemas import AgentChatRequest, AgentRunRequest, AxisCalibration, DiagramRequest, HtmlRedrawRequest, ImageDiagramRequest
from app.services.phase_diagram_agent_service import PhaseDiagramAgentService


class AgentChatService:
    def __init__(self, phase_agent_service: PhaseDiagramAgentService) -> None:
        self.phase_agent_service = phase_agent_service

    @staticmethod
    def _default_axis(label: str, minimum: float, maximum: float) -> AxisCalibration:
        return AxisCalibration(label=label, minimum=minimum, maximum=maximum)

    def build_run_request(self, request: AgentChatRequest) -> AgentRunRequest:
        message = request.message.strip() or "请生成一个相图页面。"
        diagram_request, planning_metadata = self.phase_agent_service.infer_request_from_chat(
            message,
            {
                "system_name": request.system_name,
                "diagram_type": request.diagram_type,
                "temperature_min": request.temperature_min,
                "temperature_max": request.temperature_max,
                "pressure": request.pressure,
                "step_size": request.step_size,
                "notes": request.notes,
            },
        )

        image_request = None
        if request.image_data_url:
            x_axis = request.x_axis or self._default_axis("Composition", 0.0, 100.0)
            y_axis = request.y_axis or self._default_axis("Temperature", 0.0, 1600.0)
            image_request = ImageDiagramRequest(
                image_data_url=request.image_data_url,
                filename=request.filename,
                system_name=request.system_name,
                chart_title=request.chart_title,
                diagram_type=request.diagram_type,
                x_axis=x_axis,
                y_axis=y_axis,
                notes=request.notes or message,
            )

        return AgentRunRequest(
            user_input=message,
            workspace_hint=request.workspace_hint,
            diagram_request=DiagramRequest.model_validate(diagram_request),
            image_diagram_request=image_request,
            html_redraw_request=HtmlRedrawRequest(
                message=message,
                system_name=request.system_name or diagram_request.system_name,
                chart_title=request.chart_title,
                diagram_type=request.diagram_type,
                notes=request.notes or message,
                image_data_url=request.image_data_url,
                filename=request.filename,
            ),
            context={
                "chat_mode": "multimodal_dialog" if image_request else "conversation",
                "planning": planning_metadata,
                "source": "chat_attachment" if image_request else "chat_text",
            },
        )
