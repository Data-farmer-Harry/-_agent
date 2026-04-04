from __future__ import annotations

import base64
import os
from functools import lru_cache
from html import escape
from pathlib import Path

from app.thermo.registry import get_calculated_binary_card, resolve_tdb_path
from app.utils.path_utils import ensure_directory, write_text_file


def _configure_matplotlib_cache() -> None:
    cache_dir = Path("/tmp") / "phase_diagram_agent_mpl"
    ensure_directory(cache_dir)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))


@lru_cache(maxsize=1)
def _load_plot_symbols():
    _configure_matplotlib_cache()

    import matplotlib

    matplotlib.use("Agg")

    from matplotlib import pyplot as plt
    from pycalphad import Database, binplot, variables as v

    return plt, Database, binplot, v


@lru_cache(maxsize=8)
def _load_database(path: str):
    _, Database, _, _ = _load_plot_symbols()
    return Database(path)


def build_calculated_phase_diagram_report(
    *,
    system_name: str,
    temperature_min: float,
    temperature_max: float,
    pressure: float,
    step_size: float,
    notes: str,
    output_path: str = "result.html",
) -> dict[str, str]:
    card = get_calculated_binary_card(system_name)
    if card is None:
        raise KeyError(f"Unsupported calculated binary system: {system_name}")

    plt, _, binplot, v = _load_plot_symbols()

    tdb_path = resolve_tdb_path(card)
    output_file = Path(output_path)
    ensure_directory(output_file.parent)
    image_path = output_file.with_suffix(".png")

    db = _load_database(str(tdb_path))
    conditions = {
        v.X(card.x_component): (1e-5, 1.0, 0.005),
        v.T: (float(temperature_min), float(temperature_max), max(float(step_size) / 10.0, 1.0)),
        v.P: float(pressure),
        v.N: 1.0,
    }

    ax = binplot(db, [*card.components, "VA"], list(card.phases), conditions)
    ax.set_title(f"{card.system_name} Binary Phase Diagram (pycalphad + {card.database_name})")
    ax.set_xlabel(card.x_axis_label)
    ax.set_ylabel("Temperature (K)")
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(float(temperature_min), float(temperature_max))
    ax.figure.set_size_inches(10, 7)
    ax.figure.set_dpi(180)
    ax.figure.tight_layout()
    ax.figure.savefig(image_path, bbox_inches="tight")
    plt.close(ax.figure)

    image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    safe_system_name = escape(card.system_name)
    safe_summary = escape(card.summary)
    safe_database_name = escape(card.database_name)
    safe_x_axis_label = escape(card.x_axis_label)
    safe_family = escape(card.family)
    safe_notes = escape(notes or "(none)")
    safe_source_url = escape(card.source_url)
    safe_documentation_url = escape(card.documentation_url)
    safe_phase_list = ", ".join(escape(phase) for phase in card.phases)
    safe_component_list = ", ".join(escape(component) for component in card.components)
    safe_temperature_label = f"{temperature_min:.1f}-{temperature_max:.1f} K"
    safe_pressure_label = f"{pressure:.0f} Pa"
    safe_step_size_label = f"{float(step_size):.1f} K"
    html_content = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="phase-diagram-agent-layout" content="v2">
    <meta name="phase-diagram-model-source" content="pycalphad_tdb_database">
    <meta name="phase-diagram-model-mode" content="tdb_equilibrium_calculation">
    <title>{card.system_name} Phase Diagram</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f5f8fc;
        --panel: #ffffff;
        --line: #dbe5f0;
        --text: #122033;
        --muted: #5f7289;
        --accent: #2563eb;
      }}
      * {{ box-sizing: border-box; }}
      html, body {{
        height: 100%;
      }}
      body {{
        margin: 0;
        font-family: "Segoe UI", "PingFang SC", sans-serif;
        background: linear-gradient(180deg, #f9fbff 0%, var(--bg) 100%);
        color: var(--text);
        overflow: hidden;
      }}
      #phase-diagram-agent-result {{
        width: 100%;
        height: 100vh;
        overflow: hidden;
        padding: 18px;
        display: grid;
        grid-template-rows: minmax(0, 1fr);
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 18px 20px;
        box-shadow: 0 16px 38px rgba(15, 23, 42, 0.06);
      }}
      .badge {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 34px;
        padding: 8px 12px;
        border-radius: 999px;
        border: 1px solid #dbe8ff;
        background: rgba(255, 255, 255, 0.86);
        color: var(--accent);
        font-size: 12px;
        font-weight: 700;
        line-height: 1.35;
        text-align: center;
      }}
      .panel p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.65;
      }}
      .eyebrow {{
        margin-bottom: 6px;
        color: var(--accent);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      .workspace {{
        display: grid;
        grid-template-columns: minmax(0, 1.9fr) minmax(300px, 356px);
        gap: 16px;
        height: 100%;
        min-height: 0;
        align-items: stretch;
      }}
      .figure-panel {{
        overflow: hidden;
        height: 100%;
        min-height: 0;
        min-width: 0;
        display: flex;
        flex-direction: column;
        padding: 12px;
      }}
      .figure-stage {{
        display: flex;
        justify-content: stretch;
        align-items: stretch;
        min-height: 0;
        flex: 1 1 auto;
        border-radius: 18px;
        border: 1px solid #dbe5f0;
        background: linear-gradient(180deg, #fbfdff 0%, #f3f8ff 100%);
        overflow: hidden;
      }}
      .figure-stage img {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
        object-position: center top;
        border-radius: 14px;
        background: #ffffff;
      }}
      .inline-card {{
        border-radius: 16px;
        border: 1px solid #dbe5f0;
        background: rgba(248, 251, 255, 0.9);
        padding: 14px 16px;
      }}
      .inline-card strong {{
        display: block;
        margin-bottom: 6px;
        color: var(--accent);
      }}
      .sidebar {{
        display: grid;
        gap: 14px;
        height: 100%;
        max-height: 100%;
        min-height: 0;
        min-width: 0;
        overflow-y: auto;
        padding-right: 4px;
        align-content: start;
      }}
      .sidebar::-webkit-scrollbar {{
        width: 10px;
      }}
      .sidebar::-webkit-scrollbar-thumb {{
        background: rgba(148, 163, 184, 0.4);
        border-radius: 999px;
      }}
      .sidebar::-webkit-scrollbar-track {{
        background: transparent;
      }}
      .info-card h3 {{
        margin: 0 0 12px;
        font-size: 19px;
      }}
      .info-card p {{
        font-size: 14px;
      }}
      .fact-grid {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
      }}
      .fact {{
        border-radius: 14px;
        border: 1px solid #e6edf6;
        background: linear-gradient(180deg, #fcfdff 0%, #f7faff 100%);
        padding: 12px 14px;
      }}
      .fact-label {{
        display: block;
        margin-bottom: 6px;
        color: var(--muted);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      .fact-value {{
        display: block;
        color: var(--text);
        font-size: 15px;
        font-weight: 700;
        line-height: 1.45;
        word-break: break-word;
      }}
      .phase-list {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }}
      .phase-chip {{
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        border: 1px solid #dbe8ff;
        background: #f8fbff;
        padding: 8px 12px;
        color: var(--accent);
        font-size: 12px;
        font-weight: 700;
      }}
      .reference-links {{
        display: grid;
        gap: 10px;
      }}
      .reference-links a {{
        display: block;
        border-radius: 14px;
        border: 1px solid #e6edf6;
        background: linear-gradient(180deg, #fcfdff 0%, #f7faff 100%);
        padding: 12px 14px;
        font-size: 13px;
        line-height: 1.55;
        text-decoration: none;
        word-break: break-word;
      }}
      ul {{
        margin: 10px 0 0 18px;
        color: var(--muted);
        line-height: 1.65;
      }}
      a {{ color: var(--accent); }}
      @media (max-width: 720px) {{
        body {{
          overflow: auto;
        }}
        #phase-diagram-agent-result {{
          height: auto;
          display: block;
        }}
        .workspace {{
          grid-template-columns: 1fr;
          height: auto;
        }}
        .sidebar {{
          height: auto;
          max-height: none;
          overflow: visible;
          padding-right: 0;
        }}
      }}
      @media (max-width: 560px) {{
        #phase-diagram-agent-result {{
          padding: 16px 12px 24px;
        }}
        .panel {{
          padding: 16px;
        }}
        .figure-stage {{
          min-height: 320px;
        }}
      }}
    </style>
  </head>
  <body>
    <main id="phase-diagram-agent-result">
      <section class="workspace">
        <article class="panel figure-panel">
          <div class="figure-stage">
            <img alt="{safe_system_name} calculated phase diagram" src="data:image/png;base64,{image_base64}">
          </div>
        </article>

        <aside class="sidebar">
          <section class="panel info-card">
            <h3>Overview</h3>
            <p>{safe_summary}</p>
          </section>

          <section class="panel info-card">
            <h3>Conditions</h3>
            <div class="fact-grid">
              <div class="fact">
                <span class="fact-label">Temperature</span>
                <span class="fact-value">{safe_temperature_label}</span>
              </div>
              <div class="fact">
                <span class="fact-label">Pressure</span>
                <span class="fact-value">{safe_pressure_label}</span>
              </div>
              <div class="fact">
                <span class="fact-label">Step Size</span>
                <span class="fact-value">{safe_step_size_label}</span>
              </div>
              <div class="fact">
                <span class="fact-label">X Axis</span>
                <span class="fact-value">{safe_x_axis_label}</span>
              </div>
            </div>
          </section>

          <section class="panel info-card">
            <h3>Selected Phases</h3>
            <div class="phase-list">
              {"".join(f'<span class="phase-chip">{escape(phase)}</span>' for phase in card.phases)}
            </div>
          </section>

          <section class="panel info-card">
            <h3>Calculation Context</h3>
            <div class="fact-grid">
              <div class="fact">
                <span class="fact-label">System</span>
                <span class="fact-value">{safe_system_name}</span>
              </div>
              <div class="fact">
                <span class="fact-label">Family</span>
                <span class="fact-value">{safe_family}</span>
              </div>
              <div class="fact">
                <span class="fact-label">Components</span>
                <span class="fact-value">{safe_component_list}</span>
              </div>
              <div class="fact">
                <span class="fact-label">Database</span>
                <span class="fact-value">{safe_database_name}</span>
              </div>
            </div>
          </section>

          <section class="panel info-card">
            <h3>Method & Notes</h3>
            <div class="fact-grid">
              <div class="fact">
                <span class="fact-label">Source</span>
                <span class="fact-value">pycalphad_tdb_database</span>
              </div>
              <div class="fact">
                <span class="fact-label">Mode</span>
                <span class="fact-value">tdb_equilibrium_calculation</span>
              </div>
              <div class="fact">
                <span class="fact-label">Notes</span>
                <span class="fact-value">{safe_notes}</span>
              </div>
            </div>
          </section>

          <section class="panel info-card">
            <h3>References</h3>
            <div class="reference-links">
              <a href="{safe_source_url}" target="_blank" rel="noreferrer">
                <span class="fact-label">Source URL</span>
                <span class="fact-value">{safe_source_url}</span>
              </a>
              <a href="{safe_documentation_url}" target="_blank" rel="noreferrer">
                <span class="fact-label">Documentation</span>
                <span class="fact-value">{safe_documentation_url}</span>
              </a>
            </div>
          </section>
        </aside>
      </section>
    </main>
  </body>
</html>"""

    write_text_file(output_file, html_content)
    return {
        "system_name": card.system_name,
        "family": card.family,
        "method": "tdb_equilibrium_calculation",
        "model_source": "pycalphad_tdb_database",
        "database_name": card.database_name,
        "output_path": str(output_file),
        "image_path": str(image_path),
    }
