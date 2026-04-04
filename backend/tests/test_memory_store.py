from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.memory import MemoryStore
from app.state import ConversationTurn, LastRunContext, RecognitionResult
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


if __name__ == "__main__":
    unittest.main()
