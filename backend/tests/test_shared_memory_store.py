from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.rag.sqlite_vector_store import SqliteVectorStore
from app.shared_memory import ConflictRecord, ConflictResolution, MemoryItem, MemoryScope, SharedMemoryService, SharedMemoryStore


def _item(**overrides) -> MemoryItem:
    payload = {
        "scope_type": "conversation",
        "scope_id": "conv-a",
        "item_type": "constraint",
        "subject": "Cu heating run",
        "predicate": "target_temperature",
        "value": 800,
        "unit": "K",
        "text": "Target temperature is 800 K.",
        "authority": "user",
        "polarity": "positive",
        "source_refs": ["user_request"],
    }
    payload.update(overrides)
    return MemoryItem.model_validate(payload)


class SharedMemoryStoreTests(unittest.TestCase):
    def test_migration_is_idempotent_and_preserves_existing_memory_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = SharedMemoryStore(root_dir=root)
            first.write_item(_item())
            second = SharedMemoryStore(root_dir=root)

            loaded = second.list_items(scope=MemoryScope(scope_type="conversation", scope_id="conv-a", include_global=False))

        self.assertEqual(len(loaded), 1)
        self.assertTrue(loaded[0].content_hash)
        self.assertTrue(loaded[0].normalized_hash)

    def test_write_deduplicates_exact_content_and_merges_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedMemoryStore(root_dir=Path(tmp))
            first = store.write_item(_item(source_refs=["user_request"]))
            second = store.write_item(_item(source_refs=["validated_request"]))
            loaded = store.get_item(first.item.memory_id)
            versions = store.version_history(first.item.memory_id)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertTrue(second.deduplicated)
        self.assertEqual(second.dedup_level, "exact")
        self.assertEqual(second.duplicate_of, first.item.memory_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.source_refs, ["user_request", "validated_request"])
        self.assertEqual([item["event_type"] for item in versions], ["insert", "dedup_exact"])

    def test_status_changes_append_version_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedMemoryStore(root_dir=Path(tmp))
            written = store.write_item(_item())
            updated = store.update_status(written.item.memory_id, "superseded", reason="newer execution fact")
            versions = store.version_history(written.item.memory_id)

        self.assertEqual(updated.status, "superseded")
        self.assertEqual([item["event_type"] for item in versions], ["insert", "status_change"])
        self.assertEqual(versions[-1]["reason"], "newer execution fact")

    def test_conflict_records_and_resolutions_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedMemoryStore(root_dir=Path(tmp))
            left = store.write_item(_item(value=800, normalized_text="target_temperature=800 k"))
            right = store.write_item(
                _item(
                    value=900,
                    text="Target temperature is 900 K.",
                    normalized_text="target_temperature=900 k",
                    source_refs=["runtime_request"],
                )
            )
            conflict = store.record_conflict(
                ConflictRecord(
                    left_memory_id=left.item.memory_id,
                    right_memory_id=right.item.memory_id,
                    conflict_type="value",
                    detection_mode="structured",
                    evidence_refs=["user_request", "runtime_request"],
                )
            )
            resolved = store.resolve_conflict(
                conflict.conflict_id,
                ConflictResolution(
                    resolver="unit_test",
                    decision="keep_user_constraint",
                    reason="user authority wins",
                    evidence_refs=["user_request"],
                ),
            )

        self.assertEqual(conflict.status, "open")
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.resolution["decision"], "keep_user_constraint")

    def test_service_profile_reports_deterministic_first_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            profile = service.profile()

        self.assertEqual(profile["module"], "SharedMemoryService")
        self.assertFalse(profile["embedding_enabled"])
        self.assertTrue(profile["embedding_cache_enabled"])
        self.assertEqual(profile["embedding_cache"]["row_count"], 0)
        self.assertEqual(profile["sqlite_vec_enabled"], SqliteVectorStore.extension_available())
        self.assertTrue(profile["sqlite_vec_path"].endswith("shared_memory_vectors.sqlite3"))


if __name__ == "__main__":
    unittest.main()
