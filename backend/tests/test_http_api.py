from __future__ import annotations

import uuid
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import api as api_module
from app.config import settings
from app.lammps.config import LammpsConfig, load_lammps_config, update_runtime_lammps_config
from app.state import LastRunContext, UploadedAsset
from tests.support import MINI_PNG_DATA_URL, ScriptedLLMClient, build_request


def _patch_api_llm_clients(stack: ExitStack) -> ScriptedLLMClient:
    scripted_llm = ScriptedLLMClient()
    stack.enter_context(patch.object(api_module.supervisor_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.recognition_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.chat_agent, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.phase_diagram_runtime.codegen_service, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.phase_diagram_runtime.phase_agent_service, "llm_client", scripted_llm))
    stack.enter_context(patch.object(api_module.lammps_runtime, "llm_client", scripted_llm))
    stack.enter_context(
        patch.object(
            api_module.lammps_runtime,
            "config_loader",
            lambda: LammpsConfig(
                force_mock=True,
                allow_mock_fallback=True,
                lammps_command="",
                potentials_dir="",
                max_retries=1,
            ),
        )
    )
    return scripted_llm


class HttpApiTests(unittest.TestCase):
    def test_thermo_registry_endpoint_returns_seeded_entries(self) -> None:
        with TestClient(api_module.app) as client:
            response = client.get("/api/thermo/registry")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["count"], 5)
        systems = {item["system_name"] for item in payload["systems"]}
        self.assertTrue({"Al-Zn", "Al-Mg", "Al-Ni", "Pb-Sn", "Al-Fe"}.issubset(systems))

    def test_system_diagnostics_endpoint_returns_runtime_checks(self) -> None:
        with TestClient(api_module.app) as client:
            response = client.get("/api/system/diagnostics")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["overall_status"], {"ok", "warning", "error"})
        self.assertGreaterEqual(len(payload["checks"]), 5)
        self.assertIn("LLM / Multimodal", [item["name"] for item in payload["checks"]])

    def test_thermo_rag_search_endpoint_returns_ranked_candidates(self) -> None:
        with TestClient(api_module.app) as client:
            response = client.post(
                "/api/thermo/rag/search",
                json={"query": "我想计算铝锌二元相图并查看液相线", "top_k": 3},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["query"], "我想计算铝锌二元相图并查看液相线")
        self.assertTrue(payload["matched"])
        self.assertIn(payload["embedding_backend"], {"local_hash", "llm_api", "openai_compatible"})
        self.assertGreaterEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["system_name"], "Al-Zn")
        self.assertIn("vector_score", payload["candidates"][0])
        self.assertTrue(payload["recommended_embedding_model"])

    def test_plain_chat_request_stays_in_chat_mode(self) -> None:
        request = build_request("什么是共析点，它和包晶反应有什么区别？", conversation_id=f"chat-{uuid.uuid4().hex[:8]}")

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "conversation.answer")
        self.assertIn("共析", payload["final_message"])

    def test_phase_diagram_request_runs_real_pycalphad_pipeline(self) -> None:
        request = build_request(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。",
            conversation_id=f"gen-{uuid.uuid4().hex[:8]}",
            system_name="Al-Zn",
            temperature_min=300.0,
            temperature_max=1000.0,
        )

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["route"]["name"], "phase_diagram.generate")
        self.assertTrue(payload["html_content"])
        self.assertEqual(payload["termination_reason"], "review_passed")
        self.assertTrue(payload["metadata"]["review"]["passed"])
        self.assertTrue(payload["metadata"]["accuracy"]["passed"])
        self.assertIn(payload["metadata"]["generation_source"], {"llm_codegen_calculated_wrapper", "llm_codegen_calculated_wrapper_repaired"})
        self.assertIn("runtime_final_message", payload["metadata"])
        self.assertIn("chat_final_message", payload["metadata"])
        self.assertEqual(payload["summary"]["result_profile"]["category"], "Calculated")
        self.assertEqual(payload["summary"]["result_profile"]["source_label"], "pycalphad + TDB")

        result_response = TestClient(api_module.app).get(f"/api/runs/{payload['run_id']}/result")
        self.assertEqual(result_response.status_code, 200)
        self.assertIn("pycalphad_tdb_database", result_response.text)

    def test_registry_miss_fails_honestly(self) -> None:
        request = build_request(
            "请生成一张 Xx-Yy 二元相图，温度范围 300K-1800K。",
            conversation_id=f"miss-{uuid.uuid4().hex[:8]}",
            system_name="Xx-Yy",
            temperature_min=300.0,
            temperature_max=1800.0,
        )

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["termination_reason"], "thermo_database_not_found")
        self.assertEqual(payload["metadata"]["thermo_lookup"]["lookup_mode"], "exact_then_rag")

    def test_follow_up_request_uses_last_run_context_from_memory(self) -> None:
        conversation_id = f"follow-{uuid.uuid4().hex[:8]}"
        generate_request = build_request(
            "请生成一张 Al-Mg 二元相图，温度范围 300K-1000K，突出主要相区。",
            conversation_id=conversation_id,
            system_name="Al-Mg",
            temperature_min=300.0,
            temperature_max=1000.0,
        )
        follow_up_request = build_request("你刚刚生成了什么代码？", conversation_id=conversation_id)

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                first_response = client.post("/api/agent/chat", json=generate_request.model_dump(mode="json"))
                second_response = client.post("/api/agent/chat", json=follow_up_request.model_dump(mode="json"))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        payload = second_response.json()
        self.assertEqual(payload["route"]["name"], "conversation.answer")
        self.assertIn("build_calculated_phase_diagram_report", payload["final_message"])
        self.assertIn("Al-Mg", payload["final_message"])

    def test_phase_html_follow_up_reconstructs_generated_html_inline(self) -> None:
        conversation_id = f"follow-html-{uuid.uuid4().hex[:8]}"
        generate_request = build_request(
            "请生成一张 Al-Co 二元相图，温度范围 300K-1900K，突出 FCC_A1、BCC_B2、AL3CO 和液相区。",
            conversation_id=conversation_id,
            system_name="Al-Co",
            temperature_min=300.0,
            temperature_max=1900.0,
        )
        follow_up_request = build_request("帮我生成交互式html", conversation_id=conversation_id)

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                first_response = client.post("/api/agent/chat", json=generate_request.model_dump(mode="json"))
                second_response = client.post("/api/agent/chat", json=follow_up_request.model_dump(mode="json"))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        payload = second_response.json()
        self.assertEqual(payload["route"]["name"], "conversation.answer")
        self.assertTrue(payload["html_content"])
        self.assertIn('id="recognition-simulator-root"', payload["html_content"])
        self.assertIn("recognition-reconstruction-canvas", payload["html_content"])
        self.assertIn("generated_canvas_vector_reconstruction", payload["html_content"])
        self.assertIn("structured_path_reconstruction", payload["html_content"])
        self.assertIn("reconstruction_scene", payload["html_content"])
        self.assertNotIn("pixels_rgba_b64", payload["html_content"])
        self.assertNotIn("phase-source-image", payload["html_content"])
        self.assertNotIn("data:image/", payload["html_content"])
        self.assertEqual(payload["artifacts"][0]["name"], "result.html")
        self.assertEqual(payload["metadata"]["origin_route_name"], "phase_diagram.generate")
        self.assertTrue(payload["metadata"]["generated_phase_simulator"])
        self.assertEqual(payload["metadata"]["simulation_render_mode"], "image_aware_vector_canvas_reconstruction")
        self.assertTrue(payload["metadata"]["source_image_found"])
        self.assertTrue(payload["metadata"]["source_image_inference_used"])
        self.assertFalse(payload["metadata"]["source_image_used"])
        self.assertEqual(payload["summary"]["followup_action"], "generate_phase_result_interactive_simulator")
        self.assertTrue(payload["summary"]["source_image_found"])
        self.assertTrue(payload["summary"]["source_image_inference_used"])
        self.assertFalse(payload["summary"]["source_image_used"])
        self.assertEqual(payload["termination_reason"], "conversation_answered_with_html_artifact")

    def test_phase_html_follow_up_recovers_real_phase_run_after_chat_context_pollution(self) -> None:
        conversation_id = f"follow-html-recover-{uuid.uuid4().hex[:8]}"
        generate_request = build_request(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，并解释主要相区。",
            conversation_id=conversation_id,
            system_name="Al-Zn",
            temperature_min=300.0,
            temperature_max=1000.0,
        )
        explain_request = build_request("你刚刚生成了什么代码？", conversation_id=conversation_id)
        follow_up_request = build_request("帮我生成交互式html", conversation_id=conversation_id)

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                first_response = client.post("/api/agent/chat", json=generate_request.model_dump(mode="json"))
                self.assertEqual(first_response.status_code, 200)
                second_response = client.post("/api/agent/chat", json=explain_request.model_dump(mode="json"))
                self.assertEqual(second_response.status_code, 200)
                explain_payload = second_response.json()
                follow_up_request.last_run_context = LastRunContext(
                    run_id=explain_payload["run_id"],
                    route_name="conversation.answer",
                    compute_domain="none",
                    system_name="",
                    final_message=explain_payload["final_message"],
                    generated_code_preview="",
                    review_summary="",
                    selected_tool="chat",
                    generation_source="",
                    request_summary="",
                    review_passed=None,
                    review_issues=[],
                    review_advisory_issues=[],
                    trace_summary=[],
                    recognition_summary="",
                    artifact_names=[],
                )
                third_response = client.post("/api/agent/chat", json=follow_up_request.model_dump(mode="json"))

        self.assertEqual(third_response.status_code, 200)
        payload = third_response.json()
        self.assertEqual(payload["route"]["name"], "conversation.answer")
        self.assertTrue(payload["html_content"])
        self.assertIn('id="recognition-simulator-root"', payload["html_content"])
        self.assertIn("recognition-reconstruction-canvas", payload["html_content"])
        self.assertIn("generated_canvas_vector_reconstruction", payload["html_content"])
        self.assertIn("reconstruction_scene", payload["html_content"])
        self.assertNotIn("pixels_rgba_b64", payload["html_content"])
        self.assertNotIn("phase-source-image", payload["html_content"])
        self.assertTrue(payload["metadata"]["generated_phase_simulator"])
        self.assertEqual(payload["metadata"]["origin_route_name"], "phase_diagram.generate")
        self.assertTrue(payload["metadata"]["source_image_found"])
        self.assertTrue(payload["metadata"]["source_image_inference_used"])
        self.assertFalse(payload["metadata"]["source_image_used"])
        self.assertEqual(payload["summary"]["followup_action"], "generate_phase_result_interactive_simulator")
        self.assertTrue(payload["summary"]["source_image_found"])
        self.assertTrue(payload["summary"]["source_image_inference_used"])
        self.assertFalse(payload["summary"]["source_image_used"])
        self.assertEqual(payload["termination_reason"], "conversation_answered_with_html_artifact")

    def test_conversation_snapshot_endpoint_returns_memory_and_latest_run(self) -> None:
        conversation_id = f"conv-snapshot-{uuid.uuid4().hex[:8]}"
        request = build_request(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。",
            conversation_id=conversation_id,
            system_name="Al-Zn",
            temperature_min=300.0,
            temperature_max=1000.0,
        )

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                snapshot_response = client.get(f"/api/conversations/{conversation_id}")

        self.assertEqual(snapshot_response.status_code, 200)
        snapshot = snapshot_response.json()
        self.assertEqual(snapshot["conversation_id"], conversation_id)
        self.assertTrue(snapshot["short_term"]["messages"])
        self.assertEqual(snapshot["short_term"]["last_run_context"]["run_id"], payload["run_id"])
        self.assertEqual(snapshot["latest_run"]["run_id"], payload["run_id"])
        self.assertEqual(snapshot["latest_run"]["route"]["name"], "phase_diagram.generate")

    def test_prompt_suggestion_endpoint_returns_contextual_llm_prompt(self) -> None:
        conversation_id = f"suggest-{uuid.uuid4().hex[:8]}"
        generate_request = build_request(
            "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。",
            conversation_id=conversation_id,
            system_name="Al-Zn",
            temperature_min=300.0,
            temperature_max=1000.0,
        )

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                first_response = client.post("/api/agent/chat", json=generate_request.model_dump(mode="json"))
                self.assertEqual(first_response.status_code, 200)

                suggestion_response = client.post(
                    "/api/agent/prompt-suggestion",
                    json={
                        "conversation_id": conversation_id,
                        "draft_message": "",
                        "conversation_history": [
                            {
                                "role": "user",
                                "content": "请帮我进一步分析刚刚的结果。",
                            }
                        ],
                        "last_run_context": {
                            "run_id": first_response.json()["run_id"],
                            "route_name": "phase_diagram.generate",
                            "compute_domain": "phase_diagram",
                            "system_name": "Al-Zn",
                            "final_message": first_response.json()["final_message"],
                            "generated_code_preview": "build_calculated_phase_diagram_report(...)",
                            "review_summary": "",
                            "selected_tool": "phase_diagram_codegen",
                            "generation_source": "llm_codegen_calculated_wrapper",
                            "request_summary": "Al-Zn binary phase diagram",
                            "review_passed": True,
                            "review_issues": [],
                            "review_advisory_issues": [],
                            "trace_summary": ["phase_diagram_codegen: generated wrapper"],
                            "recognition_summary": "",
                            "artifact_names": ["result.html"],
                        },
                        "current_context_summary": "LastRun: Al-Zn binary phase diagram",
                    },
                )

        self.assertEqual(suggestion_response.status_code, 200)
        payload = suggestion_response.json()
        self.assertEqual(payload["source"], "llm_prompt_suggester")
        self.assertTrue(payload["suggested_prompt"])
        self.assertIn("相图", payload["suggested_prompt"])

    def test_recognition_mvp_flow_reaches_recognition_agent(self) -> None:
        request = build_request("请识别这张相图截图，并提取体系和坐标轴。", conversation_id=f"rec-{uuid.uuid4().hex[:8]}")
        request.uploaded_assets = [
            UploadedAsset(
                asset_id="img-1",
                name="diagram.png",
                media_type="image/png",
                data_url=MINI_PNG_DATA_URL,
                size_bytes=128,
            )
        ]

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "recognition.analyze")
        self.assertIsNotNone(payload["recognition_result"])
        self.assertEqual(payload["recognition_result"]["source"], "llm_recognition_agent")
        self.assertFalse(payload["html_content"])
        self.assertFalse(payload["artifacts"])
        self.assertNotIn("result_profile", payload["summary"])
        self.assertNotIn("simulation_render_mode", payload["summary"])
        self.assertNotIn("simulation_render_mode", payload["metadata"])

    def test_recognition_html_request_returns_reconstruction_panel(self) -> None:
        request = build_request("请识别这张相图截图，并在当前窗口生成交互式html。", conversation_id=f"rec-html-{uuid.uuid4().hex[:8]}")
        request.uploaded_assets = [
            UploadedAsset(
                asset_id="img-1",
                name="diagram.png",
                media_type="image/png",
                data_url=MINI_PNG_DATA_URL,
                size_bytes=128,
            )
        ]

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "recognition.analyze")
        self.assertIsNotNone(payload["recognition_result"])
        self.assertTrue(payload["html_content"])
        self.assertIn("recognition-reconstruction-canvas", payload["html_content"])
        self.assertIn("generated_canvas_vector_reconstruction", payload["html_content"])
        self.assertIn("reconstruction_scene", payload["html_content"])
        self.assertNotIn("pixels_rgba_b64", payload["html_content"])
        self.assertNotIn("recognition-generated-svg", payload["html_content"])
        self.assertNotIn("phase-source-image", payload["html_content"])
        self.assertNotIn("data:image/png;base64", payload["html_content"])
        self.assertEqual(payload["summary"]["result_profile"]["category"], "Recognized Simulation")
        self.assertGreater(payload["summary"]["overlay_confidence"], 0.3)
        self.assertEqual(payload["summary"]["simulation_render_mode"], "generated_canvas_vector_reconstruction")
        self.assertTrue(payload["summary"]["source_image_present"])
        self.assertEqual(payload["metadata"]["simulation_render_mode"], "generated_canvas_vector_reconstruction")
        self.assertTrue(payload["metadata"]["source_image_present"])
        artifact_names = {artifact["name"] for artifact in payload["artifacts"]}
        self.assertIn("result.html", artifact_names)
        self.assertIn("recognition_simulator.json", artifact_names)

    def test_lammps_request_runs_through_compute_agent_and_returns_artifacts(self) -> None:
        request = build_request(
            "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，并返回热力学图和轨迹结果。",
            conversation_id=f"lammps-{uuid.uuid4().hex[:8]}",
        )

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "lammps.generate")
        self.assertEqual(payload["route"]["compute_domain"], "lammps")
        self.assertIn(payload["run_status"], {"completed", "failed"})
        self.assertIn("lammps_request_interpreter", [step["tool_name"] for step in payload["plan_steps"]])
        self.assertIn("lammps_input_codegen", [step["tool_name"] for step in payload["plan_steps"]])
        self.assertIn("lammps_result_review", [step["tool_name"] for step in payload["plan_steps"]])
        self.assertIn(payload["metadata"]["run_mode"], {"real", "mock", "draft"})
        self.assertTrue(any(item["name"] == "report.md" for item in payload["artifacts"]))
        self.assertIn(payload["summary"]["result_profile"]["category"], {"LAMMPS Simulated", "LAMMPS Fallback"})

    def test_lammps_registry_endpoint_is_available(self) -> None:
        with TestClient(api_module.app) as client:
            response = client.get("/api/lammps/registry")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("materials", payload)
        self.assertIn("tasks", payload)
        self.assertIn("potentials", payload)

    def test_lammps_config_endpoints_round_trip_runtime_overrides(self) -> None:
        original = load_lammps_config()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "llm_config.json"
            try:
                with patch("app.config.DEFAULT_JSON_FILE", config_path), TestClient(api_module.app) as client:
                    get_response = client.get("/api/config/lammps")
                    self.assertEqual(get_response.status_code, 200)

                    update_response = client.post(
                        "/api/config/lammps",
                        json={
                            "force_mock": True,
                            "allow_mock_fallback": True,
                            "max_retries": 3,
                        },
                    )
                    self.assertEqual(update_response.status_code, 200)
                    payload = update_response.json()

                    confirm_response = client.get("/api/config/lammps")
            finally:
                with patch("app.config.DEFAULT_JSON_FILE", config_path):
                    update_runtime_lammps_config(
                        {
                            "force_mock": original.force_mock,
                            "allow_mock_fallback": original.allow_mock_fallback,
                            "max_retries": original.max_retries,
                            "lammps_command": original.lammps_command,
                            "potentials_dir": original.potentials_dir,
                            "ovito_location": original.ovito_location,
                        }
                    )

        self.assertTrue(payload["updated"])
        self.assertTrue(payload["force_mock"])
        self.assertTrue(payload["allow_mock_fallback"])
        self.assertEqual(payload["max_retries"], 3)
        self.assertEqual(confirm_response.status_code, 200)
        confirm_payload = confirm_response.json()
        self.assertTrue(confirm_payload["force_mock"])
        self.assertEqual(confirm_payload["max_retries"], 3)

    def test_llm_config_endpoints_expose_runtime_settings(self) -> None:
        original_model = settings.llm_model
        original_timeout = settings.llm_request_timeout_seconds
        original_require_llm = settings.require_llm_for_agents
        original_thinking = settings.llm_enable_thinking
        original_supports_chat = settings.llm_supports_chat
        original_supports_vision = settings.llm_supports_vision
        original_supports_embedding = settings.llm_supports_embedding
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "llm_config.json"
            try:
                with patch("app.config.DEFAULT_JSON_FILE", config_path), TestClient(api_module.app) as client:
                    get_response = client.get("/api/config/llm")
                    self.assertEqual(get_response.status_code, 200)

                    update_response = client.post(
                        "/api/config/llm",
                        json={
                            "llm_model": "qwen3.5-plus",
                            "llm_enable_thinking": False,
                            "llm_request_timeout_seconds": 90,
                            "require_llm_for_agents": True,
                            "llm_supports_chat": True,
                            "llm_supports_vision": True,
                            "llm_supports_embedding": False,
                        },
                    )
                    self.assertEqual(update_response.status_code, 200)
                    payload = update_response.json()
            finally:
                settings.llm_model = original_model
                settings.llm_request_timeout_seconds = original_timeout
                settings.require_llm_for_agents = original_require_llm
                settings.llm_enable_thinking = original_thinking
                settings.llm_supports_chat = original_supports_chat
                settings.llm_supports_vision = original_supports_vision
                settings.llm_supports_embedding = original_supports_embedding

        self.assertTrue(payload["updated"])
        self.assertEqual(payload["llm_model"], "qwen3.5-plus")
        self.assertFalse(payload["llm_enable_thinking"])
        self.assertEqual(payload["llm_request_timeout_seconds"], 90)
        self.assertTrue(payload["require_llm_for_agents"])
        self.assertTrue(payload["llm_supports_chat"])
        self.assertTrue(payload["llm_supports_vision"])
        self.assertFalse(payload["llm_supports_embedding"])

    def test_lammps_run_endpoints_expose_history_summary_and_artifacts(self) -> None:
        conversation_id = f"lammps-artifacts-{uuid.uuid4().hex[:8]}"
        request = build_request(
            "请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，并返回热力学图和轨迹结果。",
            conversation_id=conversation_id,
        )

        with ExitStack() as stack:
            _patch_api_llm_clients(stack)
            with TestClient(api_module.app) as client:
                response = client.post("/api/agent/chat", json=request.model_dump(mode="json"))
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                run_id = payload["run_id"]

                runs_response = client.get("/api/runs")
                compact_runs_response = client.get("/api/runs?limit=1&compact=true")
                summary_response = client.get(f"/api/runs/{run_id}")
                artifact_response = client.get(f"/api/runs/{run_id}/artifacts/report.md")
                cancel_response = client.post(f"/api/runs/{run_id}/cancel")

        self.assertEqual(payload["route"]["name"], "lammps.generate")
        self.assertEqual(runs_response.status_code, 200)
        runs_payload = runs_response.json()
        self.assertGreaterEqual(runs_payload["count"], 1)
        self.assertTrue(any(item["run_id"] == run_id for item in runs_payload["runs"]))
        self.assertEqual(compact_runs_response.status_code, 200)
        compact_runs_payload = compact_runs_response.json()
        self.assertEqual(compact_runs_payload["count"], 1)
        self.assertEqual(compact_runs_payload["runs"][0]["artifacts"], [])
        self.assertEqual(compact_runs_payload["runs"][0]["trace"], [])

        self.assertEqual(summary_response.status_code, 200)
        summary_payload = summary_response.json()
        self.assertEqual(summary_payload["run_id"], run_id)
        self.assertEqual(summary_payload["route"]["name"], "lammps.generate")
        self.assertIn(summary_payload["status"], {"completed", "failed"})
        self.assertTrue(any(item["name"] == "report.md" for item in summary_payload["artifacts"]))

        self.assertEqual(artifact_response.status_code, 200)
        self.assertIn("# MD Agent Run Report", artifact_response.text)

        self.assertEqual(cancel_response.status_code, 200)
        cancel_payload = cancel_response.json()
        self.assertEqual(cancel_payload["run_id"], run_id)
        self.assertEqual(cancel_payload["status"], "cancelled_requested")
