from __future__ import annotations

import sys
from pathlib import Path

import conda_pack


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = DESKTOP_ROOT / "resources" / "runtime.tar.gz"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    conda_pack.pack(
        prefix=sys.prefix,
        output=str(OUTPUT),
        format="tar.gz",
        force=True,
        n_threads=-1,
        filters=[
            ("exclude", "**/__pycache__/**"),
            ("exclude", "**/*.pyc"),
            ("exclude", "**/.pytest_cache/**"),
            ("exclude", "**/tests/**"),
        ],
    )
    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"Packed MatterLab runtime: {OUTPUT} ({size_mb:.1f} MiB)")


if __name__ == "__main__":
    main()
