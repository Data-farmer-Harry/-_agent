from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.graph import AgentAppGraph
from app.agents.chat import ChatAgent
from app.agents.compute import ComputeAgent
from app.lammps.config import LammpsConfig
from app.memory import MemoryStore
from app.runtimes.lammps import LammpsRuntime
from app.runtimes.phase_diagram import PhaseDiagramRuntime
from app.agents.recognition import RecognitionAgent
from app.state import AgentChatRequest, AgentRunResponse, HealthResponse, ThermoRegistryEntry, ThermoRegistryResponse
from app.agents.supervisor import SupervisorAgent
from app.core.artifacts import ArtifactService
from app.core.executor import LocalPythonExecutor
from app.thermo.service import PhaseDiagramAgentService
from app.thermo.registry import get_thermo_database_card, load_thermo_database_cards
from tests.support import ScriptedLLMClient


class StubCodeGenerationService:
    def generate_code_with_source(self, request):  # noqa: ANN001
        card = get_thermo_database_card(request.system_name)
        if card is None:
            raise RuntimeError(f"No thermodynamic database is registered for {request.system_name}.")
        phase_tokens = " ".join(card.phases[:6])
        html = f"""<!DOCTYPE html>
<html>
  <head>
    <meta name="phase-diagram-agent-layout" content="v1" />
    <title>{request.system_name} phase diagram</title>
  </head>
  <body>
    <main id="phase-diagram-agent-result">
      <h1>{request.system_name} phase diagram</h1>
      <p>source=pycalphad_tdb_database</p>
      <p>mode=tdb_equilibrium_calculation</p>
      <p>database={card.database_name}</p>
      <p>phases={phase_tokens}</p>
      <p>temperature_range={request.temperature_min}-{request.temperature_max} K</p>
    </main>
  </body>
</html>
"""
        code = (
            "from pathlib import Path\n\n"
            "# from app.thermo.engine import build_calculated_phase_diagram_report\n"
            "HELPER_NAME = 'build_calculated_phase_diagram_report'\n"
            f"html = {html!r}\n"
            "Path('result.html').write_text(html, encoding='utf-8')\n"
            f"print('generated {request.system_name}')\n"
        )
        return code, "stub_llm_codegen"

    def sanitize_and_validate_code(self, request, code):  # noqa: ANN001
        _ = request
        return code, []

    def repair_code(self, request, generated_code, stderr):  # noqa: ANN001
        _ = request, generated_code, stderr
        return None


class TestPhaseDiagramRuntime(PhaseDiagramRuntime):
    @staticmethod
    def _build_accuracy_payload(request, system_name):  # noqa: ANN001
        _ = request, system_name
        return {"available": False, "passed": False}


def build_test_app(root_dir: Path) -> FastAPI:
    artifact_service = ArtifactService(root_dir=root_dir)
    memory_store = MemoryStore(root_dir=root_dir)
    scripted_llm = ScriptedLLMClient()
    codegen_service = StubCodeGenerationService()
    phase_service = PhaseDiagramAgentService(codegen_service=codegen_service, llm_client=scripted_llm)
    runtime = TestPhaseDiagramRuntime(
        artifact_service=artifact_service,
        codegen_service=codegen_service,
        executor=LocalPythonExecutor(artifact_service=artifact_service, python_executable=sys.executable),
        phase_agent_service=phase_service,
    )
    lammps_runtime = LammpsRuntime(
        artifact_service=artifact_service,
        llm_client=scripted_llm,
        config_loader=lambda: LammpsConfig(force_mock=True, allow_mock_fallback=True, lammps_command="", potentials_dir="", max_retries=1),
    )
    graph = AgentAppGraph(
        artifact_service=artifact_service,
        memory_store=memory_store,
        supervisor=SupervisorAgent(llm_client=scripted_llm),
        recognition_agent=RecognitionAgent(llm_client=scripted_llm),
        compute_agent=ComputeAgent(phase_diagram_runtime=runtime, lammps_runtime=lammps_runtime),
        chat_agent=ChatAgent(llm_client=scripted_llm),
    )

    app = FastAPI()

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", app_name="backend-test", version="test")

    @app.get("/api/thermo/registry", response_model=ThermoRegistryResponse)
    def thermo_registry() -> ThermoRegistryResponse:
        cards = load_thermo_database_cards()
        return ThermoRegistryResponse(
            count=len(cards),
            systems=[ThermoRegistryEntry(**card.public_payload()) for card in cards],
        )

    @app.post("/api/agent/chat", response_model=AgentRunResponse)
    def agent_chat(request: AgentChatRequest) -> AgentRunResponse:
        return graph.run_chat(request)

    return app


class BackendAppContractTests(unittest.TestCase):
    def test_thermo_registry_endpoint_exposes_registered_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                response = client.get("/api/thermo/registry")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["count"], 29)
        systems = {item["system_name"] for item in payload["systems"]}
        self.assertIn("Al-Zn", systems)
        self.assertIn("Cu-Ni", systems)
        self.assertIn("Nb-Re", systems)
        self.assertIn("Cr-Fe", systems)
        self.assertIn("Fe-Nb", systems)
        self.assertIn("Cr-Nb", systems)
        self.assertIn("Cr-Ti", systems)
        self.assertIn("Cr-V", systems)
        self.assertIn("Ti-V", systems)
        self.assertIn("Fe-Co", systems)
        self.assertIn("Co-Cr", systems)
        self.assertIn("Nb-Ti", systems)
        self.assertIn("Al-Cr", systems)
        self.assertIn("Cr-Ni", systems)
        self.assertIn("Al-Pt", systems)
        self.assertIn("Ni-Pt", systems)
        self.assertIn("Fe-Ni", systems)
        self.assertIn("Co-Ni", systems)
        self.assertIn("Al-Co", systems)
        self.assertIn("Pd-Ru", systems)
        self.assertIn("Pd-Tc", systems)
        self.assertIn("Pd-Mo", systems)
        self.assertIn("Ru-Tc", systems)
        self.assertIn("Ru-Mo", systems)
        self.assertIn("Tc-Mo", systems)
        first = payload["systems"][0]
        self.assertIn("database_name", first)
        self.assertIn("components", first)
        self.assertIn("phases", first)

    def test_ordinary_chat_route_stays_in_conversation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="chat-only",
                        message="什么是包晶反应，它和共析反应有什么区别？",
                    ).model_dump(mode="json"),
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "conversation.answer")
        self.assertTrue(payload["success"])
        self.assertFalse(payload["html_content"])
        self.assertTrue("包晶" in payload["final_message"] or "共析" in payload["final_message"])

    def test_phase_diagram_generate_route_runs_local_python_and_returns_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="generate-success",
                        message="请生成一张 Al-Zn 二元相图，温度范围 300K-1800K。",
                    ).model_dump(mode="json"),
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "phase_diagram.generate")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["termination_reason"], "review_passed")
        self.assertIn("phase-diagram-agent-result", payload["html_content"])
        self.assertIn("pycalphad_tdb_database", payload["html_content"])
        self.assertIn("phase_diagram_codegen", [step["tool_name"] for step in payload["plan_steps"]])
        self.assertIn("python_execute", [step["tool_name"] for step in payload["plan_steps"]])
        self.assertIn("phase_diagram_result_review", [step["tool_name"] for step in payload["plan_steps"]])
        self.assertEqual(payload["metadata"]["review"]["passed"], True)
        code_artifact = next(item for item in payload["artifacts"] if item["kind"] == "code")
        self.assertEqual(code_artifact["name"], "generated_code_attempt_1.py")
        self.assertTrue(code_artifact["path"].endswith("generated_code_attempt_1.py"))

    def test_unsupported_registry_miss_returns_failure_without_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="generate-miss",
                        message="请生成一张 Ti-Al 二元相图，温度范围 300K-2200K。",
                    ).model_dump(mode="json"),
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "phase_diagram.generate")
        self.assertFalse(payload["success"])
        self.assertEqual(payload["termination_reason"], "thermo_database_not_found")
        self.assertIsNone(payload["html_content"])
        self.assertIn("还没有这个体系对应的 TDB 文件", payload["final_message"])
        thermo_step = next(step for step in payload["trace"] if step["tool_name"] == "thermo_database_lookup")
        self.assertFalse(thermo_step["success"])

    def test_follow_up_reuses_last_run_context_from_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                first = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="follow-up-memory",
                        message="请生成一张 Al-Zn 二元相图，温度范围 300K-1800K。",
                    ).model_dump(mode="json"),
                )
                second = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="follow-up-memory",
                        message="这张图准确吗？",
                    ).model_dump(mode="json"),
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_payload = first.json()
        second_payload = second.json()
        self.assertEqual(first_payload["route"]["name"], "phase_diagram.generate")
        self.assertEqual(second_payload["route"]["name"], "conversation.answer")
        self.assertTrue(second_payload["success"])
        self.assertIn("这轮结果不是手画示意图", second_payload["final_message"])
        self.assertIn("review 摘要", second_payload["final_message"])

    def test_recognition_mvp_flow_uses_recognition_agent_and_returns_structured_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="recognition-mvp",
                        message="请帮我识别这张截图里有什么信息。",
                        uploaded_assets=[
                            {
                                "asset_id": "asset-1",
                                "name": "phase-diagram.png",
                                "media_type": "image/png",
                                "data_url": "data:image/png;base64,ZmFrZQ==",
                                "size_bytes": 4,
                            }
                        ],
                    ).model_dump(mode="json"),
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "recognition.analyze")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["recognition_result"]["source"], "llm_recognition_agent")
        self.assertIn("RecognitionAgent 已完成这张图的第一轮结构化识别", payload["final_message"])
        self.assertIn("RecognitionAgent", [step["tool_name"] for step in payload["plan_steps"]])

    def test_lammps_generate_route_runs_compute_runtime_and_returns_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = build_test_app(Path(tmp_dir))
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent/chat",
                    json=AgentChatRequest(
                        conversation_id="lammps-contract",
                        message="请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps。",
                    ).model_dump(mode="json"),
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["route"]["name"], "lammps.generate")
        self.assertEqual(payload["route"]["compute_domain"], "lammps")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["termination_reason"], "review_passed")
        self.assertEqual(payload["metadata"]["run_mode"], "mock")
        artifact_names = {item["name"] for item in payload["artifacts"]}
        self.assertIn("report.md", artifact_names)
        self.assertIn("plot.png", artifact_names)
        self.assertIn("thermo.csv", artifact_names)


if __name__ == "__main__":
    unittest.main()
