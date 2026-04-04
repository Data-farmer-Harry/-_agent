from __future__ import annotations

import http.client
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config.llm_config import LLMConfig
from src.config.supervisor_config import SupervisorConfig
from src.graphs.agent_workflow import AgentWorkflow
from src.Multi_agents.md_agent import MDAgent
from src.reasoning.llm_adapter import LLMAdapter
from src.schemas.state import AgentState
from src.tools.generate_lammps_in import generate_lammps_input, get_lammps_form_schema
from src.tools.ovito_diffusion import generate_diffusion_trajectory_if_applicable, _spawn_captured_process


class WorkflowTests(unittest.TestCase):
    def test_chat_requests_missing_fields(self) -> None:
        workflow = AgentWorkflow()
        state = workflow.handle_chat("Please run a simulation for copper.")
        self.assertTrue(state.missing_fields)
        self.assertEqual(state.route, "conversation")

    def test_chat_completes_minimal_request(self) -> None:
        workflow = AgentWorkflow()
        state = workflow.handle_chat("Run an EAM equilibration for Al at 500K for 5000 steps.")
        self.assertFalse(state.missing_fields)
        self.assertEqual(state.route, "md_run")
        self.assertTrue(state.validation["is_reasonable"])

    def test_chat_answers_general_help_without_requesting_parameters(self) -> None:
        workflow = AgentWorkflow()
        state = workflow.handle_chat("什么是LAMMPS的dump文件？")
        self.assertEqual(state.intent, "general_help")
        self.assertFalse(state.missing_fields)
        self.assertEqual(state.route, "conversation")
        self.assertIn("dump", state.messages[-1]["content"].lower())

    def test_chat_blocks_unreasonable_steps(self) -> None:
        workflow = AgentWorkflow()
        state = workflow.handle_chat("Run an EAM equilibration for Al at 500K for 500099 steps.")
        self.assertEqual(state.route, "conversation")
        self.assertFalse(state.validation["is_reasonable"])
        self.assertTrue(any("上限" in error for error in state.validation["errors"]))

    def test_md_agent_falls_back_to_mock(self) -> None:
        config = SupervisorConfig(allow_mock_fallback=True, lammps_command="", potentials_dir="")
        agent = MDAgent(config=config)
        state = AgentState(
            user_query="Run an EAM equilibration for Al at 500K for 5000 steps.",
            normalized_request={
                "material": "Al",
                "potential_family": "eam",
                "task_type": "equilibration",
                "temperature": 500,
                "steps": 5000,
            },
            mode="real",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            result = agent.run(state, Path(tmpdir))
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.mode, "mock")
            self.assertIn("plot.png", result.artifacts)
            self.assertTrue((Path(tmpdir) / "summary.json").exists())

    def test_md_agent_records_diffusion_animation_artifact(self) -> None:
        config = SupervisorConfig(allow_mock_fallback=True, lammps_command="", potentials_dir="")
        agent = MDAgent(config=config)
        state = AgentState(
            user_query="请帮我做一个铜材料的升温模拟，温度 900 K，步数 4000，用 EAM 势。",
            normalized_request={
                "material": "Cu",
                "potential_family": "eam",
                "task_type": "heating",
                "temperature": 900,
                "steps": 4000,
            },
            mode="real",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            image_path = output_dir / "diffusion_trajectory.png"
            gif_path = output_dir / "diffusion_trajectory_3d.gif"
            video_path = output_dir / "ovito.mp4"
            metadata_path = output_dir / "diffusion_metadata.json"
            image_path.write_bytes(b"png")
            gif_path.write_bytes(b"gif")
            video_path.write_bytes(b"mp4")
            metadata_path.write_text("{}", encoding="utf-8")
            with patch(
                "src.Multi_agents.md_agent.generate_diffusion_trajectory_if_applicable",
                return_value={
                    "supported_task": True,
                    "generated": True,
                    "reason": "generated",
                    "backend": "python module",
                    "image_path": str(image_path),
                    "animation_path": str(gif_path),
                    "video_path": str(video_path),
                    "metadata_path": str(metadata_path),
                },
            ):
                result = agent.run(state, output_dir)
            self.assertIn("diffusion_trajectory.png", result.artifacts)
            self.assertIn("diffusion_trajectory_3d.gif", result.artifacts)
            self.assertIn("ovito.mp4", result.artifacts)
            self.assertIn("diffusion_metadata.json", result.artifacts)

    def test_cu_heating_dump_uses_unwrapped_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_lammps_input(
                {
                    "material": "Cu",
                    "potential_family": "eam",
                    "task_type": "heating",
                    "temperature": 900,
                    "steps": 12000,
                },
                Path(tmpdir),
                potentials_dir="/tmp/potentials",
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn("dump 1 all custom", content)
            self.assertIn("xu yu zu", content)
            self.assertIn("dump_modify 1 sort id", content)

    def test_generate_lammps_input_uses_custom_structure_and_potential(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            potential_path = output_dir / "Cu_custom.eam.alloy"
            structure_path = output_dir / "initial_structure.data"
            potential_path.write_text("eam", encoding="utf-8")
            structure_path.write_text("LAMMPS data file\n\n0 atoms\n", encoding="utf-8")
            path = generate_lammps_input(
                {
                    "material": "Cu",
                    "potential_family": "eam",
                    "task_type": "equilibration",
                    "temperature": 500,
                    "steps": 5000,
                    "custom_potential_path": str(potential_path),
                    "custom_structure_path": str(structure_path),
                    "custom_structure_format": "read_data",
                },
                output_dir,
                potentials_dir="/tmp/potentials",
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn(f"read_data {structure_path.as_posix()}", content)
            self.assertNotIn("create_atoms 1 box", content)
            self.assertIn(f"* * {potential_path.as_posix()} Cu", content)

    def test_form_schema_exposes_agentsmd_style_sections(self) -> None:
        schema = get_lammps_form_schema()
        self.assertEqual(schema["type"], "layered_form")
        self.assertTrue(schema["sections"])
        self.assertEqual(schema["sections"][0]["fields"][0]["key"], "material")

    def test_diffusion_render_skips_without_ovito(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "dump.atom").write_text(
                "ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n2\nITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\nITEM: ATOMS id type xu yu zu x y z\n1 1 1 1 1 1 1 1\n2 1 2 2 2 2 2 2\n",
                encoding="utf-8",
            )
            with patch(
                "src.tools.ovito_diffusion.detect_ovito_backend",
                return_value={"ovito_available": False, "ovito_backend": "not found"},
            ):
                status = generate_diffusion_trajectory_if_applicable(
                    output_dir,
                    {"material": "Cu", "task_type": "heating"},
                    mode="real",
                )
            self.assertTrue(status["supported_task"])
            self.assertFalse(status["generated"])
            self.assertIn("OVITO", status["reason"])

    def test_ovito_process_spawn_uses_stdout_and_stderr_pipes(self) -> None:
        captured = {}

        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = 0

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return FakeProcess()

        with patch("src.tools.ovito_diffusion.subprocess.Popen", side_effect=fake_popen):
            proc = _spawn_captured_process(["echo", "hello"])

        self.assertIsInstance(proc, FakeProcess)
        self.assertNotIn("capture_output", captured["kwargs"])
        self.assertIs(captured["kwargs"]["stdout"], subprocess.PIPE)
        self.assertIs(captured["kwargs"]["stderr"], subprocess.PIPE)
        self.assertTrue(captured["kwargs"]["text"])

    def test_openai_adapter_falls_back_on_remote_disconnect(self) -> None:
        adapter = LLMAdapter(
            LLMConfig(
                provider="openai_compatible",
                base_url="http://example.test/v1",
                model="demo-model",
                api_key="sk-test",
                timeout_seconds=5,
            )
        )

        with patch(
            "src.reasoning.llm_adapter.urllib.request.urlopen",
            side_effect=http.client.RemoteDisconnected("Remote end closed connection without response"),
        ):
            result = adapter.generate("system", "讲一下这篇论文")

        self.assertIsInstance(result.content, str)
        self.assertIn("讲一下这篇论文", result.content)


if __name__ == "__main__":
    unittest.main()
