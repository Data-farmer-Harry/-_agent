from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from pathlib import Path

from pycalphad import Database, binplot, variables as v


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TDB_PATH = (
    PROJECT_ROOT
    / "backend/.venv/lib/python3.13/site-packages/pycalphad/tests/databases/pbsn.tdb"
)
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs/calculated_examples/pbsn_pycalphad.png"


def generate_pbsn_phase_diagram(
    *,
    tdb_path: Path = DEFAULT_TDB_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database(str(tdb_path))
    components = ["PB", "SN", "VA"]
    phases = ["LIQUID", "FCC_A1", "BCT_A5"]
    conditions = {
        v.X("SN"): (0.0, 1.0, 0.005),
        v.T: (300.0, 650.0, 2.0),
        v.P: 101325.0,
        v.N: 1.0,
    }

    ax = binplot(db, components, phases, conditions)
    ax.set_title("Pb-Sn Binary Phase Diagram (pycalphad + pbsn.tdb)")
    ax.set_xlabel("Mole fraction Sn")
    ax.set_ylabel("Temperature (K)")
    ax.figure.set_size_inches(10, 7)
    ax.figure.set_dpi(180)
    ax.figure.tight_layout()
    ax.figure.savefig(output_path, bbox_inches="tight")
    plt.close(ax.figure)
    return output_path


if __name__ == "__main__":
    output = generate_pbsn_phase_diagram()
    print(output)
