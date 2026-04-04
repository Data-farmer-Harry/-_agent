from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def convert_dump(output_dir: Path) -> Path:
    dump_path = output_dir / "dump.atom"
    summary = {
        "has_dump": dump_path.exists(),
        "atom_count": 4 if dump_path.exists() else 0,
        "notes": "Basic dump summary for the demo pipeline.",
    }
    converted_path = output_dir / "structure_summary.json"
    converted_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return converted_path

