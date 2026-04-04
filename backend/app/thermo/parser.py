from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class TdbMetadata:
    path: str
    database_name: str
    components: tuple[str, ...]
    phases: tuple[str, ...]


def _configure_matplotlib_cache() -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(Path("/tmp") / "phase_diagram_agent_mpl"))


@lru_cache(maxsize=32)
def parse_tdb_metadata(path: str) -> TdbMetadata:
    _configure_matplotlib_cache()

    from pycalphad import Database

    resolved_path = Path(path).resolve()
    database = Database(str(resolved_path))
    components = tuple(sorted(component for component in map(str, database.elements) if component not in {"/-", "VA"}))
    phases = tuple(sorted(map(str, database.phases.keys())))
    return TdbMetadata(
        path=str(resolved_path),
        database_name=resolved_path.name,
        components=components,
        phases=phases,
    )
