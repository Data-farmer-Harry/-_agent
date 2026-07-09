from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService


class MemoryPromptBudgetTests(unittest.TestCase):
    def test_locked_user_constraint_survives_tiny_prompt_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            locked = service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-budget",
                    item_type="constraint",
                    subject="LAMMPS copper heating run",
                    predicate="target_temperature",
                    value=800,
                    unit="K",
                    text="The user explicitly locked the target temperature at 800 K.",
                    authority="user",
                    source_refs=["user:locked-temperature"],
                    metadata={"locked": True},
                )
            ).item
            background_ids: list[str] = []
            for index in range(8):
                background_ids.append(
                    service.write(
                        MemoryItem(
                            scope_type="conversation",
                            scope_id="conv-budget",
                            item_type="evidence",
                            subject=f"background note {index}",
                            predicate="advice",
                            value=f"Long advisory paragraph {index}",
                            text=("This is verbose background evidence about LAMMPS post-processing and visualization. " * 12),
                            authority="rag",
                            source_refs=[f"rag:background:{index}"],
                        )
                    ).item.memory_id
                )

            result = service.retrieve(
                query="生成 LAMMPS 铜 800 K 加热模拟，并注意后处理。",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-budget", include_global=False),
                top_k=6,
                prompt_budget_bytes=320,
            )

        self.assertIn(locked.memory_id, result.selected_item_ids)
        self.assertEqual(result.forced_retention_ids, [locked.memory_id])
        self.assertTrue(set(result.dropped_item_ids).intersection(background_ids))
        self.assertIn("forced_locked_fact", result.candidates[0].reasons)
        self.assertGreater(result.estimated_after_bytes, result.prompt_budget_bytes)

    def test_top_k_applies_to_non_locked_items_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            locked_a = service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-budget",
                    item_type="constraint",
                    subject="LAMMPS run",
                    predicate="temperature",
                    value=800,
                    unit="K",
                    text="Temperature must be 800 K.",
                    authority="user",
                    source_refs=["user:temperature"],
                )
            ).item
            locked_b = service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-budget",
                    item_type="preference",
                    subject="LAMMPS output",
                    predicate="format",
                    value="include OVITO preview",
                    text="User prefers an OVITO preview.",
                    authority="user",
                    source_refs=["user:ovito"],
                )
            ).item
            fact = service.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="conv-budget",
                    item_type="fact",
                    subject="LAMMPS RDF",
                    predicate="method",
                    value="compute rdf",
                    text="Use compute rdf for radial distribution function analysis.",
                    authority="execution",
                    source_refs=["runtime:rdf"],
                )
            ).item

            result = service.retrieve(
                query="LAMMPS 800 K rdf ovito",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-budget", include_global=False),
                top_k=1,
                prompt_budget_bytes=100_000,
            )

        self.assertEqual(set(result.forced_retention_ids), {locked_a.memory_id, locked_b.memory_id})
        self.assertEqual(set(result.selected_item_ids), {locked_a.memory_id, locked_b.memory_id, fact.memory_id})


if __name__ == "__main__":
    unittest.main()
