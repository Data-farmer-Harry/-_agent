from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService


def _constraint(
    *,
    value,
    unit: str,
    source: str,
    polarity: str = "positive",
    authority: str = "user",
    metadata: dict | None = None,
) -> MemoryItem:
    return MemoryItem(
        scope_type="conversation",
        scope_id="conv-conflict",
        item_type="constraint",
        subject="Copper heating run",
        predicate="target_temperature",
        value=value,
        unit=unit,
        text=f"Target temperature is {value} {unit}.",
        authority=authority,
        polarity=polarity,
        source_refs=[source],
        metadata=metadata or {},
    )


def _fact(
    *,
    subject: str,
    predicate: str,
    value,
    text: str,
    source: str,
    authority: str = "execution",
    metadata: dict | None = None,
) -> MemoryItem:
    return MemoryItem(
        scope_type="conversation",
        scope_id="conv-conflict",
        item_type="fact",
        subject=subject,
        predicate=predicate,
        value=value,
        text=text,
        authority=authority,
        source_refs=[source],
        metadata=metadata or {},
    )


class MemoryContradictionTests(unittest.TestCase):
    def test_same_key_different_value_records_structured_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_constraint(value=800, unit="K", source="first_user_request"))
            second = service.write(_constraint(value=900, unit="K", source="second_user_request"))
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "value")
        self.assertEqual(conflict.detection_mode, "structured")
        self.assertEqual(conflict.status, "needs_user")
        self.assertEqual(second.item.status, "active")
        self.assertEqual(conflict.evidence_refs, ["first_user_request", "second_user_request"])

    def test_same_key_nonconvertible_unit_records_unit_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_constraint(value=800, unit="K", source="temperature_request"))
            second = service.write(_constraint(value=800, unit="eV", source="bad_parser"))
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "unit")

    def test_same_value_opposite_polarity_records_polarity_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_constraint(value=800, unit="K", source="positive_request", polarity="positive"))
            second = service.write(_constraint(value=800, unit="K", source="negative_request", polarity="negative"))
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "polarity")

    def test_superseding_active_memory_records_version_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            first = service.write(
                _fact(
                    subject="LAMMPS potential selection",
                    predicate="versioned_choice",
                    value="Cu_u3.eam",
                    text="Version 1 selected Cu_u3.eam.",
                    source="planner:v1",
                    metadata={"version": 1},
                )
            ).item
            second = service.write(
                _fact(
                    subject="LAMMPS potential selection",
                    predicate="versioned_choice",
                    value="Cu_mishin.eam",
                    text="Version 2 supersedes the previous potential choice.",
                    source="planner:v2",
                    metadata={"version": 2, "supersedes_memory_id": first.memory_id},
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "version")
        self.assertEqual(conflict.detection_mode, "structured")
        self.assertEqual(conflict.metadata["reason"], "incoming_supersedes_existing_but_existing_still_active")

    def test_domain_antonym_records_heuristic_polarity_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                _fact(
                    subject="LAMMPS run artifact",
                    predicate="origin",
                    value="real",
                    text="LAMMPS run artifact origin is real execution output.",
                    source="quality:real",
                    authority="execution",
                )
            )
            second = service.write(
                _fact(
                    subject="LAMMPS run artifact",
                    predicate="origin",
                    value="synthetic",
                    text="LAMMPS run artifact origin is synthetic mock fallback output.",
                    source="llm:synthetic",
                    authority="llm_inference",
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "polarity")
        self.assertEqual(conflict.detection_mode, "heuristic")
        self.assertEqual(conflict.metadata["reason"], "domain_antonym_pair")

    def test_negation_flip_records_heuristic_polarity_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                _fact(
                    subject="LAMMPS dump output",
                    predicate="availability",
                    value="present",
                    text="LAMMPS dump output is present for OVITO inspection.",
                    source="artifact:dump",
                    authority="execution",
                )
            )
            second = service.write(
                _fact(
                    subject="LAMMPS dump output",
                    predicate="availability",
                    value="unavailable",
                    text="LAMMPS dump output is not available for OVITO inspection.",
                    source="llm:guess",
                    authority="llm_inference",
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "polarity")
        self.assertEqual(conflict.detection_mode, "heuristic")
        self.assertEqual(conflict.metadata["reason"], "negation_flip")

    def test_different_explicit_conditions_record_context_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                _fact(
                    subject="LAMMPS diffusion coefficient",
                    predicate="measured_value",
                    value="1.0e-9",
                    text="Diffusion coefficient measured at 800 K.",
                    source="run:800K",
                    metadata={"target_temperature": 800, "material": "Cu"},
                )
            )
            second = service.write(
                _fact(
                    subject="LAMMPS diffusion coefficient",
                    predicate="measured_value",
                    value="2.0e-9",
                    text="Diffusion coefficient measured at 900 K.",
                    source="run:900K",
                    metadata={"target_temperature": 900, "material": "Cu"},
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.conflict_type, "context")
        self.assertEqual(conflict.detection_mode, "structured")
        self.assertEqual(conflict.metadata["reason"], "context_signature_mismatch:target_temperature")

    def test_authority_resolution_hint_prefers_higher_authority_without_auto_resolving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            registry = service.write(
                _fact(
                    subject="LAMMPS potential source",
                    predicate="validated",
                    value="true",
                    text="Registry validator says the potential source is supported.",
                    source="registry:potential",
                    authority="registry",
                )
            ).item
            second = service.write(
                _fact(
                    subject="LAMMPS potential source",
                    predicate="validated",
                    value="false",
                    text="LLM inference says the potential source is unsupported.",
                    source="llm:potential",
                    authority="llm_inference",
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])
            quarantined = service.store.get_item(second.item.memory_id)
            retrieval = service.retrieve(
                query="LAMMPS potential source unsupported",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-conflict", include_global=False),
                top_k=5,
            )

        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.status, "open")
        self.assertEqual(conflict.metadata["resolution_hint"]["strategy"], "authority")
        self.assertEqual(conflict.metadata["resolution_hint"]["action"], "prefer_higher_authority")
        self.assertEqual(conflict.metadata["resolution_hint"]["winner_memory_id"], registry.memory_id)
        self.assertEqual(second.item.status, "quarantined")
        self.assertIsNotNone(quarantined)
        assert quarantined is not None
        self.assertEqual(quarantined.status, "quarantined")
        self.assertNotIn(second.item.memory_id, retrieval.selected_item_ids)

    def test_user_resolution_hint_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_constraint(value=800, unit="K", source="user:locked", polarity="positive"))
            second = service.write(_constraint(value=900, unit="K", source="llm:proposal", polarity="positive"))
            conflict = service.store.get_conflict(second.conflict_ids[0])
            conflicted = service.store.get_item(second.item.memory_id)

        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.status, "needs_user")
        self.assertEqual(second.item.status, "active")
        self.assertIsNotNone(conflicted)
        assert conflicted is not None
        self.assertEqual(conflicted.status, "active")
        self.assertEqual(conflict.metadata["resolution_hint"]["strategy"], "user_confirmation")
        self.assertEqual(conflict.metadata["resolution_hint"]["action"], "needs_user_confirmation")

    def test_locked_user_conflict_marks_incoming_memory_conflicted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                _constraint(
                    value=800,
                    unit="K",
                    source="user:locked",
                    metadata={"locked": True},
                )
            )
            second = service.write(
                _constraint(
                    value=900,
                    unit="K",
                    source="llm:proposal",
                    authority="llm_inference",
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])
            loaded = service.store.get_item(second.item.memory_id)

        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.status, "needs_user")
        self.assertEqual(conflict.metadata["resolution_hint"]["rationale"], "locked_memory_involved")
        self.assertEqual(second.item.status, "conflicted")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "conflicted")

    def test_semantic_candidate_records_possible_conflict_without_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(
                _fact(
                    subject="LAMMPS potential support",
                    predicate="validated",
                    value="supported",
                    text="Registry says this LAMMPS potential support status is supported for copper.",
                    source="registry:potential-support",
                    authority="registry",
                )
            )
            second = service.write(
                _fact(
                    subject="LAMMPS potential support",
                    predicate="status",
                    value="unsupported",
                    text="RAG note says this LAMMPS potential support status is unsupported for copper.",
                    source="rag:potential-support",
                    authority="rag",
                )
            )
            conflict = service.store.get_conflict(second.conflict_ids[0])
            loaded = service.store.get_item(second.item.memory_id)

        self.assertEqual(len(second.conflict_ids), 1)
        self.assertIsNotNone(conflict)
        assert conflict is not None
        self.assertEqual(conflict.detection_mode, "semantic_candidate")
        self.assertEqual(conflict.conflict_type, "polarity")
        self.assertEqual(conflict.status, "open")
        self.assertTrue(conflict.metadata["semantic_candidate"])
        self.assertEqual(second.item.status, "active")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "active")

    def test_different_scope_does_not_record_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            service.write(_constraint(value=800, unit="K", source="conv_a_request"))
            item = _constraint(value=900, unit="K", source="conv_b_request")
            item = item.model_copy(update={"scope_id": "conv-other"})
            second = service.write(item)

        self.assertEqual(second.conflict_ids, [])


if __name__ == "__main__":
    unittest.main()
