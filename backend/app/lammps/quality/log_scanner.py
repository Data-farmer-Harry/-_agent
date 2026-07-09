from __future__ import annotations

import re
from pathlib import Path


ERROR_MARKERS = (
    "ERROR:",
    "Lost atoms",
    "non-numeric",
    "segmentation fault",
)
NONFINITE_PATTERN = re.compile(r"(^|[^A-Za-z])(nan|inf|-inf|infinity)([^A-Za-z]|$)", re.IGNORECASE)


def scan_lammps_log(log_path: Path) -> list[str]:
    if not log_path.exists():
        return ["LAMMPS run.log is missing."]
    findings: list[str] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        lowered = line.lower()
        if any(marker.lower() in lowered for marker in ERROR_MARKERS) or NONFINITE_PATTERN.search(line):
            text = line.strip()
            if text and text not in findings:
                findings.append(text)
    return findings
