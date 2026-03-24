from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas import DeliverableKind, TaskRouteName, ToolName, WorkspaceId, WorkspaceStatus


@dataclass(frozen=True)
class PlanStepTemplate:
    tool_name: ToolName
    description: str
    stage: str = ""
    retryable: bool = False
    input_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDefinition:
    name: TaskRouteName
    workspace_id: WorkspaceId
    entry_tool: str | None
    description: str
    default_reason: str
    failure_strategy: str = ""
    sample_prompts: tuple[str, ...] = ()
    available_tools: tuple[str, ...] = ()
    reserved_tools: tuple[str, ...] = ()
    input_channels: tuple[str, ...] = ("generic",)
    deliverable: DeliverableKind = "none"
    step_templates: tuple[PlanStepTemplate, ...] = ()


@dataclass(frozen=True)
class WorkspaceDefinition:
    id: WorkspaceId
    title: str
    description: str
    status: WorkspaceStatus
    default_route: TaskRouteName | None = None
    supported_routes: tuple[TaskRouteName, ...] = ()
    reserved_tools: tuple[str, ...] = ()


PHASE_DIAGRAM_ACTIVE_TOOL_NAMES: tuple[str, ...] = (
    "phase_diagram_result_review",
    "phase_diagram_codegen",
    "phase_diagram_html_redraw",
    "phase_diagram_html_review",
    "phase_diagram_image_parse",
    "phase_diagram_image_render",
    "phase_diagram_repair",
    "python_execute",
    "load_latest_html_artifact",
)

LAMMPS_ACTIVE_TOOL_NAMES: tuple[str, ...] = ("lammps_command_router",)
LAMMPS_RESERVED_TOOLS: tuple[str, ...] = ("lammps_codegen", "lammps_execute", "lammps_repair")


WORKSPACE_DEFINITIONS: dict[WorkspaceId, WorkspaceDefinition] = {
    "phase_diagram": WorkspaceDefinition(
        id="phase_diagram",
        title="Phase Diagram Workspace",
        description="Production workspace for agent-directed Python generation, screenshot recognition, explanation redraw, repair, and HTML delivery.",
        status="active",
        default_route="phase_diagram.generate",
        supported_routes=(
            "phase_diagram.generate",
            "phase_diagram.recognize",
            "phase_diagram.redraw_html",
            "phase_diagram.from_image",
            "phase_diagram.repair",
        ),
    ),
    "lammps": WorkspaceDefinition(
        id="lammps",
        title="LAMMPS Workspace",
        description="Extensible simulation workspace with an executable router stub and reserved slots for generation, execution, and repair.",
        status="active",
        default_route="lammps.generate",
        supported_routes=("lammps.generate", "lammps.repair"),
        reserved_tools=LAMMPS_RESERVED_TOOLS,
    ),
    "generic": WorkspaceDefinition(
        id="generic",
        title="Generic Workspace",
        description="Fallback workspace for commands that do not yet map onto a supported agent tool-chain.",
        status="disabled",
        default_route="generic.unknown",
        supported_routes=("materials.lookup", "materials.compare", "materials.analysis", "generic.unknown"),
    ),
}


PHASE_DIAGRAM_EXECUTION_PLAN: tuple[PlanStepTemplate, ...] = (
    PlanStepTemplate(
        tool_name="phase_diagram_codegen",
        stage="prepare",
        description="Generate Python code for the requested phase diagram.",
    ),
    PlanStepTemplate(
        tool_name="python_execute",
        stage="execute",
        description="Execute the generated Python code.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_result_review",
        stage="review",
        description="Review the generated code and HTML artifact before accepting the run.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_repair",
        stage="repair",
        retryable=True,
        description="Repair generated code if execution fails.",
    ),
    PlanStepTemplate(
        tool_name="python_execute",
        stage="execute",
        retryable=True,
        description="Re-execute repaired Python code.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_result_review",
        stage="review",
        retryable=True,
        description="Review the repaired artifact before accepting the run.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_codegen",
        stage="fallback",
        retryable=True,
        input_overrides={"force_placeholder": True},
        description="Fallback to deterministic placeholder code if repair still fails.",
    ),
    PlanStepTemplate(
        tool_name="python_execute",
        stage="execute",
        retryable=True,
        description="Execute deterministic placeholder code.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_result_review",
        stage="review",
        retryable=True,
        description="Review the deterministic fallback artifact before finishing the run.",
    ),
)

PHASE_DIAGRAM_IMAGE_PLAN: tuple[PlanStepTemplate, ...] = (
    PlanStepTemplate(
        tool_name="phase_diagram_image_parse",
        stage="recognize",
        description="Recognize the uploaded screenshot into a conservative structured spec.",
    ),
)

PHASE_DIAGRAM_IMAGE_RENDER_PLAN: tuple[PlanStepTemplate, ...] = (
    PlanStepTemplate(
        tool_name="phase_diagram_image_parse",
        stage="parse",
        description="Parse the uploaded screenshot into a calibrated structured spec.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_image_render",
        stage="render",
        description="Render a deterministic HTML page from the parsed image spec.",
    ),
)

PHASE_DIAGRAM_HTML_REDRAW_PLAN: tuple[PlanStepTemplate, ...] = (
    PlanStepTemplate(
        tool_name="phase_diagram_html_redraw",
        stage="redraw",
        description="Generate an explanation-first HTML page from the provided phase-diagram context.",
    ),
    PlanStepTemplate(
        tool_name="phase_diagram_html_review",
        stage="review",
        description="Review the generated HTML redraw before accepting the artifact.",
    ),
)

LAMMPS_STUB_PLAN: tuple[PlanStepTemplate, ...] = (
    PlanStepTemplate(
        tool_name="lammps_command_router",
        stage="route",
        description="Interpret the LAMMPS-oriented request and return the current tool-chain outline.",
    ),
)


ROUTE_DEFINITIONS: dict[TaskRouteName, RouteDefinition] = {
    "phase_diagram.generate": RouteDefinition(
        name="phase_diagram.generate",
        workspace_id="phase_diagram",
        entry_tool="phase_diagram_codegen",
        description="Generate a phase-diagram page by having the LLM write Python code, then execute local Python and review the artifact.",
        default_reason="The agent selected the Python generation path for a phase-diagram request.",
        failure_strategy="Try code generation, execute it, repair once if needed, and finally fall back to deterministic placeholder HTML.",
        sample_prompts=(
            "Generate a binary Fe-Cu phase diagram from 300 K to 1800 K.",
            "Create an Al-Si phase diagram page with clear terminal regions and annotations.",
        ),
        available_tools=PHASE_DIAGRAM_ACTIVE_TOOL_NAMES,
        input_channels=("text", "structured"),
        deliverable="html",
        step_templates=PHASE_DIAGRAM_EXECUTION_PLAN,
    ),
    "phase_diagram.recognize": RouteDefinition(
        name="phase_diagram.recognize",
        workspace_id="phase_diagram",
        entry_tool="phase_diagram_image_parse",
        description="Recognize an uploaded phase-diagram image with multimodal analysis and return a conservative structured interpretation.",
        default_reason="The agent selected the multimodal recognition path for an uploaded phase-diagram image.",
        failure_strategy="If vision extraction is unavailable or uncertain, fall back to a conservative manual-calibrated interpretation without inventing missing details.",
        sample_prompts=(
            "Read this Fe-C screenshot and summarize the visible title, labels, and boundaries conservatively.",
            "Recognize the uploaded phase diagram without inventing unreadable phase regions.",
        ),
        available_tools=PHASE_DIAGRAM_ACTIVE_TOOL_NAMES,
        input_channels=("image", "structured"),
        deliverable="json",
        step_templates=PHASE_DIAGRAM_IMAGE_PLAN,
    ),
    "phase_diagram.repair": RouteDefinition(
        name="phase_diagram.repair",
        workspace_id="phase_diagram",
        entry_tool="phase_diagram_repair",
        description="Repair a phase-diagram generation run by combining prior code and execution feedback.",
        default_reason="Detected a phase-diagram repair request and routed it into the repair-capable execution chain.",
        failure_strategy="Reuse the same guarded execution chain so repair can still fall back to deterministic placeholder output.",
        sample_prompts=(
            "Repair the last Fe-Cu run using the execution stderr and regenerate a stable page.",
        ),
        available_tools=PHASE_DIAGRAM_ACTIVE_TOOL_NAMES,
        input_channels=("text", "structured"),
        deliverable="html",
        step_templates=PHASE_DIAGRAM_EXECUTION_PLAN,
    ),
    "phase_diagram.from_image": RouteDefinition(
        name="phase_diagram.from_image",
        workspace_id="phase_diagram",
        entry_tool="phase_diagram_image_parse",
        description="Legacy alias for screenshot recognition and calibrated HTML reconstruction.",
        default_reason="Using the legacy from-image path for screenshot recognition.",
        failure_strategy="If vision extraction is unavailable or uncertain, fall back to manual calibrated reconstruction using user-provided axes.",
        sample_prompts=(
            "Upload a Fe-C screenshot, keep the temperature axis, and rebuild it as an interactive page.",
            "Calibrate this binary phase diagram image with explicit X and Y axis limits.",
        ),
        available_tools=PHASE_DIAGRAM_ACTIVE_TOOL_NAMES,
        input_channels=("image", "structured"),
        deliverable="html",
        step_templates=PHASE_DIAGRAM_IMAGE_RENDER_PLAN,
    ),
    "phase_diagram.redraw_html": RouteDefinition(
        name="phase_diagram.redraw_html",
        workspace_id="phase_diagram",
        entry_tool="phase_diagram_html_redraw",
        description="Generate a scientist-facing HTML page that redraws or explains a phase diagram from text or image context.",
        default_reason="The agent selected the HTML redraw path for an explanation-first phase-diagram request.",
        failure_strategy="Generate HTML directly, normalize the page contract, and review the result before accepting it.",
        sample_prompts=(
            "Turn these phase-diagram lecture notes into an HTML explanation page with a clean redraw panel.",
            "Create an explanation-first HTML page for this uploaded phase-diagram figure and discussion notes.",
        ),
        available_tools=PHASE_DIAGRAM_ACTIVE_TOOL_NAMES,
        input_channels=("text", "image", "structured"),
        deliverable="html",
        step_templates=PHASE_DIAGRAM_HTML_REDRAW_PLAN,
    ),
    "lammps.generate": RouteDefinition(
        name="lammps.generate",
        workspace_id="lammps",
        entry_tool="lammps_command_router",
        description="Accept a LAMMPS-oriented request and outline the future multi-tool simulation flow.",
        default_reason="Detected a LAMMPS request and routed it to the command-router stub while simulation tools remain reserved.",
        failure_strategy="Keep the request in the stub router and emit the future tool-chain outline until execution tools are implemented.",
        sample_prompts=(
            "Prepare a LAMMPS molecular dynamics workflow for Cu diffusion.",
            "Outline the next tools needed for a LAMMPS minimization job.",
        ),
        available_tools=LAMMPS_ACTIVE_TOOL_NAMES,
        reserved_tools=LAMMPS_RESERVED_TOOLS,
        input_channels=("text", "structured"),
        deliverable="text",
        step_templates=LAMMPS_STUB_PLAN,
    ),
    "lammps.repair": RouteDefinition(
        name="lammps.repair",
        workspace_id="lammps",
        entry_tool="lammps_command_router",
        description="Collect a LAMMPS failure context and outline the future debug/repair chain.",
        default_reason="Detected a LAMMPS repair-style request and routed it to the command-router stub while execution tools remain reserved.",
        failure_strategy="Capture repair intent in the stub router and return the future debug/repair sequence without running a simulation.",
        sample_prompts=(
            "Debug this LAMMPS input error and tell me the next tool stages you would call.",
        ),
        available_tools=LAMMPS_ACTIVE_TOOL_NAMES,
        reserved_tools=LAMMPS_RESERVED_TOOLS,
        input_channels=("text", "structured"),
        deliverable="text",
        step_templates=LAMMPS_STUB_PLAN,
    ),
    "materials.lookup": RouteDefinition(
        name="materials.lookup",
        workspace_id="generic",
        entry_tool=None,
        description="Reserved route for future materials lookup workflows.",
        default_reason="Using explicit task_type_hint from the request.",
    ),
    "materials.compare": RouteDefinition(
        name="materials.compare",
        workspace_id="generic",
        entry_tool=None,
        description="Reserved route for future materials comparison workflows.",
        default_reason="Using explicit task_type_hint from the request.",
    ),
    "materials.analysis": RouteDefinition(
        name="materials.analysis",
        workspace_id="generic",
        entry_tool=None,
        description="Reserved route for future materials analysis workflows.",
        default_reason="Using explicit task_type_hint from the request.",
    ),
    "generic.unknown": RouteDefinition(
        name="generic.unknown",
        workspace_id="generic",
        entry_tool=None,
        description="Fallback route when no supported workspace or tool can be inferred.",
        default_reason="No supported workspace or tool could be inferred from the command.",
        failure_strategy="Return a graceful unsupported-route response without executing any tool.",
    ),
}


def get_route_definition(route_name: TaskRouteName) -> RouteDefinition:
    return ROUTE_DEFINITIONS[route_name]


def get_workspace_definition(workspace_id: WorkspaceId) -> WorkspaceDefinition:
    return WORKSPACE_DEFINITIONS[workspace_id]


def list_route_names() -> list[TaskRouteName]:
    return list(ROUTE_DEFINITIONS.keys())


def list_route_definitions() -> list[RouteDefinition]:
    return list(ROUTE_DEFINITIONS.values())


def list_workspace_definitions() -> list[WorkspaceDefinition]:
    return list(WORKSPACE_DEFINITIONS.values())
