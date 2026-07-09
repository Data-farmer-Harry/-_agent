from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService


def _temperature_item(*, value, text: str, normalized_text: str, source: str, scope_id: str = "conv-dedup") -> MemoryItem:
    return MemoryItem(
        scope_type="conversation",
        scope_id=scope_id,
        item_type="constraint",
        subject="Cu heating run",
        predicate="target_temperature",
        value=value,
        unit="K",
        text=text,
        normalized_text=normalized_text,
        authority="user",
        polarity="positive",
        source_refs=[source],
    )


class MemoryDedupTests(unittest.TestCase):
    def test_normalized_duplicate_merges_without_overwriting_original_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            first = service.write(
                _temperature_item(
                    value="800K",
                    text="Target temperature is 800K.",
                    normalized_text="target_temperature=800 k",
                    source="user_request",
                )
            )
            second = service.write(
                _temperature_item(
                    value="800 K",
                    text="Target temperature is 800 K.",
                    normalized_text="target_temperature=800 k",
                    source="structured_parser",
                )
            )
            items = service.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="conv-dedup", include_global=False),
                item_types=["constraint"],
            )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertTrue(second.deduplicated)
        self.assertEqual(second.dedup_level, "normalized")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].text, "Target temperature is 800K.")
        self.assertEqual(items[0].source_refs, ["user_request", "structured_parser"])

    def test_different_value_is_not_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                _temperature_item(
                    value=800,
                    text="Target temperature is 800 K.",
                    normalized_text="target_temperature=800 k",
                    source="user_request",
                )
            )
            service.write(
                _temperature_item(
                    value=900,
                    text="Target temperature is 900 K.",
                    normalized_text="target_temperature=900 k",
                    source="new_user_request",
                )
            )
            items = service.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="conv-dedup", include_global=False),
                item_types=["constraint"],
            )

        self.assertEqual(len(items), 2)
        self.assertEqual({item.value for item in items}, {800, 900})

    def test_temperature_unit_and_material_alias_canonicalization_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            first = service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-dedup",
                    item_type="constraint",
                    subject="Copper heating run",
                    predicate="target_temperature",
                    value="800K",
                    unit="",
                    text="Heat copper to 800K.",
                    authority="user",
                    polarity="positive",
                    source_refs=["user_request"],
                )
            )
            second = service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-dedup",
                    item_type="constraint",
                    subject="Cu heating run",
                    predicate="target_temperature",
                    value=526.85,
                    unit="°C",
                    text="Target temperature is 526.85 °C.",
                    authority="user",
                    polarity="positive",
                    source_refs=["unit_parser"],
                )
            )
            items = service.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="conv-dedup", include_global=False),
                item_types=["constraint"],
            )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.dedup_level, "normalized")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].normalized_text, "cu heating run|target_temperature|800|k")
        self.assertEqual(items[0].source_refs, ["user_request", "unit_parser"])

    def test_convertible_units_with_different_values_are_not_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-dedup",
                    item_type="constraint",
                    subject="Copper heating run",
                    predicate="target_temperature",
                    value=800,
                    unit="K",
                    text="Target temperature is 800 K.",
                    authority="user",
                    polarity="positive",
                    source_refs=["user_request"],
                )
            )
            service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-dedup",
                    item_type="constraint",
                    subject="Cu heating run",
                    predicate="target_temperature",
                    value=527,
                    unit="°C",
                    text="Target temperature is 527 °C.",
                    authority="user",
                    polarity="positive",
                    source_refs=["unit_parser"],
                )
            )
            items = service.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="conv-dedup", include_global=False),
                item_types=["constraint"],
            )

        self.assertEqual(len(items), 2)

    def test_identical_content_in_different_scope_is_not_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            first = service.write(
                _temperature_item(
                    value=800,
                    text="Target temperature is 800 K.",
                    normalized_text="target_temperature=800 k",
                    source="conv_a_request",
                    scope_id="conv-a",
                )
            )
            second = service.write(
                _temperature_item(
                    value=800,
                    text="Target temperature is 800 K.",
                    normalized_text="target_temperature=800 k",
                    source="conv_b_request",
                    scope_id="conv-b",
                )
            )

        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertNotEqual(first.item.memory_id, second.item.memory_id)


if __name__ == "__main__":
    unittest.main()
