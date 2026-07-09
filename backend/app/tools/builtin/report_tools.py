from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.tools.models import ToolContext, ToolResult, ToolRisk, ToolSpec
from app.tools.registry import ToolRegistry


def _safe_section(title: str, content: str) -> str:
    content = content.strip() or "暂无。"
    return f"## {title}\n\n{content}\n"


def _tool_results_markdown(tool_results: list[dict[str, Any]]) -> str:
    if not tool_results:
        return "本报告生成前没有额外工具结果。"
    sections: list[str] = []
    for result in tool_results:
        tool_name = result.get("tool_name", "")
        summary = result.get("summary", "")
        success = result.get("success", False)
        output = result.get("output", {})
        sections.append(
            "\n".join(
                [
                    f"### {tool_name}",
                    "",
                    f"状态：{'成功' if success else '失败'}",
                    "",
                    f"摘要：{summary}",
                    "",
                    "关键输出：",
                    "",
                    "```json",
                    _compact_json(output),
                    "```",
                ]
            )
        )
    return "\n\n".join(sections)


def _compact_json(value: Any, *, limit: int = 5000) -> str:
    import json

    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n..."
    return text


def _report_generate(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    state = context.state
    title = str(arguments.get("title") or "MatterPilot 任务报告").strip()
    last_run = context.last_run_context
    tool_results = list(state.get("tool_results", []))
    trace = state.get("trace", [])
    artifacts = state.get("artifact_messages", [])
    compute_response = state.get("phase_diagram_result") or state.get("lammps_result")
    generated_at = datetime.now(timezone.utc).isoformat()

    request_section = "\n".join(
        [
            f"用户请求：{context.request_message}",
            f"conversation_id：{context.conversation_id}",
            f"run_id：{context.run_id}",
            f"生成时间：{generated_at}",
        ]
    )

    last_run_section = "暂无上一轮可复用结果。"
    if last_run and getattr(last_run, "run_id", ""):
        last_run_section = "\n".join(
            [
                f"上一轮 run_id：{last_run.run_id}",
                f"路由：{last_run.route_name}",
                f"计算域：{last_run.compute_domain}",
                f"摘要：{last_run.request_summary or last_run.final_message[:500]}",
                f"review：{last_run.review_summary or '暂无'}",
                f"artifact：{', '.join(last_run.artifact_names) if last_run.artifact_names else '暂无'}",
            ]
        )

    compute_section = "本轮没有新的计算 runtime 结果。"
    if compute_response is not None:
        compute_section = "\n".join(
            [
                f"success：{compute_response.success}",
                f"termination_reason：{compute_response.termination_reason}",
                f"route：{compute_response.route.name}",
                f"final_message：{compute_response.final_message[:1200]}",
                "",
                "summary：",
                "",
                "```json",
                _compact_json(compute_response.summary),
                "```",
            ]
        )

    trace_lines = []
    for item in trace[-20:]:
        trace_lines.append(f"{getattr(item, 'step_index', '?')}. {getattr(item, 'tool_name', '')} — {getattr(item, 'summary', '')}")
    trace_section = "\n".join(trace_lines) if trace_lines else "暂无 trace。"

    artifact_lines = []
    for artifact in artifacts:
        artifact_lines.append(f"- {artifact.name} ({artifact.kind}) {artifact.path or artifact.url or ''}".strip())
    artifact_section = "\n".join(artifact_lines) if artifact_lines else "暂无 artifact。"

    body = "\n\n".join(
        [
            f"# {title}",
            _safe_section("请求信息", request_section),
            _safe_section("上一轮上下文", last_run_section),
            _safe_section("本轮计算结果", compute_section),
            _safe_section("工具结果", _tool_results_markdown(tool_results)),
            _safe_section("执行 Trace", trace_section),
            _safe_section("Artifacts", artifact_section),
            _safe_section("注意事项", "本报告由本地工具根据当前 run 的 trace、artifact 和上下文自动生成；未在 trace 中出现的外部检索或计算不应被视为已执行。"),
        ]
    )

    artifact_name = "matterpilot_report.md"
    output_path = context.artifact_service.get_artifact_path(context.run_id, artifact_name)
    output_path.write_text(body, encoding="utf-8")
    artifact = context.artifact_service.build_artifact_ref(
        kind="markdown",
        name=artifact_name,
        path=output_path,
        url=context.artifact_service.build_artifact_url(context.run_id, artifact_name),
        content=body[:2000],
        metadata={"title": title, "generated_at": generated_at},
    )
    return ToolResult(
        tool_name="report.generate",
        success=True,
        summary=f"已生成 Markdown 报告：{artifact_name}。",
        output={"artifact": artifact.model_dump(mode="json"), "preview": body[:4000]},
        artifacts=[artifact],
    )


def register_report_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="report.generate",
            description="Generate a Markdown report artifact for the current run using request context, previous run context, tool results, trace, artifacts, and compute summary.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                },
            },
            risk=ToolRisk.WRITE_ARTIFACT,
            read_only=False,
            output_kind="artifact",
        ),
        _report_generate,
    )
