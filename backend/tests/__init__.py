"""Backend test package for the phase diagram agent."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


def _bootstrap_runtime_module() -> None:
    module_name = "app.services.agent_runtime"
    if module_name in sys.modules:
        return

    try:
        importlib.import_module(module_name)
        return
    except ModuleNotFoundError:
        pass

    project_root = Path(__file__).resolve().parents[1]
    pyc_candidates = sorted((project_root / "app" / "services" / "__pycache__").glob("agent_runtime*.pyc"))
    if not pyc_candidates:
        return

    spec = importlib.util.spec_from_file_location(module_name, pyc_candidates[0])
    if spec is None or spec.loader is None:
        return

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


_bootstrap_runtime_module()
