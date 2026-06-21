from __future__ import annotations

import html as html_lib
import json

from app.recognition_reconstruction.canvas_vectorize import build_canvas_vector_scene
from app.recognition_reconstruction.schema import ReconstructionGeometry, ReconstructionSchema
from app.state import ResultProfile


def geometry_fallback(schema: ReconstructionSchema) -> float:
    assert schema.x_axis.minimum is not None and schema.x_axis.maximum is not None
    return (schema.x_axis.minimum + schema.x_axis.maximum) / 2


def _format_axis_value(value: float) -> str:
    return f"{value:.3g}"


def _phase_list_html(schema: ReconstructionSchema) -> str:
    return "".join(f"<li>{html_lib.escape(phase)}</li>" for phase in schema.phases)


def _point_list_html(schema: ReconstructionSchema) -> str:
    return "".join(
        "<li><strong>{label}</strong> · x={composition}, T={temperature} {unit}</li>".format(
            label=html_lib.escape(point.label or "critical point"),
            composition=_format_axis_value(
                point.composition if point.composition is not None else geometry_fallback(schema),
            ),
            temperature=_format_axis_value(
                point.temperature if point.temperature is not None else schema.controls.temperature_default,
            ),
            unit=html_lib.escape(schema.y_axis.unit or "K"),
        )
        for point in schema.critical_points
    )


def _render_sidebar(schema: ReconstructionSchema, result_profile: ResultProfile, *, extra_note: str = "") -> str:
    warning_html = "".join(f"<li>{html_lib.escape(warning)}</li>" for warning in schema.warnings)
    note_html = "".join(f"<li>{html_lib.escape(note)}</li>" for note in schema.notes)
    evidence_html = "".join(f"<li>{html_lib.escape(str(item))}</li>" for item in result_profile.evidence)
    extra = f'<p class="note">{html_lib.escape(extra_note)}</p>' if extra_note else ""
    confidence_percent = int((result_profile.confidence or schema.confidence or 0) * 100)
    return f"""
      <aside class="panel sidebar">
        <div class="meta-card">
          <h2>Overview</h2>
          <h3 class="sidebar-title">{html_lib.escape(schema.system)} Reconstruction</h3>
          <p>{html_lib.escape(schema.raw_summary or "This panel rebuilds the recognized phase-diagram layout into deterministic HTML.")}</p>
          <div class="tag-row">
            <span class="tag">{html_lib.escape(result_profile.category)}</span>
            <span class="tag">{html_lib.escape(result_profile.mode_label)}</span>
            <span class="tag">confidence {confidence_percent}%</span>
          </div>
        </div>
        <div class="meta-card">
          <h2>Trust</h2>
          <p>{html_lib.escape(result_profile.trust_statement)}</p>
          {extra}
        </div>
        <div class="meta-card">
          <h2>Recognized Context</h2>
          <dl class="kv">
            <dt>System</dt><dd>{html_lib.escape(schema.system)}</dd>
            <dt>Diagram</dt><dd>{html_lib.escape(schema.diagram_type)}</dd>
            <dt>X axis</dt><dd>{html_lib.escape(schema.x_axis.label)} · {_format_axis_value(schema.x_axis.minimum or 0)}-{_format_axis_value(schema.x_axis.maximum or 0)} {html_lib.escape(schema.x_axis.unit or "")}</dd>
            <dt>Y axis</dt><dd>{html_lib.escape(schema.y_axis.label)} · {_format_axis_value(schema.y_axis.minimum or 0)}-{_format_axis_value(schema.y_axis.maximum or 0)} {html_lib.escape(schema.y_axis.unit or "")}</dd>
            <dt>Plot box</dt><dd>{schema.plot_region.left:.2f}, {schema.plot_region.top:.2f}, {schema.plot_region.right:.2f}, {schema.plot_region.bottom:.2f}</dd>
            <dt>Bounds src</dt><dd>{html_lib.escape(schema.plot_region.source or "validator_default")}</dd>
            <dt>Confidence</dt><dd>{int(schema.overlay_confidence * 100)}%</dd>
          </dl>
        </div>
        <div class="meta-card">
          <h2>Phases</h2>
          <ul>{_phase_list_html(schema)}</ul>
        </div>
        <div class="meta-card">
          <h2>Critical Points</h2>
          <ul>{_point_list_html(schema)}</ul>
        </div>
        <div class="meta-card">
          <h2>Warnings</h2>
          <ul>{warning_html}</ul>
        </div>
        <div class="meta-card">
          <h2>Usage Notes</h2>
          <ul>{note_html}</ul>
        </div>
        <div class="meta-card">
          <h2>Evidence</h2>
          <ul>{evidence_html}</ul>
        </div>
      </aside>
    """


def _canvas_dimensions(geometry: ReconstructionGeometry, scene: dict[str, object] | None) -> tuple[int, int]:
    if scene is not None:
        width = int(scene.get("width") or 0)
        height = int(scene.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    return int(geometry.svg_width), int(geometry.svg_height)


def _render_canvas_reconstruction_html(
    schema: ReconstructionSchema,
    geometry: ReconstructionGeometry,
    result_profile: ResultProfile,
    *,
    source_image_data_url: str | None,
) -> str:
    canvas_scene = build_canvas_vector_scene(source_image_data_url) if source_image_data_url else None
    if canvas_scene is not None:
        render_mode = "generated_canvas_vector_reconstruction"
        render_priority_mode = "structured_path_reconstruction"
        banner = (
            "Structured-path mode: the uploaded figure is decomposed into quantized color regions and merged canvas "
            "primitives. This page does not embed the source image, does not use an image tag, and does not render an SVG layer."
        )
        extra_note = (
            "The uploaded figure is reconstructed into HTML/canvas from vectorized contour layers. "
            "No source image tag, data URL, SVG layer, or pixel buffer is embedded in the final page."
        )
    else:
        render_mode = "generated_canvas_schema_reconstruction"
        render_priority_mode = "deterministic_canvas_projection"
        banner = (
            "Deterministic canvas mode: no source bitmap is available for vector extraction, so the chart is rebuilt "
            "from the validated schema, axes, phases, and critical points. The output is still HTML/canvas, not SVG."
        )
        extra_note = (
            "No final SVG fallback is used. When image vectorization is unavailable, the renderer draws a deterministic "
            "canvas model from the recognized schema."
        )

    canvas_width, canvas_height = _canvas_dimensions(geometry, canvas_scene)
    payload = {
        "schema": schema.model_dump(mode="json"),
        "geometry": geometry.model_dump(mode="json"),
        "render_mode": render_mode,
        "render_priority_mode": render_priority_mode,
        "source_image_present": bool(source_image_data_url),
        "reconstruction_scene": canvas_scene,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    sidebar = _render_sidebar(schema, result_profile, extra_note=extra_note)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html_lib.escape(schema.system)} · HTML Canvas Reconstruction</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d7dfed;
      --ink: #18263d;
      --muted: #62748f;
      --accent: #2f63ff;
      --accent-soft: rgba(47, 99, 255, 0.12);
      --shadow: 0 18px 48px rgba(24, 38, 61, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "SF Pro Text", "PingFang SC", "Noto Sans SC", sans-serif;
      background: linear-gradient(180deg, #fbfdff 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    #recognition-simulator-root {{
      min-height: 100vh;
      padding: 24px;
    }}
    .shell {{
      max-width: 1520px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(0, 1.85fr) 360px;
      gap: 20px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
    }}
    .chart-panel {{ padding: 20px; }}
    .chart-shell {{
      border-radius: 22px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, #fdfefe 0%, #f7f9fc 100%);
      overflow: hidden;
    }}
    .chart-header {{
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .chart-header strong {{ font-size: 18px; }}
    .chart-header span {{ font-size: 13px; color: var(--muted); }}
    .chart-stage {{ padding: 18px; }}
    .fidelity-banner {{
      margin: 0 18px 18px;
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(24, 38, 61, 0.08);
      background: rgba(24, 38, 61, 0.04);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }}
    .figure-stack {{
      position: relative;
      width: 100%;
      overflow: hidden;
      border-radius: 18px;
      border: 1px solid rgba(215, 223, 237, 0.8);
      background: #ffffff;
      aspect-ratio: {canvas_width} / {canvas_height};
      min-height: 360px;
      display: grid;
      place-items: center;
    }}
    #recognition-reconstruction-canvas {{
      display: block;
      width: 100%;
      height: 100%;
      background: #ffffff;
    }}
    .sidebar {{
      padding: 24px;
      display: grid;
      gap: 16px;
      align-content: start;
      max-height: calc(100vh - 48px);
      overflow-y: auto;
    }}
    .meta-card {{
      border-radius: 20px;
      border: 1px solid var(--line);
      padding: 16px 18px;
      background: #fff;
    }}
    .meta-card h2 {{
      margin: 0 0 12px;
      font-size: 11px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: #7c8faa;
    }}
    .sidebar-title {{
      margin: 0 0 10px;
      font-size: 22px;
      line-height: 1.2;
      color: var(--ink);
    }}
    .meta-card p, .meta-card li {{
      margin: 0;
      font-size: 13px;
      line-height: 1.7;
      color: var(--ink);
    }}
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }}
    .tag {{
      padding: 7px 11px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }}
    .meta-card ul {{
      padding-left: 18px;
      margin: 0;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 96px minmax(0, 1fr);
      gap: 10px 12px;
      font-size: 13px;
    }}
    .kv dt {{ color: var(--muted); font-weight: 700; }}
    .kv dd {{ margin: 0; color: var(--ink); }}
    @media (max-width: 1040px) {{
      .shell {{ grid-template-columns: minmax(0, 1fr); }}
      .sidebar {{ max-height: none; overflow: visible; }}
    }}
  </style>
</head>
<body>
  <div id="recognition-simulator-root" class="canvas-priority" data-render-mode="{render_mode}" data-priority-mode="{render_priority_mode}">
    <div class="shell">
      <section class="panel chart-panel">
        <div class="chart-shell">
          <div class="chart-header">
            <strong>{html_lib.escape(schema.system)} · HTML/canvas Reconstruction</strong>
            <span>{html_lib.escape(schema.x_axis.label)} / {html_lib.escape(schema.y_axis.label)}</span>
          </div>
          <div class="fidelity-banner">{html_lib.escape(banner)}</div>
          <div class="chart-stage">
            <div class="figure-stack">
              <canvas id="recognition-reconstruction-canvas" width="{canvas_width}" height="{canvas_height}" aria-label="recognition reconstruction canvas"></canvas>
            </div>
          </div>
        </div>
      </section>
      {sidebar}
    </div>
  </div>
  <script type="application/json" id="recognition-simulator-data">{payload_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById('recognition-simulator-data').textContent || '{{}}')
    const scene = payload.reconstruction_scene || null
    const geometry = payload.geometry || {{}}
    const schema = payload.schema || {{}}
    const canvas = document.getElementById('recognition-reconstruction-canvas')

    function number(value, fallback = 0) {{
      const parsed = Number(value)
      return Number.isFinite(parsed) ? parsed : fallback
    }}

    function drawLoopPath(ctx, loop) {{
      if (!Array.isArray(loop) || loop.length < 2) return
      ctx.moveTo(number(loop[0][0]), number(loop[0][1]))
      for (let index = 1; index < loop.length; index += 1) {{
        ctx.lineTo(number(loop[index][0]), number(loop[index][1]))
      }}
      ctx.closePath()
    }}

    function drawRectPrimitive(ctx, rect) {{
      if (!Array.isArray(rect) || rect.length !== 4) return
      const x = number(rect[0])
      const y = number(rect[1])
      const width = number(rect[2])
      const height = number(rect[3])
      if (width <= 0 || height <= 0) return
      ctx.fillRect(x, y, width, height)
    }}

    function paintVectorScene(ctx, width, height) {{
      ctx.fillStyle = String(scene.background || '#ffffff')
      ctx.fillRect(0, 0, width, height)
      const layers = Array.isArray(scene.layers) ? scene.layers : []
      for (const layer of layers) {{
        ctx.save()
        ctx.globalAlpha = number(layer.opacity, 1)
        ctx.fillStyle = String(layer.fill || '#000000')
        const rects = Array.isArray(layer.rects) ? layer.rects : []
        if (rects.length) {{
          for (const rect of rects) drawRectPrimitive(ctx, rect)
          ctx.restore()
          continue
        }}
        const loops = Array.isArray(layer.loops) ? layer.loops : []
        if (!loops.length) {{
          ctx.restore()
          continue
        }}
        ctx.beginPath()
        for (const loop of loops) drawLoopPath(ctx, loop)
        ctx.fill('evenodd')
        const strokeWidth = number(layer.strokeWidth, 0)
        const stroke = layer.stroke
        if (stroke && strokeWidth > 0) {{
          ctx.strokeStyle = String(stroke)
          ctx.lineWidth = strokeWidth
          ctx.lineJoin = 'round'
          ctx.lineCap = 'round'
          ctx.stroke()
        }}
        ctx.restore()
      }}
    }}

    function tickValues(minimum, maximum, count = 5) {{
      if (count <= 1) return [minimum]
      const step = (maximum - minimum) / (count - 1)
      return Array.from({{ length: count }}, (_, index) => minimum + step * index)
    }}

    function paintSchemaModel(ctx, width, height) {{
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, width, height)
      const left = number(geometry.margin_left, 120)
      const top = number(geometry.margin_top, 80)
      const chartWidth = number(geometry.chart_width, width - 220)
      const chartHeight = number(geometry.chart_height, height - 180)
      const right = left + chartWidth
      const bottom = top + chartHeight
      const xMin = number(geometry.x_min, 0)
      const xMax = number(geometry.x_max, 1)
      const yMin = number(geometry.y_min, 0)
      const yMax = number(geometry.y_max, 1)
      const xPx = (value) => left + ((value - xMin) / Math.max(xMax - xMin, 1e-6)) * chartWidth
      const yPx = (value) => bottom - ((value - yMin) / Math.max(yMax - yMin, 1e-6)) * chartHeight
      const cpX = xPx(number(geometry.base_cp_x, (xMin + xMax) / 2))
      const cpY = yPx(number(geometry.base_cp_y, (yMin + yMax) / 2))
      const leftEdge = xPx(number(geometry.left_edge_x, xMin))
      const rightEdge = xPx(number(geometry.right_edge_x, xMax))
      const leftShoulder = xPx(number(geometry.left_shoulder_x, xMin + (xMax - xMin) * 0.25))
      const rightShoulder = xPx(number(geometry.right_shoulder_x, xMin + (xMax - xMin) * 0.75))
      const leftPeak = yPx(number(geometry.left_peak_temp, yMax))
      const rightPeak = yPx(number(geometry.right_peak_temp, yMax))

      ctx.strokeStyle = '#d7dfed'
      ctx.lineWidth = 1
      ctx.fillStyle = '#f8fbff'
      ctx.fillRect(left, top, chartWidth, chartHeight)
      for (const value of tickValues(xMin, xMax)) {{
        const x = xPx(value)
        ctx.beginPath()
        ctx.moveTo(x, top)
        ctx.lineTo(x, bottom)
        ctx.stroke()
      }}
      for (const value of tickValues(yMin, yMax)) {{
        const y = yPx(value)
        ctx.beginPath()
        ctx.moveTo(left, y)
        ctx.lineTo(right, y)
        ctx.stroke()
      }}

      ctx.fillStyle = 'rgba(252, 176, 64, 0.34)'
      ctx.beginPath()
      ctx.moveTo(left, top)
      ctx.lineTo(right, top)
      ctx.lineTo(rightEdge, rightPeak)
      ctx.bezierCurveTo(rightShoulder, rightPeak + 32, rightShoulder, cpY - 18, cpX, cpY)
      ctx.bezierCurveTo(leftShoulder, cpY - 18, leftShoulder, leftPeak + 32, leftEdge, leftPeak)
      ctx.closePath()
      ctx.fill()

      ctx.fillStyle = 'rgba(71, 118, 230, 0.22)'
      ctx.beginPath()
      ctx.moveTo(left, bottom)
      ctx.lineTo(leftEdge, leftPeak)
      ctx.bezierCurveTo(leftShoulder, leftPeak + 32, leftShoulder, cpY - 18, cpX, cpY)
      ctx.lineTo(cpX, bottom)
      ctx.closePath()
      ctx.fill()

      ctx.fillStyle = 'rgba(20, 180, 138, 0.2)'
      ctx.beginPath()
      ctx.moveTo(cpX, cpY)
      ctx.bezierCurveTo(rightShoulder, cpY - 18, rightShoulder, rightPeak + 32, rightEdge, rightPeak)
      ctx.lineTo(right, bottom)
      ctx.lineTo(cpX, bottom)
      ctx.closePath()
      ctx.fill()

      ctx.strokeStyle = '#18263d'
      ctx.lineWidth = 2
      ctx.strokeRect(left, top, chartWidth, chartHeight)
      ctx.lineWidth = 3
      ctx.strokeStyle = '#f97316'
      ctx.beginPath()
      ctx.moveTo(leftEdge, leftPeak)
      ctx.bezierCurveTo(leftShoulder, leftPeak + 32, leftShoulder, cpY - 18, cpX, cpY)
      ctx.bezierCurveTo(rightShoulder, cpY - 18, rightShoulder, rightPeak + 32, rightEdge, rightPeak)
      ctx.stroke()
      ctx.strokeStyle = '#2f63ff'
      ctx.beginPath()
      ctx.moveTo(leftShoulder, cpY)
      ctx.lineTo(rightShoulder, cpY)
      ctx.stroke()

      ctx.fillStyle = '#18263d'
      ctx.font = '16px sans-serif'
      ctx.textAlign = 'center'
      for (const value of tickValues(xMin, xMax)) {{
        const x = xPx(value)
        ctx.fillText(Number(value).toPrecision(3).replace(/\\.0+$/, ''), x, bottom + 30)
      }}
      ctx.textAlign = 'right'
      for (const value of tickValues(yMin, yMax)) {{
        const y = yPx(value)
        ctx.fillText(Number(value).toPrecision(3).replace(/\\.0+$/, ''), left - 14, y + 5)
      }}
      ctx.textAlign = 'center'
      ctx.font = '18px sans-serif'
      ctx.fillText(String(schema.x_axis?.label || 'composition'), left + chartWidth / 2, height - 28)
      ctx.save()
      ctx.translate(28, top + chartHeight / 2)
      ctx.rotate(-Math.PI / 2)
      ctx.fillText(String(schema.y_axis?.label || 'temperature'), 0, 0)
      ctx.restore()

      ctx.font = 'bold 18px sans-serif'
      ctx.fillStyle = '#f97316'
      ctx.fillText(String(geometry.liquid_label || 'Liquid'), left + chartWidth / 2, top + 36)
      ctx.fillStyle = '#2f63ff'
      ctx.fillText(String(geometry.left_solid_label || 'Primary solid'), left + chartWidth * 0.25, bottom - 28)
      ctx.fillStyle = '#14b48a'
      ctx.fillText(String(geometry.right_solid_label || 'Secondary solid'), left + chartWidth * 0.75, bottom - 28)

      ctx.fillStyle = '#ffffff'
      ctx.strokeStyle = '#18263d'
      ctx.lineWidth = 3
      ctx.beginPath()
      ctx.arc(cpX, cpY, 9, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = '#18263d'
      ctx.textAlign = 'left'
      ctx.font = 'bold 14px sans-serif'
      ctx.fillText(String(geometry.critical_point_label || 'critical point'), cpX + 14, cpY - 12)
    }}

    function paintCanvas() {{
      if (!canvas) return
      const width = canvas.width
      const height = canvas.height
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.clearRect(0, 0, width, height)
      if (scene && Array.isArray(scene.layers)) {{
        paintVectorScene(ctx, width, height)
      }} else {{
        paintSchemaModel(ctx, width, height)
      }}
    }}

    paintCanvas()
  </script>
</body>
</html>"""


def render_reconstruction_html(
    schema: ReconstructionSchema,
    geometry: ReconstructionGeometry,
    result_profile: ResultProfile,
    *,
    source_image_data_url: str | None = None,
    source_image_name: str = "",
) -> str:
    _ = source_image_name
    return _render_canvas_reconstruction_html(
        schema,
        geometry,
        result_profile,
        source_image_data_url=source_image_data_url,
    )
