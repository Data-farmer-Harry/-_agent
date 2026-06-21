from __future__ import annotations

import unittest

from app.materials_rag.document_store import load_materials_rag_documents
from app.thermo.registry import load_thermo_database_cards
from benchmarks.run_rag_blind_eval import DEFAULT_DATASET, _load_cases, _ndcg


class RagBlindAssetsTests(unittest.TestCase):
    def test_blind_dataset_is_frozen_unique_and_fully_labeled(self) -> None:
        cases = _load_cases(DEFAULT_DATASET)
        material_ids = {document.id for document in load_materials_rag_documents()}
        thermo_ids = {card.system_name for card in load_thermo_database_cards()}

        self.assertGreaterEqual(len(cases), 200)
        self.assertLessEqual(len(cases), 500)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))
        self.assertEqual(len({case.query.casefold() for case in cases}), len(cases))
        self.assertEqual({case.language for case in cases}, {"zh", "en", "mixed"})
        for case in cases:
            valid_ids = thermo_ids if case.suite == "thermo_blind" else material_ids
            self.assertTrue(set(case.expected).intersection(valid_ids), case.case_id)

    def test_ndcg_rewards_earlier_relevant_documents(self) -> None:
        expected = ("target",)
        self.assertEqual(_ndcg(["target", "other"], expected, 2), 1.0)
        self.assertGreater(_ndcg(["other", "target"], expected, 2), 0.0)
        self.assertLess(_ndcg(["other", "target"], expected, 2), 1.0)
        self.assertEqual(_ndcg(["other"], expected, 1), 0.0)


if __name__ == "__main__":
    unittest.main()
