from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.chat import ChatAgent
from app.core.llm import LLMRequiredError
from app.core.artifacts import ArtifactService
from app.core.executor import LocalPythonExecutor
from app.agents.compute import ComputeAgent
from app.agents.recognition import RecognitionAgent
from app.runtimes.lammps import LammpsRuntime
from app.runtimes.phase_diagram import PhaseDiagramRuntime
from app.thermo.accuracy import build_thermo_accuracy_report, estimate_endpoint_liquidus
from app.thermo.registry import ThermoDatabaseCard, get_thermo_database_card
from app.thermo.service import PhaseDiagramAgentService
from app.thermo.codegen import CodeGenerationService
from app.thermo.prompts import PromptBuilder
from app.config import settings
from app.state import AgentGraphState, AgentRunResponse, AxisSpec, ConversationTurn, LastRunContext, RecognitionResult, TaskRoute, UploadedAsset
from app.agents.supervisor import SupervisorAgent
from tests.support import MINI_PNG_DATA_URL, ScriptedLLMClient, build_request


class SupervisorAndChatUnitTests(unittest.TestCase):
    def test_supervisor_routes_plain_question_to_chat(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("什么是共析点，它和包晶反应有什么区别？"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")

    def test_supervisor_routes_image_plus_generate_to_mixed(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("根据我上传的截图生成对应体系的相图。"),
            "messages": [],
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-1",
                    name="sample.png",
                    media_type="image/png",
                    data_url=MINI_PNG_DATA_URL,
                    size_bytes=128,
                )
            ],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "mixed.request")

    def test_recognition_agent_uses_llm_multimodal_path(self) -> None:
        agent = RecognitionAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请识别这张 Al-Zn 相图截图。"),
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-1",
                    name="alzn.png",
                    media_type="image/png",
                    data_url=MINI_PNG_DATA_URL,
                    size_bytes=128,
                )
            ],
        }

        result = agent.recognize(state)
        self.assertEqual(result.system, "Al-Zn")
        self.assertEqual(result.source, "llm_recognition_agent")

    def test_chat_agent_answers_follow_up_from_last_run_context(self) -> None:
        agent = ChatAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("你刚刚生成了什么代码？"),
            "messages": [
                ConversationTurn(role="user", content="请生成一张 Al-Zn 二元相图。"),
                ConversationTurn(role="assistant", content="相图已生成。"),
            ],
            "last_run_context": LastRunContext(
                run_id="run-1",
                route_name="phase_diagram.generate",
                system_name="Al-Zn",
                final_message="相图已生成。",
                generated_code_preview="from app.thermo.engine import build_calculated_phase_diagram_report",
                review_summary="passed",
                trace_summary=["thermo_database_lookup: ok", "python_execute: ok"],
            ),
            "current_context_summary": "",
        }

        result = agent.run(state)
        self.assertIn("build_calculated_phase_diagram_report", result["final_answer"])
        self.assertIn("pycalphad", result["final_answer"])

    def test_chat_agent_prompt_constrains_capability_claims(self) -> None:
        llm = ScriptedLLMClient()
        agent = ChatAgent(llm_client=llm)
        state: AgentGraphState = {
            "request": build_request("你有什么能力？"),
            "messages": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        agent.run(state)

        last_call = llm.calls[-1]
        self.assertEqual(last_call["method"], "chat_text")
        self.assertIn("Do not invent browsing", last_call["system_prompt"])
        self.assertIn("Do not describe yourself as a generic internet-enabled assistant", last_call["system_prompt"])

    def test_strict_agent_mode_rejects_supervisor_without_llm(self) -> None:
        supervisor = SupervisorAgent()
        state: AgentGraphState = {
            "request": build_request("请生成一张 Al-Zn 二元相图。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        with patch("app.config.settings.llm_enabled", False), patch("app.config.settings.require_llm_for_agents", True):
            with self.assertRaises(LLMRequiredError):
                supervisor.decide(state)

    def test_local_executor_writes_named_code_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            executor = LocalPythonExecutor(artifact_service=artifact_service, python_executable=sys.executable)
            result = executor.execute(
                "run-artifact-test",
                "from pathlib import Path\nPath('result.html').write_text('<main id=\"phase-diagram-agent-result\"></main>', encoding='utf-8')\n",
                code_filename="generated_code_attempt_1.py",
            )
            code_path = artifact_service.get_code_path("run-artifact-test", "generated_code_attempt_1.py")
            code_path_exists = code_path.exists()
            code_path_name = code_path.name

        self.assertTrue(result.success)
        self.assertTrue(code_path_name.endswith("generated_code_attempt_1.py"))
        self.assertTrue(code_path_exists)

    def test_thermo_accuracy_report_reuses_cached_result_for_same_request(self) -> None:
        card = get_thermo_database_card("Al-Zn")
        self.assertIsNotNone(card)
        report_1 = build_thermo_accuracy_report(card, temperature_min=300.0, temperature_max=1000.0)
        report_2 = build_thermo_accuracy_report(card, temperature_min=300.0, temperature_max=1000.0)
        self.assertIs(report_1, report_2)

    def test_registry_card_component_selection_overrides_database_metadata(self) -> None:
        card = ThermoDatabaseCard(
            system_name="Cu-Ni",
            aliases=(),
            summary="test",
            database_file="configs/thermo_databases/COST507-modified.tdb",
            documentation_url="",
            source_url="",
            provenance="test",
            x_component="NI",
            x_axis_label="Mole fraction Ni",
            component_selection=("CU", "NI"),
            phase_selection=("LIQUID", "FCC_A1"),
            accuracy_reference={},
            tags=(),
        )

        self.assertEqual(card.components, ("CU", "NI"))

    def test_custom_liquid_phase_name_is_supported_by_accuracy_gate(self) -> None:
        card = ThermoDatabaseCard(
            system_name="Nb-Re",
            aliases=(),
            summary="test",
            database_file="configs/thermo_databases/nbre_liu.tdb",
            documentation_url="",
            source_url="",
            provenance="test",
            x_component="RE",
            x_axis_label="Mole fraction Re",
            component_selection=("NB", "RE"),
            phase_selection=("BCC_RENB", "CHI_RENB", "FCC_RENB", "HCP_RENB", "LIQUID_RENB", "SIGMARENB"),
            accuracy_reference={
                "left_endmember": "Nb",
                "right_endmember": "Re",
                "left_liquidus_K": 2750,
                "right_liquidus_K": 3459,
                "liquid_phase_name": "LIQUID_RENB",
                "liquidus_tolerance_K": 35,
            },
            tags=(),
        )

        left = estimate_endpoint_liquidus(card, side="left", temperature_min=300.0, temperature_max=3600.0)
        right = estimate_endpoint_liquidus(card, side="right", temperature_min=300.0, temperature_max=3600.0)

        self.assertTrue(left.passes)
        self.assertTrue(right.passes)

    def test_phase_request_parser_keeps_explicit_user_temperature_range_when_llm_drifts(self) -> None:
        class DriftingPhaseLLM(ScriptedLLMClient):
            def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1):  # type: ignore[override]
                payload = super().chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if payload and "Return conservative JSON for a binary phase diagram request" in system_prompt:
                    payload["temperature_min"] = 400.0
                    payload["temperature_max"] = 800.0
                return payload

        service = PhaseDiagramAgentService(
            codegen_service=CodeGenerationService(prompt_builder=PromptBuilder(), llm_client=DriftingPhaseLLM()),
            llm_client=DriftingPhaseLLM(),
        )

        request, planning = service.infer_request_from_chat(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。",
            {
                "system_name": "",
                "diagram_type": "binary",
                "temperature_min": 300.0,
                "temperature_max": 1000.0,
                "pressure": 101325.0,
                "step_size": 50.0,
                "notes": "",
            },
        )

        self.assertEqual(request.system_name, "Al-Zn")
        self.assertEqual(request.temperature_min, 300.0)
        self.assertEqual(request.temperature_max, 1000.0)
        self.assertEqual(planning["source"], "llm_request_interpreter")

    def test_phase_request_parser_falls_back_when_llm_numeric_fields_are_invalid(self) -> None:
        class MalformedPhaseLLM(ScriptedLLMClient):
            def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1):  # type: ignore[override]
                payload = super().chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if payload and "Return conservative JSON for a binary phase diagram request" in system_prompt:
                    payload["step_size"] = "conservative"
                    payload["pressure"] = "keep default"
                return payload

        llm = MalformedPhaseLLM()
        service = PhaseDiagramAgentService(
            codegen_service=CodeGenerationService(prompt_builder=PromptBuilder(), llm_client=llm),
            llm_client=llm,
        )

        request, planning = service.infer_request_from_chat(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。",
            {
                "system_name": "",
                "diagram_type": "binary",
                "temperature_min": 300.0,
                "temperature_max": 1000.0,
                "pressure": 101325.0,
                "step_size": 50.0,
                "notes": "",
            },
        )

        self.assertEqual(request.temperature_min, 300.0)
        self.assertEqual(request.temperature_max, 1000.0)
        self.assertEqual(request.pressure, 101325.0)
        self.assertEqual(request.step_size, 50.0)

    def test_phase_request_parser_tolerates_non_numeric_confidence(self) -> None:
        class MalformedConfidenceLLM(ScriptedLLMClient):
            def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1):  # type: ignore[override]
                payload = super().chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if payload and "Return conservative JSON for a binary phase diagram request" in system_prompt:
                    payload["confidence"] = "conservative"
                return payload

        llm = MalformedConfidenceLLM()
        service = PhaseDiagramAgentService(
            codegen_service=CodeGenerationService(prompt_builder=PromptBuilder(), llm_client=llm),
            llm_client=llm,
        )

        request, planning = service.infer_request_from_chat(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线。",
            {
                "system_name": "",
                "diagram_type": "binary",
                "temperature_min": 300.0,
                "temperature_max": 1000.0,
                "pressure": 101325.0,
                "step_size": 50.0,
                "notes": "",
            },
        )

        self.assertEqual(request.system_name, "Al-Zn")
        self.assertEqual(planning["source"], "llm_request_interpreter")
        self.assertEqual(planning["confidence"], 0.82)

    def test_thermo_rag_can_retrieve_card_from_chinese_query(self) -> None:
        service = PhaseDiagramAgentService(
            codegen_service=CodeGenerationService(prompt_builder=PromptBuilder(), llm_client=ScriptedLLMClient()),
            llm_client=ScriptedLLMClient(),
        )

        card, retrieval = service.lookup_registered_database(
            "未知体系",
            query_text="我想计算一张铝锌二元相图，重点看看液相线和FCC_A1区域。",
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card["system_name"], "Al-Zn")
        self.assertEqual(retrieval["selection_strategy"], "rag_auto_select")
        self.assertEqual(retrieval["lookup_mode"], "exact_then_rag")

    def test_thermo_rag_keeps_honest_failure_for_ambiguous_query(self) -> None:
        service = PhaseDiagramAgentService(
            codegen_service=CodeGenerationService(prompt_builder=PromptBuilder(), llm_client=ScriptedLLMClient()),
            llm_client=ScriptedLLMClient(),
        )

        original_threshold = settings.thermo_rag_auto_select_threshold
        try:
            settings.thermo_rag_auto_select_threshold = 0.95
            card, retrieval = service.lookup_registered_database(
                "未知体系",
                query_text="帮我找一种适合科研演示的二元相图数据库。",
            )
        finally:
            settings.thermo_rag_auto_select_threshold = original_threshold

        self.assertIsNone(card)
        self.assertEqual(retrieval["lookup_mode"], "exact_then_rag")
        self.assertFalse(retrieval["rag"]["matched"])

    def test_phase_runtime_build_request_uses_recognition_temperature_and_notes(self) -> None:
        llm = ScriptedLLMClient()
        codegen = CodeGenerationService(prompt_builder=PromptBuilder(), llm_client=llm)
        service = PhaseDiagramAgentService(codegen_service=codegen, llm_client=llm)
        runtime = PhaseDiagramRuntime(
            artifact_service=ArtifactService(root_dir=Path(tempfile.mkdtemp())),
            codegen_service=codegen,
            phase_agent_service=service,
        )
        request = build_request(
            "根据这张截图生成对应体系相图。",
            system_name="",
            temperature_min=300.0,
            temperature_max=1800.0,
        )
        recognition_result = RecognitionResult(
            system="Al-Zn",
            diagram_type="binary",
            x_axis=AxisSpec(label="Mole fraction Zn", minimum=0.0, maximum=1.0, unit=""),
            y_axis=AxisSpec(label="Temperature", minimum=320.0, maximum=980.0, unit="K"),
            phases=["FCC_A1", "HCP_A3", "LIQUID"],
            labels=["eutectic", "liquidus"],
            confidence=0.88,
            source="llm_recognition_agent",
            raw_summary="Detected Al-Zn binary diagram with temperature axis in Kelvin.",
        )

        diagram_request, planning = runtime._build_request(request, recognition_result)

        self.assertEqual(diagram_request.system_name, "Al-Zn")
        self.assertEqual(diagram_request.temperature_min, 320.0)
        self.assertEqual(diagram_request.temperature_max, 980.0)
        self.assertIn("recognized_phases=FCC_A1, HCP_A3, LIQUID", diagram_request.notes)
        self.assertEqual(planning["source"], "llm_request_interpreter")

    def test_compute_agent_does_not_append_runtime_message_before_chat(self) -> None:
        class StubPhaseRuntime:
            def run(self, **kwargs):  # type: ignore[no-untyped-def]
                return AgentRunResponse(
                    success=True,
                    run_id="run-1",
                    conversation_id="conv-1",
                    route=TaskRoute(name="phase_diagram.generate", compute_domain="phase_diagram"),
                    final_message="runtime result",
                    artifacts=[],
                    plan_steps=[],
                    trace=[],
                    generated_code=None,
                    termination_reason="review_passed",
                    metadata={},
                    summary={},
                    run_status="completed",
                    stdout="",
                    stderr="",
                    html_content=None,
                    html_path=None,
                )

        compute = ComputeAgent(
            phase_diagram_runtime=StubPhaseRuntime(),  # type: ignore[arg-type]
            lammps_runtime=LammpsRuntime(artifact_service=ArtifactService(root_dir=Path(tempfile.mkdtemp()))),
        )
        state: AgentGraphState = {
            "run_id": "run-1",
            "conversation_id": "conv-1",
            "request": build_request("请生成一张 Al-Zn 二元相图。", conversation_id="conv-1", system_name="Al-Zn"),
            "messages": [ConversationTurn(role="user", content="请生成一张 Al-Zn 二元相图。")],
            "recognition_result": None,
            "compute_domain": "phase_diagram",
            "artifact_messages": [],
            "plan_steps": [],
            "trace": [],
            "event_sink": None,
        }

        result = compute.run(state, {"compute_domain": "phase_diagram"})

        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0].role, "user")
