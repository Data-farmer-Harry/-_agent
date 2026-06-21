from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.thermo.rag_documents import build_thermo_rag_documents, write_thermo_rag_documents
from app.thermo.registry import load_thermo_database_cards


class ThermoRagDocumentTests(unittest.TestCase):
    def test_build_rag_documents_covers_every_registry_system(self) -> None:
        cards = load_thermo_database_cards()
        documents = build_thermo_rag_documents(cards)
        system_card_names = {
            item["system_name"]
            for item in documents
            if item["doc_type"] == "system_card"
        }
        self.assertEqual(system_card_names, {card.system_name for card in cards})

    def test_write_rag_documents_outputs_jsonl(self) -> None:
        cards = load_thermo_database_cards()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "thermo_rag_documents.jsonl"
            count = write_thermo_rag_documents(output_path, cards)
            lines = [line for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(count, len(lines))
        parsed = [json.loads(line) for line in lines]
        self.assertGreaterEqual(len(parsed), len(cards) * 4)
        self.assertIn("system_card", {item["doc_type"] for item in parsed})
        self.assertIn("phase_card", {item["doc_type"] for item in parsed})
        self.assertIn("provenance_card", {item["doc_type"] for item in parsed})
        self.assertIn("tdb_chunk", {item["doc_type"] for item in parsed})
