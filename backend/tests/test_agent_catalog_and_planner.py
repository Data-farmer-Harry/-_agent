from __future__ import annotations

import unittest

from app.schemas import AgentRunRequest, TaskRoute
from app.services.agent_catalog import AgentCatalogService, LAMMPS_RESERVED_TOOLS
from app.services.planner_service import PlannerService
from app.services.task_router import TaskRouter
from app.services.tool_registry import ToolRegistry

from tests.support import sample_image_request, sample_request


class AgentCatalogAndPlannerTests(unittest.TestCase):
    def test_catalog_marks_lammps_reserved_when_stub_tool_is_absent(self) -> None:
        catalog = AgentCatalogService().build_catalog(ToolRegistry())
        workspaces = {workspace.id: workspace for workspace in catalog.workspaces}
        tools = {tool.name: tool for tool in catalog.tools}

        self.assertEqual(workspaces["lammps"].status, "reserved")
        self.assertEqual(workspaces["lammps"].available_tools, [])
        self.assertEqual(workspaces["lammps"].reserved_tools, list(LAMMPS_RESERVED_TOOLS))
        for tool_name in LAMMPS_RESERVED_TOOLS:
            self.assertEqual(tools[tool_name].status, "reserved")
            self.assertEqual(tools[tool_name].workspace_id, "lammps")

    def test_catalog_infers_workspace_from_structured_payloads_and_keywords(self) -> None:
        service = AgentCatalogService()

        self.assertEqual(
            service.infer_workspace_id(AgentRunRequest(user_input="run from payload", diagram_request=sample_request())),
            "phase_diagram",
        )
        self.assertEqual(
            service.infer_workspace_id(
                AgentRunRequest(user_input="uploaded screenshot", image_diagram_request=sample_image_request())
            ),
            "phase_diagram",
        )
        self.assertEqual(
            service.infer_workspace_id(AgentRunRequest(user_input="Please prepare a LAMMPS molecular dynamics workflow")),
            "lammps",
        )
        self.assertEqual(service.infer_workspace_id(AgentRunRequest(user_input="tell me a joke")), "generic")

    def test_task_router_lammps_route_exposes_reserved_tool_slots(self) -> None:
        route = TaskRouter(catalog_service=AgentCatalogService()).route(
            AgentRunRequest(user_input="Run a LAMMPS atomistic simulation", task_type_hint="lammps.generate")
        )

        self.assertEqual(route.name, "lammps.generate")
        self.assertEqual(route.workspace_id, "lammps")
        self.assertEqual(route.selected_tool, "lammps_command_router")
        self.assertEqual(route.reserved_tools, list(LAMMPS_RESERVED_TOOLS))

    def test_task_router_respects_explicit_phase_from_image_hint(self) -> None:
        route = TaskRouter(catalog_service=AgentCatalogService()).route(
            AgentRunRequest(user_input="route by explicit hint", task_type_hint="phase_diagram.from_image")
        )

        self.assertEqual(route.name, "phase_diagram.from_image")
        self.assertEqual(route.workspace_id, "phase_diagram")
        self.assertEqual(route.selected_tool, "phase_diagram_image_parse")
        self.assertIn("explicit phase-diagram task_type_hint", route.reason)

    def test_planner_repair_route_matches_generation_plan_shape(self) -> None:
        planner = PlannerService()
        generate_plan = planner.build_plan(TaskRoute(name="phase_diagram.generate", workspace_id="phase_diagram", reason="generate"))
        repair_plan = planner.build_plan(TaskRoute(name="phase_diagram.repair", workspace_id="phase_diagram", reason="repair"))

        self.assertEqual([step.tool_name for step in repair_plan], [step.tool_name for step in generate_plan])
        self.assertTrue(repair_plan[3].retryable)
        self.assertTrue(repair_plan[6].input["force_placeholder"])


if __name__ == "__main__":
    unittest.main()
