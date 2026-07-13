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
from app.thermo.rag_index import build_thermo_card_index
from app.thermo.rag_retriever import search_thermo_cards
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
        llm = ScriptedLLMClient()
        supervisor = SupervisorAgent(llm_client=llm)
        state: AgentGraphState = {
            "request": build_request("什么是共析点，它和包晶反应有什么区别？"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")
        self.assertEqual(decision["source"], "heuristic_supervisor")
        self.assertEqual(llm.calls, [])
        audit = decision["supervisor_audit"]
        self.assertTrue(audit["passed"])
        self.assertFalse(audit["requires_llm_review"])
        self.assertGreater(audit["confidence_margin"], 0.18)
        self.assertEqual(audit["confidence_formula"]["source"], "deterministic_supervisor_v2")
        self.assertFalse(audit["confidence_formula"]["llm_confidence_used"])
        self.assertEqual(
            audit["dag"]["topological_order"],
            ["signal_extract", "candidate_score", "asset_prerequisite", "compute_prerequisite", "route_contract", "commit_route"],
        )

    def test_supervisor_treats_phase_diagram_concept_question_as_chat(self) -> None:
        llm = ScriptedLLMClient()
        supervisor = SupervisorAgent(llm_client=llm)
        state: AgentGraphState = {
            "request": build_request("Fe-C 相图中的共析点是什么？请给出知识库依据。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")
        self.assertEqual(llm.calls, [])

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

    def test_recognition_agent_normalizes_min_max_fields_and_preserves_low_temperature_window(self) -> None:
        class NormalizingRecognitionLLM(ScriptedLLMClient):
            def chat_multimodal_json(self, *, system_prompt: str, user_prompt: str, image_data_url: str, max_tokens: int = 2000, temperature: float = 0.1):  # type: ignore[override]
                return {
                    "system": "",
                    "diagram_type": "Binary Phase Diagram",
                    "x_axis": {"label": "Mole fraction Zn", "min": 0.0, "max": 1.0, "unit": ""},
                    "y_axis": {"label": "Temperature (K)", "min": 4.0, "max": 5.0, "unit": "K"},
                    "plot_region": {"left": 0.16, "top": 0.12, "right": 0.88, "bottom": 0.84, "confidence": 0.87},
                    "phases": ["Liquid", "fcc_a1", "HCP_A3", "fcc_a1"],
                    "critical_points": [{"label": "eutectic", "composition": "0.42", "temperature": "4.5", "x_norm": 0.48, "y_norm": 0.41, "confidence": 0.8, "notes": "stub"}],
                    "labels": ["铝锌二元相图"],
                    "confidence": 0.95,
                    "raw_summary": "识别到铝锌二元相图截图。",
                }

        agent = RecognitionAgent(llm_client=NormalizingRecognitionLLM())
        state: AgentGraphState = {
            "request": build_request("请识别这张铝锌相图截图。"),
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-1",
                    name="alzn_phase.png",
                    media_type="image/png",
                    data_url=MINI_PNG_DATA_URL,
                    size_bytes=128,
                )
            ],
        }

        result = agent.recognize(state)

        self.assertEqual(result.system, "Al-Zn")
        self.assertEqual(result.diagram_type, "binary")
        self.assertEqual(result.x_axis.minimum, 0.0)
        self.assertEqual(result.x_axis.maximum, 1.0)
        self.assertEqual(result.y_axis.minimum, 4.0)
        self.assertEqual(result.y_axis.maximum, 5.0)
        self.assertAlmostEqual(result.plot_region.left or 0, 0.16, places=3)
        self.assertAlmostEqual(result.plot_region.bottom or 0, 0.84, places=3)
        self.assertEqual(result.plot_region.source, "llm_plot_region")
        self.assertEqual(result.phases, ["LIQUID", "FCC_A1", "HCP_A3"])
        self.assertEqual(result.critical_points[0].temperature, 4.5)
        self.assertAlmostEqual(result.critical_points[0].x_norm or 0, 0.48, places=3)
        self.assertAlmostEqual(result.critical_points[0].y_norm or 0, 0.41, places=3)

    def test_recognition_agent_normalizes_phase_objects_and_dict_like_strings(self) -> None:
        class RichPhaseRecognitionLLM(ScriptedLLMClient):
            def chat_multimodal_json(self, *, system_prompt: str, user_prompt: str, image_data_url: str, max_tokens: int = 2000, temperature: float = 0.1):  # type: ignore[override]
                return {
                    "system": "Al-Ni",
                    "diagram_type": "binary",
                    "x_axis": {"label": "Composition", "min": 0.0, "max": 1.0, "unit": "mole fraction"},
                    "y_axis": {"label": "Temperature", "min": 200.0, "max": 2200.0, "unit": "°C"},
                    "plot_region": {"left": 0.15, "top": 0.10, "right": 0.95, "bottom": 0.85, "confidence": 0.90},
                    "phases": [
                        {"name": "L", "description": "Liquid phase"},
                        "{'name': 'Al', 'description': 'Aluminum solid solution'}",
                        {"phase": "NiAl3"},
                        "Ni5Al3",
                    ],
                    "critical_points": [],
                    "labels": ["Al-Ni phase diagram"],
                    "confidence": 0.9,
                    "raw_summary": "识别到 Al-Ni 二元相图。",
                }

        agent = RecognitionAgent(llm_client=RichPhaseRecognitionLLM())
        state: AgentGraphState = {
            "request": build_request("请识别这张铝镍相图截图。"),
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-rich-phase",
                    name="alni_phase.png",
                    media_type="image/png",
                    data_url=MINI_PNG_DATA_URL,
                    size_bytes=128,
                )
            ],
        }

        result = agent.recognize(state)

        self.assertEqual(result.phases, ["L", "AL", "NIAL3", "NI5AL3"])

    def test_supervisor_routes_uploaded_image_explanation_to_recognition(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请解释我上传这张相图里的相区和关键点。"),
            "messages": [],
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-2",
                    name="phase.png",
                    media_type="image/png",
                    data_url=MINI_PNG_DATA_URL,
                    size_bytes=128,
                )
            ],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "recognition.analyze")

    def test_supervisor_routes_uploaded_image_interactive_html_to_recognition(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请把这张相图生成交互式html。"),
            "messages": [],
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-html",
                    name="phase.png",
                    media_type="image/png",
                    data_url=MINI_PNG_DATA_URL,
                    size_bytes=128,
                )
            ],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "recognition.analyze")
        self.assertEqual(decision["intent"], "recognize_image_to_interactive_simulator")

    def test_supervisor_routes_recent_recognition_explain_to_chat(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("讲解一下"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "recognition_result": RecognitionResult(
                system="Al-Ni",
                diagram_type="binary",
                x_axis=AxisSpec(label="composition"),
                y_axis=AxisSpec(label="temperature", unit="K"),
                phases=["LIQUID", "NIAL3"],
                critical_points=[],
                labels=["Al-Ni phase diagram"],
                confidence=0.82,
                source="llm_recognition_agent",
                raw_summary="识别到 Al-Ni 二元相图。",
            ),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")

    def test_supervisor_routes_uploaded_image_plus_regenerate_to_mixed(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请先识别我上传的相图，再根据识别结果重新生成一张对应体系的可计算相图。"),
            "messages": [],
            "uploaded_assets": [
                UploadedAsset(
                    asset_id="img-3",
                    name="phase.png",
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

    def test_supervisor_routes_recognition_follow_up_generation_to_phase_compute(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("根据你刚才识别的结果生成对应体系的可计算相图。"),
            "messages": [],
            "uploaded_assets": [],
            "recognition_result": RecognitionResult(
                system="Al-Zn",
                diagram_type="binary",
                x_axis=AxisSpec(label="Mole fraction Zn", minimum=0.0, maximum=1.0),
                y_axis=AxisSpec(label="Temperature (K)", minimum=300.0, maximum=1000.0, unit="K"),
                phases=["LIQUID", "FCC_A1", "HCP_A3"],
                confidence=0.95,
                source="llm_recognition_agent",
                raw_summary="识别到 Al-Zn 二元相图。",
            ),
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "phase_diagram.generate")

    def test_supervisor_routes_lammps_follow_up_question_to_chat(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("你刚刚这轮模拟用了什么势函数，为什么这么选？"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(
                run_id="run-lammps-1",
                route_name="lammps.generate",
                compute_domain="lammps",
                system_name="Cu",
                final_message="LAMMPS 结果已生成。",
            ),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")

    def test_supervisor_clarifies_underspecified_lammps_request(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("帮我用 LAMMPS 跑一下模拟。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")
        self.assertEqual(decision["intent"], "clarify_lammps_request")
        self.assertIn("material", decision["clarification_slots"])
        self.assertIn("temperature", decision["clarification_slots"])
        self.assertIn("steps", decision["clarification_slots"])
        self.assertFalse(decision["supervisor_audit"]["requires_llm_review"])

    def test_supervisor_routes_complete_lammps_request_to_runtime(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请用 LAMMPS 做 Cu heating，800K，4000 steps，NVT，EAM 势函数。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "lammps.generate")
        self.assertEqual(decision["compute_domain"], "lammps")
        self.assertEqual(decision["intent"], "run_lammps_simulation")

    def test_supervisor_uses_ambiguous_fallback_for_phase_and_lammps_overlap(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请同时用 LAMMPS 模拟 Al 并生成 Al-Zn 相图。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")
        self.assertEqual(decision["intent"], "clarify_ambiguous_request")
        self.assertTrue(decision["supervisor_audit"]["llm_reviewed"])
        self.assertFalse(decision["supervisor_audit"]["requires_llm_review"])

    def test_supervisor_uses_llm_and_dag_fallback_for_missing_phase_system(self) -> None:
        llm = ScriptedLLMClient()
        supervisor = SupervisorAgent(llm_client=llm)
        state: AgentGraphState = {
            "request": build_request("请生成一张相图。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertTrue(any(call["method"] == "chat_json" for call in llm.calls))
        self.assertEqual(decision["route_name"], "conversation.answer")
        self.assertEqual(decision["intent"], "clarify_phase_request")
        self.assertEqual(decision["source"], "supervisor_dag_fallback")
        self.assertTrue(decision["supervisor_audit"]["llm_reviewed"])
        self.assertIn("compute_prerequisite", decision["supervisor_audit"]["rejected_failures"])

    def test_supervisor_confidence_ignores_model_self_reported_score(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("请用 LAMMPS 做 Cu heating，800K，4000 steps，NVT，EAM 势函数。"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }
        low = supervisor._supervisor_audit(
            state,
            {
                "route_name": "lammps.generate",
                "next_step": "compute",
                "compute_domain": "lammps",
                "confidence": 0.01,
            },
        )
        high = supervisor._supervisor_audit(
            state,
            {
                "route_name": "lammps.generate",
                "next_step": "compute",
                "compute_domain": "lammps",
                "confidence": 0.99,
            },
        )

        self.assertEqual(low["calibrated_confidence"], high["calibrated_confidence"])
        self.assertGreater(low["calibrated_confidence"], 0.78)
        self.assertFalse(low["confidence_formula"]["llm_confidence_used"])

    def test_chat_agent_returns_phase_clarification_from_dag_fallback(self) -> None:
        llm = ScriptedLLMClient()
        agent = ChatAgent(llm_client=llm)
        state: AgentGraphState = {
            "request": build_request("请生成一张相图。"),
            "messages": [],
            "last_run_context": LastRunContext(),
            "supervisor_decision": {
                "route_name": "conversation.answer",
                "intent": "clarify_phase_request",
            },
            "current_context_summary": "",
        }

        result = agent.run(state)

        self.assertIn("材料体系", result["final_answer"])
        self.assertIn("Al-Zn", result["final_answer"])
        self.assertEqual(result["response_metadata"]["materials_rag"]["gate_reason"], "contextual_follow_up")
        self.assertEqual(llm.calls, [])

    def test_chat_agent_returns_lammps_clarification_from_supervisor_decision(self) -> None:
        agent = ChatAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("帮我用 LAMMPS 跑一下模拟。"),
            "messages": [],
            "last_run_context": LastRunContext(),
            "supervisor_decision": {
                "route_name": "conversation.answer",
                "intent": "clarify_lammps_request",
                "clarification_slots": ["material", "task_type", "temperature", "steps"],
            },
            "current_context_summary": "",
        }

        result = agent.run(state)

        self.assertIn("补充", result["final_answer"])
        self.assertIn("材料", result["final_answer"])
        self.assertIn("温度", result["final_answer"])
        self.assertIn("步数", result["final_answer"])

    def test_supervisor_routes_phase_html_follow_up_to_chat(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("可以帮我生成交互式html吗？"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(
                run_id="run-phase-1",
                route_name="phase_diagram.generate",
                compute_domain="phase_diagram",
                system_name="Al-Co",
                final_message="相图已生成。",
                artifact_names=["result.html"],
            ),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")

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

    def test_chat_agent_explains_recent_recognition_without_triggering_html(self) -> None:
        agent = ChatAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("讲解一下"),
            "messages": [
                ConversationTurn(role="user", content="请识别这张 Al-Ni 相图。"),
                ConversationTurn(role="assistant", content="已完成识别。"),
            ],
            "route": TaskRoute(name="conversation.answer", workspace_id="materials_agent", reason="follow-up", selected_tool="chat"),
            "recognition_result": RecognitionResult(
                system="Al-Ni",
                diagram_type="binary",
                x_axis=AxisSpec(label="Mole fraction Ni", minimum=0.0, maximum=1.0, unit=""),
                y_axis=AxisSpec(label="Temperature", minimum=300.0, maximum=1800.0, unit="K"),
                phases=["LIQUID", "NIAL3", "NI2AL3"],
                critical_points=[],
                labels=["Al-Ni phase diagram"],
                confidence=0.88,
                source="llm_recognition_agent",
                raw_summary="识别到 Al-Ni 二元相图。",
            ),
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        result = agent.run(state)

        self.assertIn("RecognitionAgent", result["final_answer"])
        self.assertFalse(result["html_content"])
        self.assertFalse(result["artifact_messages"])

    def test_chat_agent_prompt_constrains_capability_claims(self) -> None:
        llm = ScriptedLLMClient()
        agent = ChatAgent(llm_client=llm)
        state: AgentGraphState = {
            "request": build_request("你有什么能力？"),
            "messages": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        result = agent.run(state)

        last_call = llm.calls[-1]
        self.assertEqual(last_call["method"], "chat_text")
        self.assertEqual(last_call["max_tokens"], 800)
        self.assertIn("Do not invent browsing", last_call["system_prompt"])
        self.assertIn("Do not describe yourself as a generic internet-enabled assistant", last_call["system_prompt"])
        self.assertEqual(result["response_metadata"]["chat_prompt_mode"], "lean_direct")

    def test_chat_agent_reconstructs_previous_phase_result_via_internal_image_recognition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_service = ArtifactService(root_dir=Path(tmp_dir))
            result_path = artifact_service.get_result_path("run-phase-html-1")
            result_path.write_text(
                (
                    "<html><head></head><body><main id=\"phase-diagram-agent-result\">"
                    f"<img alt=\"Al-Co calculated phase diagram\" src=\"{MINI_PNG_DATA_URL}\">"
                    "</main></body></html>"
                ),
                encoding="utf-8",
            )
            agent = ChatAgent(llm_client=ScriptedLLMClient(), artifact_service=artifact_service)
            state: AgentGraphState = {
                "request": build_request("帮我生成交互式html"),
                "messages": [
                    ConversationTurn(role="user", content="请生成一张 Al-Co 二元相图。"),
                    ConversationTurn(role="assistant", content="相图已生成。"),
                ],
                "last_run_context": LastRunContext(
                    run_id="run-phase-html-1",
                    route_name="phase_diagram.generate",
                    compute_domain="phase_diagram",
                    system_name="Al-Co",
                    final_message="相图已生成。",
                    artifact_names=["result.html"],
                ),
                "current_context_summary": "",
            }

            result = agent.run(state)

        self.assertIn("不含原始图像", result["final_answer"])
        self.assertTrue(result["html_content"])
        self.assertIn('id="recognition-simulator-root"', result["html_content"])
        self.assertIn("recognition-reconstruction-canvas", result["html_content"])
        self.assertIn("generated_canvas_vector_reconstruction", result["html_content"])
        self.assertIn("reconstruction_scene", result["html_content"])
        self.assertNotIn("pixels_rgba_b64", result["html_content"])
        self.assertNotIn(MINI_PNG_DATA_URL, result["html_content"])
        self.assertTrue(result["response_metadata"]["generated_phase_simulator"])
        self.assertEqual(result["response_metadata"]["simulation_render_mode"], "image_aware_vector_canvas_reconstruction")
        self.assertTrue(result["response_metadata"]["source_image_found"])
        self.assertTrue(result["response_metadata"]["source_image_inference_used"])
        self.assertFalse(result["response_metadata"]["source_image_used"])
        self.assertEqual(result["html_path"], str(result_path))
        self.assertEqual(result["artifact_messages"][0].name, "result.html")
        self.assertEqual(result["artifact_messages"][0].url, "/api/runs/run-phase-html-1/artifacts/result.html")
        self.assertEqual(result["artifact_messages"][0].metadata["source"], "phase_diagram_followup_interactive_simulator")
        self.assertTrue(result["artifact_messages"][0].metadata["source_image_found"])
        self.assertTrue(result["artifact_messages"][0].metadata["source_image_inference_used"])
        self.assertFalse(result["artifact_messages"][0].metadata["source_image_used"])
        self.assertEqual(result["response_summary"]["followup_action"], "generate_phase_result_interactive_simulator")
        self.assertTrue(result["response_summary"]["source_image_found"])
        self.assertTrue(result["response_summary"]["source_image_inference_used"])
        self.assertFalse(result["response_summary"]["source_image_used"])
        self.assertEqual(result["termination_reason"], "conversation_answered_with_html_artifact")

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
            def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1, capability: str = ""):  # type: ignore[override]
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
            def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1, capability: str = ""):  # type: ignore[override]
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
            def chat_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.1, capability: str = ""):  # type: ignore[override]
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

    def test_thermo_rag_vector_layer_exposes_vector_score(self) -> None:
        candidates = search_thermo_cards("alzn phase boundary database", top_k=3)

        self.assertGreaterEqual(len(candidates), 1)
        top = candidates[0]
        self.assertEqual(top.card.system_name, "Al-Zn")
        self.assertGreater(top.bm25_score, 0.0)
        self.assertGreater(top.vector_score, 0.0)
        self.assertEqual(top.embedding_backend, build_thermo_card_index()[0].embedding_backend)

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
