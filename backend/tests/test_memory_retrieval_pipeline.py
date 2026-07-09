from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.rag.sqlite_vector_store import SqliteVectorStore
from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService


def _memory(
    *,
    scope_id: str = "conv-rag",
    item_type: str = "fact",
    subject: str,
    predicate: str,
    text: str,
    value: object | None = None,
    authority: str = "execution",
    source: str = "runtime",
    metadata: dict | None = None,
) -> MemoryItem:
    return MemoryItem(
        scope_type="conversation",
        scope_id=scope_id,
        item_type=item_type,
        subject=subject,
        predicate=predicate,
        value=value if value is not None else text,
        text=text,
        authority=authority,
        source_refs=[source],
        metadata=metadata or {},
    )


class MemoryRetrievalPipelineTests(unittest.TestCase):
    def test_query_rewrite_and_bm25_recall_lammps_analysis_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            rdf = service.write(
                _memory(
                    subject="LAMMPS RDF analysis",
                    predicate="method",
                    text="Use compute rdf and radial distribution function to inspect local structure after equilibration.",
                    source="rag:lammps.rdf",
                )
            ).item
            msd = service.write(
                _memory(
                    subject="LAMMPS diffusion analysis",
                    predicate="method",
                    text="Use compute msd and mean squared displacement to estimate diffusion.",
                    source="rag:lammps.msd",
                )
            ).item
            service.write(
                _memory(
                    subject="Thermo database registry",
                    predicate="phase_diagram",
                    text="Use pycalphad TDB cards for phase diagram calculations.",
                    source="registry:thermo",
                )
            )

            result = service.retrieve(
                query="LAMMPS 里怎么分析 rdf 和 msd？",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False),
                top_k=2,
            )

        self.assertIn(rdf.memory_id, result.selected_item_ids)
        self.assertIn(msd.memory_id, result.selected_item_ids)
        self.assertIn("radial distribution function", " ".join(result.expansion_terms))
        expected_backend = (
            "metadata_bm25_sqlite_vec_dense_cache_r2_mmr_textrank_r3"
            if SqliteVectorStore.extension_available()
            else "metadata_bm25_persistent_dense_cache_r2_mmr_textrank_r3"
        )
        self.assertEqual(result.retrieval_backend, expected_backend)
        self.assertTrue(rdf.embedding_id.startswith("emb-"))
        self.assertGreater(result.estimated_before_bytes, 0)
        self.assertGreater(result.estimated_after_bytes, 0)
        dense_reason_prefix = "r1_sqlite_vec" if SqliteVectorStore.extension_available() else "r1_dense_fallback"
        self.assertTrue(any(dense_reason_prefix in reason for candidate in result.candidates for reason in candidate.reasons))

    def test_persistent_embedding_cache_reuses_vectors_across_service_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False)
            service = SharedMemoryService(root_dir=root)
            written = service.write(
                _memory(
                    subject="LAMMPS NVT thermostat",
                    predicate="method",
                    text="Use fix nvt temp 800 800 0.1 for a stable LAMMPS heating workflow.",
                    source="execution:lammps:nvt",
                )
            ).item

            first = service.retrieve(query="LAMMPS nvt heating thermostat", scope=scope, top_k=1)
            first_stats = service.store.embedding_cache_stats()
            second = service.retrieve(query="LAMMPS nvt heating thermostat", scope=scope, top_k=1)
            second_stats = service.store.embedding_cache_stats()
            reopened = SharedMemoryService(root_dir=root)
            third = reopened.retrieve(query="LAMMPS nvt heating thermostat", scope=scope, top_k=1)
            third_stats = reopened.store.embedding_cache_stats()

        self.assertEqual(first.selected_item_ids, [written.memory_id])
        self.assertEqual(second.selected_item_ids, [written.memory_id])
        self.assertEqual(third.selected_item_ids, [written.memory_id])
        self.assertGreaterEqual(first_stats["row_count"], 2)
        self.assertEqual(first_stats["row_count"], second_stats["row_count"])
        self.assertEqual(second_stats["row_count"], third_stats["row_count"])
        self.assertGreater(second_stats["total_use_count"], first_stats["total_use_count"])
        self.assertGreater(third_stats["total_use_count"], second_stats["total_use_count"])
        first_cache_reason = "embedding_cache:item_hit" if SqliteVectorStore.extension_available() else "embedding_cache:item_miss"
        self.assertTrue(any(first_cache_reason in candidate.reasons for candidate in first.candidates))
        self.assertTrue(any("embedding_cache:item_hit" in candidate.reasons for candidate in second.candidates))
        self.assertTrue(any("embedding_cache:item_hit" in candidate.reasons for candidate in third.candidates))

    def test_sqlite_vec_dense_index_is_used_when_available(self) -> None:
        if not SqliteVectorStore.extension_available():
            self.skipTest("sqlite-vec extension is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = SharedMemoryService(root_dir=root)
            scope = MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False)
            target = service.write(
                _memory(
                    subject="LAMMPS thermostat guidance",
                    predicate="method",
                    text="Use fix nvt temp and a damping parameter to control the target temperature in LAMMPS.",
                    source="rag:lammps.fix_nvt",
                )
            ).item
            service.write(
                _memory(
                    subject="CALPHAD phase diagram note",
                    predicate="method",
                    text="Use pycalphad and a TDB database for equilibrium phase diagram calculations.",
                    source="rag:thermo.pycalphad",
                )
            )

            first = service.retrieve(query="LAMMPS fix nvt thermostat temperature control", scope=scope, top_k=1)
            second = service.retrieve(query="LAMMPS fix nvt thermostat temperature control", scope=scope, top_k=1)
            inventory = SqliteVectorStore(root / "shared_memory_vectors.sqlite3").inventory()

        self.assertEqual(first.selected_item_ids, [target.memory_id])
        self.assertEqual(second.selected_item_ids, [target.memory_id])
        self.assertEqual(first.retrieval_backend, "metadata_bm25_sqlite_vec_dense_cache_r2_mmr_textrank_r3")
        self.assertTrue(any(reason.startswith("r1_sqlite_vec:") for candidate in first.candidates for reason in candidate.reasons))
        self.assertEqual(inventory["backend"], "sqlite_vec")
        self.assertTrue(any(collection["collection"].startswith("shared_memory:") for collection in inventory["collections"]))

    def test_r2_mmr_diversifies_near_duplicate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            rdf_primary = service.write(
                _memory(
                    subject="LAMMPS RDF primary note",
                    predicate="method",
                    text=(
                        "LAMMPS compute rdf radial distribution function local structure coordination shell "
                        "pair correlation histogram analysis after equilibration."
                    ),
                    source="rag:rdf:primary",
                )
            ).item
            rdf_duplicate = service.write(
                _memory(
                    subject="LAMMPS RDF duplicate note",
                    predicate="method",
                    text=(
                        "LAMMPS compute rdf radial distribution function local structure coordination shell "
                        "pair correlation histogram background reference."
                    ),
                    source="rag:rdf:duplicate",
                )
            ).item
            msd = service.write(
                _memory(
                    subject="LAMMPS MSD diffusion note",
                    predicate="method",
                    text=(
                        "LAMMPS compute msd mean squared displacement diffusion coefficient atom mobility "
                        "trajectory analysis after equilibration."
                    ),
                    source="rag:msd",
                )
            ).item

            result = service.retrieve(
                query="LAMMPS rdf msd diffusion local structure analysis",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False),
                top_k=2,
                prompt_budget_bytes=100_000,
            )

        self.assertIn(msd.memory_id, result.selected_item_ids)
        self.assertEqual(len(set(result.selected_item_ids).intersection({rdf_primary.memory_id, rdf_duplicate.memory_id})), 1)
        self.assertTrue(any(reason.startswith("r2_mmr:") for candidate in result.candidates for reason in candidate.reasons))

    def test_r2_textrank_compresses_long_rag_text_and_preserves_l3_pointer(self) -> None:
        long_text = " ".join(
            [
                "LAMMPS compute rdf calculates radial distribution functions for selected atom groups after a run.",
                "The command bins pair distances and reports coordination-like structural signals.",
                "For metallic systems, the first RDF peak often describes the nearest-neighbour shell.",
                "The analysis should be performed after equilibration so transient heating artifacts are reduced.",
                "A dump trajectory can be post-processed, but in-script computes are useful for repeatable workflows.",
                "Users should choose bin counts and cutoff distances that match the box size and material density.",
                "This background sentence intentionally adds length without adding a new protected script token.",
            ]
            * 4
        )
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            written = service.write(
                _memory(
                    item_type="evidence",
                    subject="LAMMPS RDF documentation",
                    predicate="supports_query",
                    text=long_text,
                    authority="rag",
                    source="https://docs.lammps.org/compute_rdf.html",
                )
            ).item

            result = service.retrieve(
                query="How should I use LAMMPS compute rdf after equilibration?",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False),
                top_k=1,
                prompt_budget_bytes=100_000,
            )

        candidate = result.candidates[0]
        compression = candidate.item.metadata["context_compression"]
        self.assertEqual(candidate.item.memory_id, written.memory_id)
        self.assertLess(len(candidate.item.text), len(long_text))
        self.assertTrue(candidate.item.text.startswith("L2 summary:"))
        self.assertEqual(compression["method"], "textrank_v1")
        self.assertFalse(compression["protected"])
        self.assertEqual(compression["l3"]["raw_evidence_ids"], written.metadata["raw_evidence_ids"])
        self.assertIn("r2_textrank_compressed", candidate.reasons)

    def test_r2_protects_lammps_input_scripts_from_compression(self) -> None:
        script = "\n".join(
            [
                "units metal",
                "atom_style atomic",
                "boundary p p p",
                "read_data data.cu",
                "pair_style eam",
                "pair_coeff * * Cu_u3.eam",
                "velocity all create 800.0 12345",
                "fix 1 all nvt temp 800.0 800.0 0.1",
                "thermo 100",
                "run 10000",
            ]
            * 4
        )
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            written = service.write(
                _memory(
                    item_type="evidence",
                    subject="LAMMPS input script",
                    predicate="generated_script",
                    text=script,
                    authority="execution",
                    source="input.in",
                )
            ).item

            result = service.retrieve(
                query="LAMMPS pair_style nvt run script",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False),
                top_k=1,
                prompt_budget_bytes=100_000,
            )

        candidate = result.candidates[0]
        compression = candidate.item.metadata["context_compression"]
        self.assertEqual(candidate.item.memory_id, written.memory_id)
        self.assertEqual(candidate.item.text, script)
        self.assertEqual(compression["method"], "preserve_original")
        self.assertTrue(compression["protected"])
        self.assertIn(compression["reason"], {"source_non_compressible", "lammps_script"})
        self.assertNotIn("L2 summary:", candidate.item.text)

    def test_scope_hard_filter_prevents_rewrite_from_leaking_other_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            own = service.write(
                _memory(
                    scope_id="conv-a",
                    subject="own LAMMPS run",
                    predicate="target",
                    text="This conversation uses RDF analysis.",
                    source="conv-a",
                )
            ).item
            service.write(
                _memory(
                    scope_id="conv-b",
                    subject="other LAMMPS run",
                    predicate="target",
                    text="Other conversation has a more relevant RDF and MSD plan.",
                    source="conv-b",
                )
            )

            result = service.retrieve(
                query="rdf msd lammps",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-a", include_global=False),
                top_k=5,
            )

        self.assertEqual(result.selected_item_ids, [own.memory_id])
        self.assertEqual(result.scope_filter, [("conversation", "conv-a")])

    def test_working_state_exports_l1_l2_l3_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SharedMemoryService(root_dir=Path(tmp))
            locked = service.write(
                _memory(
                    item_type="constraint",
                    subject="Cu heating run",
                    predicate="target_temperature",
                    value=800,
                    text="User locked target temperature at 800 K.",
                    authority="user",
                    source="user:locked-temp",
                    metadata={"locked": True},
                )
            ).item
            evidence = service.write(
                _memory(
                    item_type="evidence",
                    subject="LAMMPS RDF documentation",
                    predicate="supports_query",
                    text="LAMMPS compute rdf returns radial distribution function evidence.",
                    authority="rag",
                    source="https://docs.lammps.org/compute_rdf.html",
                )
            ).item

            retrieval = service.retrieve(
                query="Cu 800K LAMMPS rdf",
                scope=MemoryScope(scope_type="conversation", scope_id="conv-rag", include_global=False),
                top_k=3,
            )
            working_state = service.build_working_state(retrieval, run_id="run-1", conversation_id="conv-rag")

        self.assertEqual(working_state.schema_version, "shared-memory-working-state/v1")
        self.assertEqual(working_state.run_id, "run-1")
        self.assertIn(locked.memory_id, [fact["memory_id"] for fact in working_state.locked_facts])
        digest_by_memory = {digest.memory_id: digest for digest in working_state.evidence_digests}
        self.assertIn(evidence.memory_id, digest_by_memory)
        self.assertTrue(digest_by_memory[evidence.memory_id].l3_raw_evidence_ids)
        self.assertTrue(set(working_state.raw_evidence_ids) >= set(digest_by_memory[evidence.memory_id].l3_raw_evidence_ids))


if __name__ == "__main__":
    unittest.main()
