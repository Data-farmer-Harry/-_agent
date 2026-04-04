from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import List

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def generate_plot(output_dir: Path) -> Path:
    thermo_path = output_dir / "thermo.csv"
    steps: List[float] = []
    temperatures: List[float] = []
    energy: List[float] = []
    with thermo_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            steps.append(float(row["step"]))
            temperatures.append(float(row["temp"]))
            energy.append(float(row["etotal"]))

    fig, ax1 = plt.subplots(figsize=(7, 4.5))
    fig.patch.set_facecolor("#f6f1e8")
    ax1.set_facecolor("#fffdf7")
    ax1.plot(steps, temperatures, color="#0b5d4d", linewidth=2.2, label="Temperature (K)")
    ax1.set_xlabel("Step")
    ax1.set_ylabel("Temperature (K)", color="#0b5d4d")
    ax1.tick_params(axis="y", labelcolor="#0b5d4d")

    ax2 = ax1.twinx()
    ax2.plot(steps, energy, color="#b04a1f", linewidth=2.0, linestyle="--", label="Total Energy")
    ax2.set_ylabel("Total Energy", color="#b04a1f")
    ax2.tick_params(axis="y", labelcolor="#b04a1f")

    ax1.set_title("MD Agent Thermo Overview")
    ax1.grid(alpha=0.25)
    plot_path = output_dir / "plot.png"
    plt.tight_layout()
    plt.savefig(plot_path, dpi=140)
    plt.close(fig)
    return plot_path
