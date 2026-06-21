from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.memory import MemoryStore
from app.state import ConversationTurn, LastRunContext, LongTermMemorySnapshot, RecognitionResult
from tests.support import build_request


class MemoryStoreTests(unittest.TestCase):
    def test_build_next_snapshot_enriches_session_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            snapshot = store.build_next_snapshot(
                conversation_id="session-1",
                messages=[
                    ConversationTurn(role="user", content="请生成一张 Al-Zn 二元相图，温度范围 300K-1000K。"),
                    ConversationTurn(role="assistant", content="好的，我会开始计算。"),
                ],
                uploaded_assets=[],
                recognition_result=RecognitionResult(
                    system="Al-Zn",
                    diagram_type="binary",
                    phases=["LIQUID", "FCC_A1", "HCP_A3"],
                    confidence=0.91,
                    source="llm_recognition_agent",
                ),
                last_run_context=LastRunContext(
                    run_id="run-1",
                    route_name="phase_diagram.generate",
                    system_name="Al-Zn",
                    request_summary="Al-Zn binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )

        self.assertEqual(snapshot.session_title, "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K。")
        self.assertEqual(snapshot.last_user_message, "请生成一张 Al-Zn 二元相图，温度范围 300K-1000K。")
        self.assertEqual(snapshot.message_count, 2)
        self.assertEqual(snapshot.asset_count, 0)
        self.assertEqual(snapshot.summary_version, "v2")
        self.assertIn("Session:", snapshot.current_context_summary)
        self.assertIn("Recognition:", snapshot.current_context_summary)
        self.assertIn("LastRun:", snapshot.current_context_summary)

    def test_merge_request_reuses_snapshot_context_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            existing = store.build_next_snapshot(
                conversation_id="follow-up",
                messages=[
                    ConversationTurn(role="user", content="请生成一张 Pb-Sn 二元相图。"),
                    ConversationTurn(role="assistant", content="相图已经生成。"),
                ],
                uploaded_assets=[],
                recognition_result=None,
                last_run_context=LastRunContext(
                    run_id="run-2",
                    route_name="phase_diagram.generate",
                    system_name="Pb-Sn",
                    request_summary="Pb-Sn binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )
            store.save(existing)

            request = build_request("你刚刚用了什么数据库？", conversation_id="follow-up")
            merged = store.merge_request(request)

        self.assertEqual(merged.session_title, "请生成一张 Pb-Sn 二元相图。")
        self.assertEqual(merged.last_run_context.system_name, "Pb-Sn")
        self.assertEqual(merged.message_count, 2)
        self.assertIn("Pb-Sn", merged.current_context_summary)

    def test_save_creates_short_and_long_term_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            snapshot = store.build_next_snapshot(
                conversation_id="split-save",
                messages=[
                    ConversationTurn(role="user", content="请生成一张 Al-Co 二元相图。"),
                    ConversationTurn(role="assistant", content="好的，我会开始计算。"),
                ],
                uploaded_assets=[],
                recognition_result=None,
                last_run_context=LastRunContext(
                    run_id="run-split",
                    route_name="phase_diagram.generate",
                    system_name="Al-Co",
                    request_summary="Al-Co binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )

            saved_paths = store.save(snapshot)
            reloaded = store.load("split-save")
            self.assertTrue(saved_paths["short_term"].exists())
            self.assertTrue(saved_paths["long_term"].exists())
            self.assertTrue(saved_paths["sqlite"].exists())
            self.assertEqual(reloaded.short_term.session_title, "请生成一张 Al-Co 二元相图。")
            self.assertIn("Al-Co", reloaded.long_term.strategic_summary)
            self.assertIn("Al-Co", " ".join(reloaded.long_term.research_topics))

    def test_sqlite_is_canonical_memory_persistence_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            store = MemoryStore(root_dir=root_dir)
            snapshot = store.build_next_snapshot(
                conversation_id="sqlite-memory",
                messages=[
                    ConversationTurn(role="user", content="请生成一张 Fe-Ni 二元相图，温度范围 300K-2000K。"),
                    ConversationTurn(role="assistant", content="相图已经生成。"),
                ],
                uploaded_assets=[],
                recognition_result=None,
                last_run_context=LastRunContext(
                    run_id="sqlite-run",
                    route_name="phase_diagram.generate",
                    system_name="Fe-Ni",
                    request_summary="Fe-Ni binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )
            saved_paths = store.save(snapshot)
            saved_paths["short_term"].unlink()
            saved_paths["long_term"].unlink()

            reloaded = MemoryStore(root_dir=root_dir).load("sqlite-memory")
            with closing(sqlite3.connect(saved_paths["sqlite"])) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM memory_snapshots WHERE conversation_id = ?",
                    ("sqlite-memory",),
                ).fetchone()[0]

        self.assertEqual(count, 1)
        self.assertEqual(reloaded.last_run_context.system_name, "Fe-Ni")
        self.assertEqual(reloaded.short_term.session_title, "请生成一张 Fe-Ni 二元相图，温度范围 300K-2000K。")
        self.assertIn("Fe-Ni", reloaded.long_term.strategic_summary)

    def test_long_term_memory_extracts_user_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            snapshot = store.build_next_snapshot(
                conversation_id="preferences",
                messages=[
                    ConversationTurn(role="user", content="后面前端先别动，优先扩 TDB 和后端 memory。"),
                    ConversationTurn(role="assistant", content="好的，我先冻结前端。"),
                    ConversationTurn(role="user", content="相图结果最好保持真实计算，不要退回示意图。"),
                ],
                uploaded_assets=[],
                recognition_result=None,
                last_run_context=LastRunContext(
                    run_id="pref-run",
                    route_name="phase_diagram.generate",
                    system_name="Al-Zn",
                    request_summary="Al-Zn binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )

        self.assertGreaterEqual(len(snapshot.long_term.user_preferences), 2)
        self.assertTrue(any("前端先别动" in item for item in snapshot.long_term.user_preferences))
        self.assertTrue(any("不要退回示意图" in item for item in snapshot.long_term.user_preferences))

    def test_retrieve_long_term_context_prioritizes_relevant_topics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            snapshot = store.build_next_snapshot(
                conversation_id="retrieval",
                messages=[
                    ConversationTurn(role="user", content="请生成一张 Al-Co 二元相图。"),
                    ConversationTurn(role="assistant", content="好的。"),
                    ConversationTurn(role="user", content="后面继续扩充更多 TDB，并且优先后端 memory。"),
                ],
                uploaded_assets=[],
                recognition_result=RecognitionResult(
                    system="Al-Co",
                    diagram_type="binary",
                    phases=["LIQUID", "FCC_A1", "AL3CO"],
                    confidence=0.88,
                    source="llm_recognition_agent",
                ),
                last_run_context=LastRunContext(
                    run_id="retrieval-run",
                    route_name="phase_diagram.generate",
                    system_name="Al-Co",
                    request_summary="Al-Co binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )
            hits = store.retrieve_long_term_context(
                query="帮我继续分析 Al-Co 相图，并记住优先扩充 TDB",
                snapshot=snapshot,
                conversation_id="retrieval",
                limit=5,
            )

        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(any("Al-Co" in item for item in hits))
        self.assertTrue(any("TDB" in item or "扩充" in item for item in hits))

    def test_retrieve_long_term_context_matches_chinese_material_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            snapshot = store.build_next_snapshot(
                conversation_id="alias-retrieval",
                messages=[
                    ConversationTurn(role="user", content="请生成一张 Al-Co 二元相图，并重点关注 AL3CO 和液相区。"),
                    ConversationTurn(role="assistant", content="好的，我会使用真实 TDB 计算。"),
                    ConversationTurn(role="user", content="后面继续扩充更多 TDB，并保留长期记忆里的研究重点。"),
                ],
                uploaded_assets=[],
                recognition_result=RecognitionResult(
                    system="Al-Co",
                    diagram_type="binary",
                    phases=["LIQUID", "FCC_A1", "AL3CO"],
                    confidence=0.9,
                    source="llm_recognition_agent",
                ),
                last_run_context=LastRunContext(
                    run_id="alias-run",
                    route_name="phase_diagram.generate",
                    system_name="Al-Co",
                    request_summary="Al-Co binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                current_context_summary="",
            )
            hits = store.retrieve_long_term_context(
                query="帮我继续看铝钴体系相图，并优先扩充 TDB。",
                snapshot=snapshot,
                conversation_id="alias-retrieval",
                limit=5,
            )

        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(any("Al-Co" in item for item in hits))
        self.assertTrue(any("扩充" in item or "TDB" in item for item in hits))

    def test_load_migrates_legacy_single_file_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            legacy_path = store.paths.legacy_path_for("legacy-conversation")
            legacy_payload = {
                "conversation_id": "legacy-conversation",
                "messages": [
                    {"role": "user", "content": "请生成一张 Pb-Sn 二元相图。"},
                    {"role": "assistant", "content": "相图已经生成。"},
                ],
                "uploaded_assets": [],
                "recognition_result": None,
                "last_run_context": {
                    "run_id": "legacy-run",
                    "route_name": "phase_diagram.generate",
                    "system_name": "Pb-Sn",
                    "request_summary": "Pb-Sn binary phase diagram",
                    "generation_source": "llm_codegen_calculated_wrapper",
                    "selected_tool": "phase_diagram_codegen",
                    "review_passed": True,
                    "artifact_names": ["result.html"],
                },
                "session_title": "请生成一张 Pb-Sn 二元相图。",
                "last_user_message": "请生成一张 Pb-Sn 二元相图。",
                "message_count": 2,
                "asset_count": 0,
                "summary_version": "v2",
                "current_context_summary": "Session: legacy",
            }
            legacy_path.write_text(json.dumps(legacy_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            snapshot = store.load("legacy-conversation")
            saved_paths = store.save(snapshot)
            self.assertEqual(snapshot.short_term.session_title, "请生成一张 Pb-Sn 二元相图。")
            self.assertEqual(snapshot.long_term.conversation_id, "legacy-conversation")
            self.assertTrue(saved_paths["short_term"].exists())
            self.assertTrue(saved_paths["long_term"].exists())
            self.assertFalse(legacy_path.exists())

    def test_summarize_recomputes_long_term_instead_of_reusing_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            previous_long_term = LongTermMemorySnapshot(
                conversation_id="summary-refresh",
                strategic_summary="之前一直在讨论 Al-Zn。",
                research_topics=["Al-Zn", "液相线"],
            )
            summary = store.summarize(
                messages=[ConversationTurn(role="user", content="现在改成继续看 Pb-Sn 相图，并关注共晶附近。")],
                last_run_context=LastRunContext(
                    run_id="summary-refresh-run",
                    route_name="phase_diagram.generate",
                    system_name="Pb-Sn",
                    request_summary="Pb-Sn binary phase diagram",
                    generation_source="llm_codegen_calculated_wrapper",
                    selected_tool="phase_diagram_codegen",
                    review_passed=True,
                    artifact_names=["result.html"],
                ),
                previous_long_term=previous_long_term,
                conversation_id="summary-refresh",
            )

        self.assertIn("Pb-Sn", summary)
        self.assertNotIn("no prior conversation", summary.lower())

    def test_combined_summary_stays_within_prompt_suggestion_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(root_dir=Path(tmp_dir))
            very_long = "扩充TDB和长期记忆" * 260
            summary = store.summarize(
                messages=[ConversationTurn(role="user", content=very_long)],
                last_run_context=LastRunContext(
                    run_id="length-run",
                    route_name="conversation.answer",
                    request_summary="length stress test",
                ),
                previous_long_term=LongTermMemorySnapshot(
                    conversation_id="length",
                    strategic_summary=very_long,
                    research_topics=[very_long, "TDB"],
                    salient_facts=[very_long],
                    user_preferences=[very_long],
                ),
                conversation_id="length",
            )

        self.assertLessEqual(len(summary), 4000)


if __name__ == "__main__":
    unittest.main()
