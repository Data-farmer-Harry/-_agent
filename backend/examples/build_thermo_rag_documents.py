from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.thermo.rag_documents import write_thermo_rag_documents  # noqa: E402
from app.thermo.registry import load_thermo_database_cards  # noqa: E402


def main() -> int:
    output_path = PROJECT_ROOT / "configs" / "thermo_rag_documents.example.jsonl"
    count = write_thermo_rag_documents(output_path, load_thermo_database_cards())
    print(f"Wrote {count} thermo RAG documents to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
