from __future__ import annotations

import json
import unittest

from app.materials_rag.document_store import WIKIPEDIA_DOCUMENTS_PATH, load_materials_rag_documents
from benchmarks.run_rag_recall import WIKIPEDIA_MATERIALS_CASES


class WikipediaMaterialsRagTests(unittest.TestCase):
    def test_wikipedia_corpus_has_sourced_materials_chunks(self) -> None:
        payloads = [json.loads(line) for line in WIKIPEDIA_DOCUMENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = {str(item["id"]) for item in payloads}
        topic_prefixes = {document_id.rsplit(".chunk", 1)[0] for document_id in ids}

        self.assertGreaterEqual(len(payloads), 220)
        self.assertGreaterEqual(len(topic_prefixes), 110)
        self.assertEqual(len(ids), len(payloads))
        self.assertTrue(all(item["source"] == "Wikipedia (English)" for item in payloads))
        self.assertTrue(all(str(item["source_url"]).startswith("https://en.wikipedia.org/wiki/") for item in payloads))
        self.assertTrue(all(item["metadata"]["license"] == "CC BY-SA 4.0" for item in payloads))
        self.assertTrue(all(item["metadata"]["revision_id"] for item in payloads))
        self.assertIn("wikipedia.en.materials-science.chunk1", ids)
        self.assertIn("wikipedia.en.grain-boundary.chunk1", ids)
        self.assertIn("wikipedia.en.precipitation-hardening.chunk1", ids)
        self.assertIn("wikipedia.en.transmission-electron-microscopy.chunk1", ids)
        self.assertIn("wikipedia.en.phase-rule.chunk1", ids)
        self.assertIn("wikipedia.en.martensite.chunk1", ids)
        self.assertIn("wikipedia.en.interatomic-potential.chunk1", ids)
        self.assertIn("wikipedia.en.x-ray-diffraction.chunk1", ids)
        self.assertIn("wikipedia.en.high-entropy-alloy.chunk1", ids)

    def test_document_store_combines_curated_and_wikipedia_corpora(self) -> None:
        documents = load_materials_rag_documents()
        ids = {document.id for document in documents}

        self.assertGreaterEqual(len(documents), 320)
        self.assertIn("lammps.command.fix_nvt", ids)
        self.assertIn("wikipedia.en.materials-science.chunk1", ids)

    def test_wikipedia_benchmark_expected_ids_exist_in_corpus(self) -> None:
        ids = {document.id for document in load_materials_rag_documents()}

        self.assertGreaterEqual(len(WIKIPEDIA_MATERIALS_CASES), 10)
        for case in WIKIPEDIA_MATERIALS_CASES:
            self.assertTrue(set(case.expected).intersection(ids), case.case_id)


if __name__ == "__main__":
    unittest.main()
