from __future__ import annotations

from threading import Lock


class RunCancelledError(RuntimeError):
    """Raised when an interactive compute run is cancelled by the user."""


_CANCELLED_RUNS: set[str] = set()
_LOCK = Lock()


def cancel_run(run_id: str) -> None:
    with _LOCK:
        _CANCELLED_RUNS.add(run_id)


def is_cancelled(run_id: str | None) -> bool:
    if not run_id:
        return False
    with _LOCK:
        return run_id in _CANCELLED_RUNS


def clear_cancellation(run_id: str | None) -> None:
    if not run_id:
        return
    with _LOCK:
        _CANCELLED_RUNS.discard(run_id)
