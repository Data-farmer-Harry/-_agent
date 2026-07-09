from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService


def _fact(scope_id: str, text: str, *, scope_type: str = "conversation", source: str | None = None) -> MemoryItem:
    return MemoryItem(
        scope_type=scope_type,
        scope_id=scope_id,
        item_type="fact",
        subject=scope_id,
        predicate="note",
        value=text,
        text=text,
        normalized_text=text.lower(),
        authority="execution" if scope_type == "run" else "user",
        source_refs=[source or f"{scope_id}:source"],
    )


class MemoryScopeIsolationTests(unittest.TestCase):
    def test_conversation_scope_does_not_leak_other_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_fact("conv-a", "alpha private fact"))
            service.write(_fact("conv-b", "beta private fact"))
            service.write(_fact("global", "gamma global fact", scope_type="global", source="global_doc"))

            no_global = service.retrieve(
                query="alpha beta gamma",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-a", include_global=False),
                top_k=10,
            )
            with_global = service.retrieve(
                query="alpha beta gamma",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-a", include_global=True),
                top_k=10,
            )

        self.assertEqual([candidate.item.scope_id for candidate in no_global.candidates], ["conv-a"])
        self.assertEqual({candidate.item.scope_id for candidate in with_global.candidates}, {"conv-a", "global"})
        self.assertNotIn("conv-b", {candidate.item.scope_id for candidate in with_global.candidates})

    def test_run_scope_can_read_current_run_and_parent_conversation_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_fact("run-1", "run local pressure fact", scope_type="run", source="run.log"))
            service.write(_fact("conv-a", "conversation local temperature fact", source="user_request"))
            service.write(_fact("conv-b", "other conversation should not leak", source="other_user_request"))
            result = service.retrieve(
                query="pressure temperature leak",
                scope=MemoryScope(
                    scope_type="run",
                    scope_id="run-1",
                    conversation_id="conv-a",
                    include_global=False,
                ),
                top_k=10,
            )

        self.assertEqual({candidate.item.scope_id for candidate in result.candidates}, {"run-1", "conv-a"})
        self.assertNotIn("conv-b", {candidate.item.scope_id for candidate in result.candidates})
        self.assertEqual(result.scope_filter, [("run", "run-1"), ("conversation", "conv-a")])


if __name__ == "__main__":
    unittest.main()
