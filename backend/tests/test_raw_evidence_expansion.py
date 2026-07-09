from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.shared_memory import MemoryItem, MemoryScope, RawEvidence, SharedMemoryService


def _memory(**overrides) -> MemoryItem:
    payload = {
        "scope_type": "conversation",
        "scope_id": "raw-evidence",
        "item_type": "evidence",
        "subject": "materials_rag:lammps.compute.rdf",
        "predicate": "supports_query",
        "value": {"title": "LAMMPS compute rdf"},
        "text": "Use compute rdf to calculate radial distribution functions from LAMMPS trajectories.",
        "authority": "rag",
        "source_refs": ["https://docs.lammps.org/compute_rdf.html"],
        "metadata": {"stage": "unit_test_rag", "rank": 1},
    }
    payload.update(overrides)
    return MemoryItem.model_validate(payload)


class RawEvidenceExpansionTests(unittest.TestCase):
    def test_write_item_creates_expandable_inline_raw_evidence_with_verified_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            written = service.write(_memory())
            expanded = service.expand_evidence([written.item.memory_id])

        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0].memory_id, written.item.memory_id)
        self.assertEqual(expanded[0].source_type, "rag_document")
        self.assertIn("compute rdf", expanded[0].excerpt)
        self.assertTrue(expanded[0].full_content_inline)
        self.assertTrue(expanded[0].hash_verified)
        self.assertEqual(expanded[0].verification_error, "")
        self.assertIn(expanded[0].evidence_id, written.item.metadata["raw_evidence_ids"])

    def test_expand_accepts_raw_evidence_id_and_memory_id_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            written = service.write(_memory())
            raw_id = written.item.metadata["raw_evidence_ids"][0]
            expanded = service.expand_evidence([raw_id, written.item.memory_id])

        self.assertEqual([item.evidence_id for item in expanded], [raw_id])
        self.assertTrue(expanded[0].hash_verified)

    def test_file_backed_raw_evidence_reports_hash_mismatch_after_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_file = root / "run.log"
            source_file.write_text("LAMMPS run completed\nStep Temp\n0 300\n", encoding="utf-8")
            service = SharedMemoryService(root_dir=root)
            written = service.write(
                _memory(
                    item_type="fact",
                    subject="LAMMPS run",
                    predicate="log_excerpt",
                    text="LAMMPS run completed",
                    authority="execution",
                    source_refs=[str(source_file)],
                )
            )
            raw = service.write_raw_evidence(
                RawEvidence(
                    memory_id=written.item.memory_id,
                    source_type="log",
                    source_ref="run.log:1-3",
                    path_or_url=str(source_file),
                    excerpt="LAMMPS run completed\nStep Temp\n0 300\n",
                    full_content_inline=False,
                )
            )
            self.assertTrue(service.expand_evidence([raw.evidence_id])[0].hash_verified)

            source_file.write_text("LAMMPS run changed\nStep Temp\n0 999\n", encoding="utf-8")
            expanded = service.expand_evidence([raw.evidence_id])

        self.assertEqual(len(expanded), 1)
        self.assertFalse(expanded[0].hash_verified)
        self.assertEqual(expanded[0].verification_error, "content_hash_mismatch")
        self.assertEqual(expanded[0].metadata["verification_error"], "content_hash_mismatch")

    def test_dedup_merge_adds_raw_evidence_for_new_source_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            first = service.write(_memory(source_refs=["rag:first"]))
            second = service.write(_memory(source_refs=["rag:second"]))
            loaded = service.store.get_item(first.item.memory_id)
            expanded = service.expand_evidence([first.item.memory_id])

        self.assertFalse(second.created)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.source_refs, ["rag:first", "rag:second"])
        self.assertEqual(len(expanded), 2)
        self.assertEqual({item.source_ref for item in expanded}, {"rag:first", "rag:second"})
        self.assertTrue(all(item.hash_verified for item in expanded))

    def test_scope_retrieval_items_carry_raw_evidence_ids_for_r3_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            written = service.write(_memory(subject="LAMMPS RDF analysis", source_refs=["rag:rdf"]))
            result = service.retrieve(
                query="rdf",
                scope=MemoryScope(scope_type="conversation", scope_id="raw-evidence", include_global=False),
            )

        retrieved = result.candidates[0].item
        self.assertEqual(retrieved.memory_id, written.item.memory_id)
        self.assertEqual(retrieved.metadata["raw_evidence_ids"], written.item.metadata["raw_evidence_ids"])


if __name__ == "__main__":
    unittest.main()
