import threading

_CANCELLED_RUNS = set()
_LOCK = threading.Lock()

class SimulationCancelledError(Exception):
    """Raised when a simulation run is cancelled by the user."""
    pass

def cancel_run(run_id: str) -> None:
    """Mark a run_id as cancelled."""
    with _LOCK:
        _CANCELLED_RUNS.add(run_id)

def is_cancelled(run_id: str) -> bool:
    """Check if a run_id has been cancelled."""
    with _LOCK:
        return run_id in _CANCELLED_RUNS

def clear_cancellation(run_id: str) -> None:
    """Clear the cancellation status for a run_id (useful for testing or cleanup)."""
    with _LOCK:
        _CANCELLED_RUNS.discard(run_id)
