from __future__ import annotations

import html
import re

import plotly.graph_objects as go

from app.config import settings
from app.schemas import (
    AxisCalibration,
    ImageDiagramBoundary,
    ImageDiagramLabel,
    ImageDiagramRequest,
    ImageDiagramSpec,
)
from app.services.llm_client import LLMClient


class PhaseDiagramImageService:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _extract_json_object(content: str) -> dict | None:
        return LLMClient.extract_json_object(content)

    def build_analysis_prompt(self, request: ImageDiagramRequest) -> str:
        return f"""You are a careful multimodal materials-diagram analyst.
Inspect the uploaded phase-diagram screenshot and return JSON only.

Rules:
1. Be conservative. If a title, label, region, or boundary is unclear, leave it empty.
2. Do not invent phase regions that are not visible in the screenshot.
3. The numeric axis ranges are already supplied by the caller; do not change them.
4. Prefer short summaries and only include labels/boundaries when they are reasonably legible.
5. Return a JSON object with these keys:
   - chart_title: string
   - system_name: string
   - summary: string
   - confidence: number between 0 and 1
   - notes: string[]
   - labels: [{{"text": string, "x": number, "y": number}}]
   - boundaries: [{{"name": string, "points": [[x, y], ...]}}]
6. Coordinates must use the supplied calibrated axes below.
7. If you are not confident enough to place a boundary, return an empty boundaries array.

Caller-provided calibration:
- filename: {request.filename or '(unknown)'}
- system_name: {request.system_name or '(unknown)'}
- chart_title: {request.chart_title or '(unknown)'}
- diagram_type: {request.diagram_type}
- x_axis.label: {request.x_axis.label}
- x_axis.minimum: {request.x_axis.minimum}
- x_axis.maximum: {request.x_axis.maximum}
- y_axis.label: {request.y_axis.label}
- y_axis.minimum: {request.y_axis.minimum}
- y_axis.maximum: {request.y_axis.maximum}
- notes: {request.notes or '(none)'}
"""

    def analyze_image(self, request: ImageDiagramRequest) -> tuple[ImageDiagramSpec, str]:
        prompt = self.build_analysis_prompt(request)
        manual_spec = self._build_manual_spec(request)

        if self.llm_client.is_configured():
            try:
                llm_payload = self._analyze_with_llm(request, prompt)
                if llm_payload:
                    return self._merge_with_manual_spec(request, manual_spec, llm_payload), prompt
            except RuntimeError:
                pass

        return manual_spec, prompt

    @staticmethod
    def _extract_json_object(content: str) -> dict | None:
        return LLMClient.extract_json_object(content)

    @staticmethod
    def _build_manual_spec(request: ImageDiagramRequest) -> ImageDiagramSpec:
        chart_title = request.chart_title.strip() or request.system_name.strip() or "Calibrated phase-diagram screenshot"
        notes = [
            "The uploaded screenshot is shown as a calibrated background image with explicit axis ranges.",
            "This fallback mode avoids inventing phase regions when multimodal extraction is unavailable or uncertain.",
        ]
        if request.notes.strip():
            notes.append(f"User note: {request.notes.strip()}")

        return ImageDiagramSpec(
            chart_title=chart_title,
            system_name=request.system_name.strip(),
            filename=request.filename.strip(),
            diagram_type=request.diagram_type,
            source_image_data_url=request.image_data_url,
            x_axis=request.x_axis,
            y_axis=request.y_axis,
            detection_mode="manual_calibrated",
            confidence=0.38,
            summary="Generated a calibrated screenshot view with deterministic axes and no speculative phase reconstruction.",
            notes=notes,
            labels=[],
            boundaries=[],
        )

    def _analyze_with_llm(self, request: ImageDiagramRequest, prompt: str) -> dict | None:
        return self.llm_client.chat_multimodal_json(
            system_prompt="Return JSON only. Be conservative and avoid hallucinating unreadable phase features.",
            user_prompt=prompt,
            image_data_url=request.image_data_url,
            max_tokens=min(settings.llm_max_tokens, 2000),
            temperature=0.1,
        )

    @staticmethod
    def _normalize_point(point: list[float], x_axis: AxisCalibration, y_axis: AxisCalibration) -> list[float] | None:
        if len(point) != 2:
            return None
        x_value = float(point[0])
        y_value = float(point[1])
        if x_axis.minimum <= x_value <= x_axis.maximum and y_axis.minimum <= y_value <= y_axis.maximum:
            return [x_value, y_value]
        return None

    def _merge_with_manual_spec(self, request: ImageDiagramRequest, manual_spec: ImageDiagramSpec, llm_payload: dict) -> ImageDiagramSpec:
        labels: list[ImageDiagramLabel] = []
        for raw_label in llm_payload.get("labels", []):
            if not isinstance(raw_label, dict):
                continue
            try:
                label = ImageDiagramLabel(
                    text=str(raw_label.get("text", "")).strip(),
                    x=float(raw_label.get("x")),
                    y=float(raw_label.get("y")),
                )
            except (TypeError, ValueError):
                continue
            if label.text and request.x_axis.minimum <= label.x <= request.x_axis.maximum and request.y_axis.minimum <= label.y <= request.y_axis.maximum:
                labels.append(label)

        boundaries: list[ImageDiagramBoundary] = []
        for raw_boundary in llm_payload.get("boundaries", []):
            if not isinstance(raw_boundary, dict):
                continue
            points = []
            for raw_point in raw_boundary.get("points", []):
                if not isinstance(raw_point, list):
                    continue
                normalized = self._normalize_point(raw_point, request.x_axis, request.y_axis)
                if normalized is not None:
                    points.append(normalized)
            name = str(raw_boundary.get("name", "")).strip()
            if name and len(points) >= 2:
                boundaries.append(ImageDiagramBoundary(name=name, points=points))

        notes = list(manual_spec.notes)
        for raw_note in llm_payload.get("notes", []):
            note = str(raw_note).strip()
            if note and note not in notes:
                notes.append(note)

        chart_title = request.chart_title.strip() or str(llm_payload.get("chart_title", "")).strip() or manual_spec.chart_title
        system_name = request.system_name.strip() or str(llm_payload.get("system_name", "")).strip() or manual_spec.system_name
        summary = str(llm_payload.get("summary", "")).strip() or manual_spec.summary

        try:
            confidence = float(llm_payload.get("confidence", manual_spec.confidence))
        except (TypeError, ValueError):
            confidence = manual_spec.confidence
        confidence = max(0.0, min(confidence, 1.0))

        if not labels and not boundaries:
            return manual_spec

        return ImageDiagramSpec(
            chart_title=chart_title,
            system_name=system_name,
            filename=request.filename.strip(),
            diagram_type=request.diagram_type,
            source_image_data_url=request.image_data_url,
            x_axis=request.x_axis,
            y_axis=request.y_axis,
            detection_mode="vision_augmented",
            confidence=max(confidence, 0.45),
            summary=summary,
            notes=notes,
            labels=labels,
            boundaries=boundaries,
        )

    @staticmethod
    def _axis_title(axis: AxisCalibration) -> str:
        return axis.label

    def render_html(self, spec: ImageDiagramSpec) -> str:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=[spec.x_axis.minimum, spec.x_axis.maximum],
                y=[spec.y_axis.minimum, spec.y_axis.maximum],
                mode="markers",
                marker={"opacity": 0.0, "size": 1},
                hoverinfo="skip",
                showlegend=False,
            )
        )

        for index, boundary in enumerate(spec.boundaries):
            x_values = [point[0] for point in boundary.points]
            y_values = [point[1] for point in boundary.points]
            fig.add_trace(
                go.Scatter(
                    x=x_values,
                    y=y_values,
                    mode="lines",
                    name=boundary.name,
                    line={"width": 2.4, "color": ["#2563eb", "#dc2626", "#0f766e", "#7c3aed"][index % 4]},
                    hovertemplate="%{x:.3f}, %{y:.3f}<extra>" + boundary.name + "</extra>",
                )
            )

        for label in spec.labels:
            fig.add_annotation(
                text=label.text,
                x=label.x,
                y=label.y,
                showarrow=False,
                bgcolor="rgba(255,255,255,0.82)",
                bordercolor="rgba(148, 163, 184, 0.55)",
                font={"size": 12, "color": "#0f172a"},
            )

        x_range = spec.x_axis.maximum - spec.x_axis.minimum
        y_range = spec.y_axis.maximum - spec.y_axis.minimum
        fig.update_layout(
            title=None,
            template="plotly_white",
            dragmode="pan",
            hovermode="closest",
            margin={"l": 70, "r": 24, "t": 24, "b": 64},
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0.0},
            images=[
                {
                    "source": spec.source_image_data_url,
                    "xref": "x",
                    "yref": "y",
                    "x": spec.x_axis.minimum,
                    "y": spec.y_axis.maximum,
                    "sizex": x_range,
                    "sizey": y_range,
                    "sizing": "stretch",
                    "opacity": 1.0,
                    "layer": "below",
                }
            ],
        )
        fig.update_xaxes(
            title=self._axis_title(spec.x_axis),
            range=[spec.x_axis.minimum, spec.x_axis.maximum],
            showgrid=True,
            gridcolor="rgba(148, 163, 184, 0.32)",
            zeroline=False,
        )
        fig.update_yaxes(
            title=self._axis_title(spec.y_axis),
            range=[spec.y_axis.minimum, spec.y_axis.maximum],
            showgrid=True,
            gridcolor="rgba(148, 163, 184, 0.32)",
            zeroline=False,
        )

        chart_html = fig.to_html(
            full_html=False,
            include_plotlyjs="cdn",
            config={"responsive": True, "displaylogo": False, "scrollZoom": True},
        )

        summary_cards = [
            ("System", spec.system_name or "Not specified"),
            ("Diagram Type", spec.diagram_type.title()),
            ("X Axis", f"{spec.x_axis.label}: {spec.x_axis.minimum:g} to {spec.x_axis.maximum:g}"),
            ("Y Axis", f"{spec.y_axis.label}: {spec.y_axis.minimum:g} to {spec.y_axis.maximum:g}"),
            ("Detection", spec.detection_mode.replace("_", " ")),
            ("Confidence", f"{spec.confidence:.2f}"),
        ]
        cards_html = "".join(
            f'<div class="summary-card"><span class="summary-label">{html.escape(label)}</span><strong class="summary-value">{html.escape(value)}</strong></div>'
            for label, value in summary_cards
        )
        notes_html = "".join(f"<li>{html.escape(note)}</li>" for note in spec.notes)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="phase-diagram-agent-layout" content="v1">
  <title>{html.escape(spec.chart_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --page-bg: #edf4fb;
      --panel-bg: rgba(255, 255, 255, 0.94);
      --line: rgba(191, 219, 254, 0.55);
      --text: #102033;
      --muted: #52606d;
      --accent: #1565c0;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "SF Pro Text", "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f7fbff 0%, var(--page-bg) 100%);
      color: var(--text);
    }}
    .page-shell {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 20px 42px;
    }}
    .hero {{
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 22px 24px;
      background: linear-gradient(135deg, #0f4c81 0%, #1565c0 58%, #0f766e 100%);
      color: #ffffff;
      box-shadow: 0 18px 46px rgba(15, 23, 42, 0.12);
    }}
    .hero h1 {{ margin: 0; font-size: 32px; }}
    .hero p {{ margin: 10px 0 0; color: rgba(255,255,255,0.9); line-height: 1.6; }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .summary-card {{
      border: 1px solid rgba(209, 219, 233, 0.8);
      border-radius: 16px;
      padding: 14px;
      background: var(--panel-bg);
      box-shadow: 0 10px 26px rgba(15, 23, 42, 0.04);
    }}
    .summary-label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .summary-value {{
      display: block;
      font-size: 15px;
      line-height: 1.45;
    }}
    .panel {{
      margin-top: 16px;
      border: 1px solid rgba(209, 219, 233, 0.82);
      border-radius: 18px;
      padding: 18px;
      background: var(--panel-bg);
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.05);
    }}
    .panel h2 {{
      margin: 0 0 8px;
      font-size: 20px;
    }}
    .panel p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}
    .chart-shell {{
      border: 1px solid rgba(209, 219, 233, 0.86);
      border-radius: 16px;
      overflow: hidden;
      background: #ffffff;
      margin-top: 14px;
      min-height: 680px;
    }}
    ul {{
      margin: 12px 0 0;
      padding-left: 20px;
      color: var(--muted);
      line-height: 1.7;
    }}
    @media (max-width: 900px) {{
      .page-shell {{ padding: 18px 12px 28px; }}
      .hero {{ padding: 18px; }}
      .chart-shell {{ min-height: 520px; }}
    }}
  </style>
</head>
<body>
  <main id="phase-diagram-agent-result" class="page-shell">
    <section class="hero">
      <h1>{html.escape(spec.chart_title)}</h1>
      <p>{html.escape(spec.summary)}</p>
    </section>
    <section class="summary-grid">{cards_html}</section>
    <section class="panel">
      <h2>Calibrated phase-diagram view</h2>
      <p>The uploaded screenshot is used as the chart background so the axis ranges remain explicit and adjustable. Any overlays are kept conservative.</p>
      <div class="chart-shell">{chart_html}</div>
    </section>
    <section class="panel">
      <h2>Interpretation notes</h2>
      <ul>{notes_html}</ul>
    </section>
  </main>
</body>
</html>"""
