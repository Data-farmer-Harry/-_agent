from __future__ import annotations

import unittest

from app.config import settings
from app.core.artifacts import ArtifactService
from app.state import TaskRoute
from app.tools import ToolExecutor, ToolRouter, build_default_tool_registry
from app.tools.models import ToolCall
from tests.support import build_request


class GenericToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_tool_registry()
        self.router = ToolRouter(self.registry)
        self.executor = ToolExecutor(self.registry)
        self.artifact_service = ArtifactService(root_dir=settings.tmp_dir)
        self.run_id = "tooltest"

    def _state(self, message: str) -> dict:
        request = build_request(message, conversation_id="tool-tests")
        return {
            "run_id": self.run_id,
            "conversation_id": request.conversation_id,
            "request": request,
            "route": TaskRoute(name="conversation.answer", compute_domain="none"),
            "compute_domain": "none",
            "uploaded_assets": [],
            "last_run_context": request.last_run_context,
            "tool_results": [],
            "artifact_messages": [],
            "trace": [],
            "plan_steps": [],
        }

    def test_router_keeps_plain_explanation_tool_free(self) -> None:
        decision = self.router.decide(self._state("解释一下 NVT 和 NPT 的区别"))
        self.assertFalse(decision.need_tool)
        self.assertEqual(decision.selected_calls, [])

        phase_state = self._state("请生成 Al-Zn 相图，温度范围 300K-1000K")
        phase_state["route"] = TaskRoute(name="phase_diagram.generate", compute_domain="phase_diagram")
        phase_state["compute_domain"] = "phase_diagram"
        phase_state["phase_diagram_result"] = object()
        phase_decision = self.router.decide(phase_state)
        self.assertFalse(phase_decision.need_tool)

    def test_router_selects_physics_and_literature_tools_only_when_explicit(self) -> None:
        physics_decision = self.router.decide(self._state("帮我检查 Cu 800K 4000 steps timestep 1 fs 合理吗"))
        self.assertTrue(physics_decision.need_tool)
        self.assertEqual([call.tool_name for call in physics_decision.selected_calls], ["physics.check"])

        literature_decision = self.router.decide(self._state("帮我查 3 篇 Cu EAM potential 相关论文"))
        self.assertTrue(literature_decision.need_tool)
        self.assertEqual([call.tool_name for call in literature_decision.selected_calls], ["literature.search"])
        self.assertEqual(literature_decision.selected_calls[0].arguments["top_k"], 3)

        workspace_decision = self.router.decide(self._state("帮我在项目里搜索文件 generic_tools"))
        self.assertTrue(workspace_decision.need_tool)
        self.assertEqual([call.tool_name for call in workspace_decision.selected_calls], ["workspace.search"])

        data_decision = self.router.decide(self._state("帮我分析这个 thermo log 的数据概况"))
        self.assertTrue(data_decision.need_tool)
        self.assertEqual([call.tool_name for call in data_decision.selected_calls], ["data.profile"])

    def test_file_read_and_physics_check_execute_with_traceable_payloads(self) -> None:
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        sample_path = settings.tmp_dir / "tool_test_lammps.log"
        sample_path.write_text("Step Temp Press\n0 300 1\n100 310 1.1\n", encoding="utf-8")
        try:
            context = self.executor.build_context(self._state("读取 backend/outputs/tool_test_lammps.log"), self.artifact_service)
            file_result = self.executor.execute(
                ToolCall(tool_name="file.read", arguments={"path": str(sample_path), "max_chars": 2000}),
                context,
            )
            self.assertTrue(file_result.success)
            self.assertIn("preview", file_result.output)
            self.assertEqual(file_result.output["line_count"], 3)

            physics_result = self.executor.execute(
                ToolCall(tool_name="physics.check", arguments={"text": "Cu 800K 4000 steps timestep 1 fs"}),
                context,
            )
            self.assertTrue(physics_result.success)
            self.assertIn("total_simulation_time_ps", physics_result.output["conversions"])

            data_result = self.executor.execute(
                ToolCall(tool_name="data.profile", arguments={"path": str(sample_path), "format": "lammps_log"}),
                context,
            )
            self.assertTrue(data_result.success)
            self.assertEqual(data_result.output["profile"]["type"], "lammps_thermo")
            self.assertIn("Temp", data_result.output["profile"]["numeric_columns"])

            search_result = self.executor.execute(
                ToolCall(tool_name="workspace.search", arguments={"query": "tool_test_lammps", "path": str(settings.tmp_dir)}),
                context,
            )
            self.assertTrue(search_result.success)
            self.assertGreaterEqual(search_result.output["match_count"], 1)
        finally:
            sample_path.unlink(missing_ok=True)

    def test_structure_convert_and_report_generate_write_artifacts(self) -> None:
        state = self._state("把 xyz 转成 lammps data 并生成报告")
        context = self.executor.build_context(state, self.artifact_service)
        xyz = "2\nCu dimer\nCu 0 0 0\nCu 1 0 0\n"
        structure_result = self.executor.execute(
            ToolCall(tool_name="structure.convert", arguments={"text": xyz, "source_format": "xyz", "target_format": "lammps_data"}),
            context,
        )
        self.assertTrue(structure_result.success)
        self.assertTrue(structure_result.artifacts)
        self.assertTrue((settings.tmp_dir / "runs" / self.run_id / "converted_structure.data").exists())

        state["tool_results"] = [structure_result.model_dump()]
        report_context = self.executor.build_context(state, self.artifact_service)
        report_result = self.executor.execute(
            ToolCall(tool_name="report.generate", arguments={"title": "工具测试报告"}),
            report_context,
        )
        self.assertTrue(report_result.success)
        self.assertTrue((settings.tmp_dir / "runs" / self.run_id / "matterpilot_report.md").exists())


if __name__ == "__main__":
    unittest.main()
