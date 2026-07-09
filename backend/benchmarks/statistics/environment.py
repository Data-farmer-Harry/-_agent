from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchmarks.statistics.bootstrap import DEFAULT_BOOTSTRAP_RESAMPLES, DEFAULT_BOOTSTRAP_SEED


def build_statistics_environment_manifest(repo_root: Path | None = None, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[2]
    manifest: dict[str, Any] = {
        "statistics_version": "materials-statistics/v1",
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "default_bootstrap_seed": DEFAULT_BOOTSTRAP_SEED,
        "default_bootstrap_resamples": DEFAULT_BOOTSTRAP_RESAMPLES,
        "git_commit": _git_output(root, ["rev-parse", "HEAD"]),
        "git_branch": _git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": _git_dirty(root),
    }
    if extra:
        manifest["extra"] = dict(extra)
    return manifest


def _git_output(root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_dirty(root: Path) -> bool | None:
    try:
        completed = subprocess.run(["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())
