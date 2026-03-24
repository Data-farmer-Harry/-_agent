from __future__ import annotations

import html
import json
import re

from app.config import settings
from app.schemas import HtmlRedrawRequest
from app.services.llm_client import LLMClientService


LAYOUT_MARKER = "phase-diagram-agent-layout"
ROOT_ID = "phase-diagram-agent-result"


class PhaseDiagramHtmlService:
    def __init__(self, llm_client: LLMClientService | None = None) -> None:
        self.llm_client = llm_client or LLMClientService()

    def build_redraw_prompt(self, request: HtmlRedrawRequest) -> str:
        return f"""You are building a scientist-facing HTML page that redraws or explains a phase diagram.

Return HTML only. The HTML must be a full standalone document and must satisfy these rules:
1. Include a <meta name="{LAYOUT_MARKER}" content="v1"> marker.
2. Include a <main id="{ROOT_ID}"> root container.
3. Keep the layout readable for materials-research group members.
4. If the request is explanatory, include a clear explanation panel and a diagram panel.
5. If the request is ambiguous, be conservative and state any uncertainty in the page.
6. You may use Plotly via CDN for the diagram panel.
7. Do not output Markdown fences.

Request details:
- message: {request.message}
- system_name: {request.system_name or '(unknown)'}
- chart_title: {request.chart_title or '(unknown)'}
- diagram_type: {request.diagram_type}
- notes: {request.notes or '(none)'}
- filename: {request.filename or '(none)'}
"""

    @staticmethod
    def _extract_html_document(content: str) -> str:
        stripped = content.strip()
        fenced_match = re.search(r"```(?:html)?\s*([\s\S]*?)\s*```", stripped, flags=re.IGNORECASE)
        candidate = fenced_match.group(1).strip() if fenced_match else stripped
        if "<html" in candidate.lower():
            return candidate
        if "<body" in candidate.lower() or "<main" in candidate.lower():
            return f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{candidate}</body></html>"
        return candidate

    @staticmethod
    def _inject_root(body_inner: str) -> str:
        if ROOT_ID in body_inner:
            return body_inner
        return f"<main id=\"{ROOT_ID}\" class=\"agent-redraw-shell\">{body_inner}</main>"

    @staticmethod
    def _enforce_main_root(candidate: str) -> str:
        opening_patterns = (
            rf"<div([^>]*\bid=[\"']{ROOT_ID}[\"'][^>]*)>",
            rf"<section([^>]*\bid=[\"']{ROOT_ID}[\"'][^>]*)>",
            rf"<article([^>]*\bid=[\"']{ROOT_ID}[\"'][^>]*)>",
        )
        for pattern in opening_patterns:
            candidate, substitutions = re.subn(pattern, r"<main\1>", candidate, count=1, flags=re.IGNORECASE)
            if substitutions:
                break
        candidate = re.sub(
            rf"</(?:div|section|article)>\s*(?=(?:</body>|$))",
            "</main>",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
        return candidate

    @staticmethod
    def _collect_contract_issues(request: HtmlRedrawRequest, html_content: str) -> list[str]:
        issues: list[str] = []
        lowered_html = html_content.lower()

        if LAYOUT_MARKER not in html_content:
            issues.append("Generated HTML is missing the phase-diagram-agent-layout marker.")
        if f'id="{ROOT_ID}"' not in lowered_html and f"id='{ROOT_ID}'" not in lowered_html:
            issues.append("Generated HTML is missing the standardized root container.")
        if "<main" not in lowered_html or ROOT_ID not in lowered_html:
            issues.append("Generated HTML is missing the standardized main container for the redraw page.")
        if request.system_name.strip() and request.system_name.strip().lower() not in lowered_html:
            issues.append("The HTML redraw does not clearly reference the requested material system.")

        if lowered_html.count("<script") != lowered_html.count("</script>"):
            issues.append("Generated HTML contains mismatched script tags and may be truncated.")
        if lowered_html.count("<style") != lowered_html.count("</style>"):
            issues.append("Generated HTML contains mismatched style tags and may be truncated.")
        if "plotly" in lowered_html and "plotly.newplot" not in lowered_html:
            issues.append("Generated HTML loads Plotly but does not initialize a chart.")

        return issues

    def _normalize_html(self, content: str, request: HtmlRedrawRequest) -> str:
        candidate = self._extract_html_document(content)
        if not candidate.strip():
            return self._build_fallback_html(request)

        if "<html" not in candidate.lower():
            candidate = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{candidate}</body></html>"

        if LAYOUT_MARKER not in candidate:
            if re.search(r"<head[^>]*>", candidate, flags=re.IGNORECASE):
                candidate = re.sub(
                    r"(<head[^>]*>)",
                    rf"\1\n<meta name=\"{LAYOUT_MARKER}\" content=\"v1\">",
                    candidate,
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                candidate = candidate.replace("<html>", f"<html><head><meta name=\"{LAYOUT_MARKER}\" content=\"v1\"></head>", 1)

        body_match = re.search(r"(<body[^>]*>)([\s\S]*?)(</body>)", candidate, flags=re.IGNORECASE)
        if body_match:
            wrapped = self._inject_root(body_match.group(2).strip())
            candidate = f"{candidate[:body_match.start()]}{body_match.group(1)}{wrapped}{body_match.group(3)}{candidate[body_match.end():]}"
        else:
            candidate = f"<!DOCTYPE html><html><head><meta name=\"{LAYOUT_MARKER}\" content=\"v1\"></head><body>{self._inject_root(candidate)}</body></html>"

        return self._enforce_main_root(candidate)

    def _build_fallback_html(self, request: HtmlRedrawRequest) -> str:
        title = request.chart_title.strip() or request.system_name.strip() or "Phase Diagram Explanation"
        system_name = request.system_name.strip() or "Unspecified system"
        note = request.notes.strip() or "The agent built a deterministic fallback page because HTML redraw was unavailable."
        image_block = (
            f"""
            <section class="artifact-panel artifact-panel-image">
              <div class="panel-head">
                <strong>Reference Figure</strong>
                <span>{html.escape(request.filename or "uploaded image")}</span>
              </div>
              <img src="{request.image_data_url}" alt="Reference phase diagram" />
            </section>
            """
            if request.image_data_url
            else ""
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="{LAYOUT_MARKER}" content="v1">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eff5fb;
      --panel: rgba(255,255,255,0.94);
      --line: rgba(148,163,184,0.36);
      --text: #102033;
      --muted: #526173;
      --accent: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "PingFang SC", sans-serif;
      background: linear-gradient(180deg, #f7fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .agent-redraw-shell {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 28px 20px 36px;
      display: grid;
      gap: 18px;
    }}
    .hero, .artifact-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.07);
    }}
    .hero h1, .artifact-panel h2 {{ margin: 0 0 10px; }}
    .hero p, .artifact-panel p {{ margin: 0; line-height: 1.7; color: var(--muted); }}
    .artifact-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 12px;
      color: var(--muted);
    }}
    .diagram-frame {{
      min-height: 320px;
      border-radius: 18px;
      border: 1px dashed rgba(37, 99, 235, 0.34);
      background: linear-gradient(180deg, rgba(219, 234, 254, 0.4), rgba(255, 255, 255, 0.94));
      padding: 18px;
      display: grid;
      place-items: center;
      text-align: center;
    }}
    .artifact-panel img {{
      width: 100%;
      border-radius: 16px;
      border: 1px solid var(--line);
      display: block;
    }}
    @media (max-width: 900px) {{
      .artifact-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main id="{ROOT_ID}" class="agent-redraw-shell">
    <section class="hero">
      <p style="margin:0 0 8px;color:var(--accent);font-weight:700;letter-spacing:0.04em;text-transform:uppercase;">Agent HTML Redraw</p>
      <h1>{html.escape(title)}</h1>
      <p>This page summarizes and redraws phase-diagram knowledge for <strong>{html.escape(system_name)}</strong>. It is meant for materials-research discussion and can be upgraded once more detailed tools are attached.</p>
    </section>

    <section class="artifact-grid">
      <section class="artifact-panel">
        <div class="panel-head">
          <strong>Research Intent</strong>
          <span>{html.escape(request.diagram_type.title())}</span>
        </div>
        <p>{html.escape(request.message)}</p>
      </section>

      <section class="artifact-panel">
        <div class="panel-head">
          <strong>Agent Note</strong>
          <span>Fallback</span>
        </div>
        <p>{html.escape(note)}</p>
      </section>
    </section>

    <section class="artifact-grid">
      <section class="artifact-panel">
        <div class="panel-head">
          <strong>Redraw Canvas</strong>
          <span>Placeholder</span>
        </div>
        <div class="diagram-frame">
          <div>
            <h2>{html.escape(system_name)}</h2>
            <p>The agent reserved this area for a richer HTML redraw. In the deterministic fallback path, it preserves the explanation-first structure instead of inventing a diagram.</p>
          </div>
        </div>
      </section>
      {image_block}
    </section>
  </main>
</body>
</html>"""

    def generate_html(self, request: HtmlRedrawRequest) -> tuple[str, str, str]:
        prompt = self.build_redraw_prompt(request)

        if self.llm_client.is_configured():
            try:
                if request.image_data_url:
                    candidate = self.llm_client.chat_multimodal_text(
                        system_prompt="Return HTML only. Build a scientist-facing phase-diagram explanation page.",
                        user_prompt=prompt,
                        image_data_url=request.image_data_url,
                        max_tokens=min(settings.llm_max_tokens, 3600),
                    )
                else:
                    candidate = self.llm_client.chat_text(
                        system_prompt="Return HTML only. Build a scientist-facing phase-diagram explanation page.",
                        user_prompt=prompt,
                        max_tokens=min(settings.llm_max_tokens, 3400),
                    )
                normalized = self._normalize_html(candidate, request)
                return normalized, prompt, "llm_html_redraw"
            except RuntimeError:
                pass

        return self._build_fallback_html(request), prompt, "deterministic_html_fallback"

    def review_redraw_artifact(self, request: HtmlRedrawRequest, html_content: str) -> dict[str, object]:
        issues = self._collect_contract_issues(request, html_content)

        review_mode = "heuristic_redraw_review"
        llm_summary = ""
        llm_confidence: float | None = None

        should_call_llm_review = self.llm_client.is_configured() and len(html_content) <= 5000
        if should_call_llm_review:
            try:
                payload = self.llm_client.chat_json(
                    system_prompt="You are a careful reviewer for materials-agent HTML redraws. Return JSON only.",
                    user_prompt=(
                        "Review whether this HTML redraw matches the user's intent and is safe to show.\n"
                        f"Request: {json.dumps(request.model_dump(), ensure_ascii=False)}\n"
                        f"HTML preview: {html_content[:7000]}\n"
                        "Return JSON with keys: summary, confidence, issues."
                    ),
                    max_tokens=700,
                )
                if payload:
                    review_mode = "llm_plus_heuristic_redraw_review"
                    llm_summary = str(payload.get("summary") or "").strip()
                    try:
                        llm_confidence = max(0.0, min(float(payload.get("confidence", 0.76)), 1.0))
                    except (TypeError, ValueError):
                        llm_confidence = None
                    for raw_issue in payload.get("issues", []):
                        issue = str(raw_issue).strip()
                        if issue and issue not in issues:
                            issues.append(issue)
            except RuntimeError:
                pass

        confidence = max(0.14, 0.9 - 0.15 * len(issues))
        if llm_confidence is not None:
            confidence = min(confidence, llm_confidence) if issues else max(confidence, llm_confidence)

        passed = not issues
        summary = (
            llm_summary
            or (
                "HTML redraw review passed. The page structure looks presentable for a research-facing demo."
                if passed
                else f"HTML redraw review found {len(issues)} issue(s) that should be fixed before trusting the page."
            )
        )

        return {
            "passed": passed,
            "summary": summary,
            "confidence": round(confidence, 2),
            "issues": issues,
            "review_mode": review_mode,
        }
