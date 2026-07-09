from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.agents.chat import ChatAgent
from app.agents.compute import ComputeAgent
from app.agents.recognition import RecognitionAgent
from app.agents.supervisor import SupervisorAgent
from app.core.artifacts import ArtifactService
from app.graph import AgentAppGraph
from app.memory import MemoryStore
from app.shared_memory import MemoryItem, MemoryScope, SharedMemoryService
from app.state import AgentChatRequest, AgentGraphState, AgentRunResponse, MemorySnapshot, TaskRoute
from tests.support import ScriptedLLMClient


def _base_state(request: AgentChatRequest) -> AgentGraphState:
    return {
        "run_id": "run-shared-agent",
        "request_id": request.request_id,
        "conversation_id": request.conversation_id,
        "request": request,
        "messages": [],
        "uploaded_assets": [],
        "user_intent": "",
        "next_step": "",
        "compute_domain": "none",
        "route": TaskRoute(name="supervisor.dispatch"),
        "recognition_result": None,
        "phase_diagram_result": None,
        "lammps_result": None,
        "last_run_context": request.last_run_context,
        "artifact_messages": [],
        "html_content": "",
        "html_path": "",
        "current_context_summary": "",
        "final_answer": "",
        "error": "",
        "success": True,
        "termination_reason": "",
        "response_metadata": {},
        "response_summary": {},
        "plan_steps": [],
        "trace": [],
        "event_sink": None,
        "memory_snapshot": MemorySnapshot(conversation_id=request.conversation_id),
        "long_term_memory_hits": [],
        "shared_memory_context": {},
        "shared_memory_events": [],
        "protocol_messages": [],
    }


def _graph(root: Path, shared_memory: SharedMemoryService) -> AgentAppGraph:
    artifact_service = ArtifactService(root_dir=root)
    memory_store = MemoryStore(root_dir=root)
    scripted = ScriptedLLMClient()
    return AgentAppGraph(
        artifact_service=artifact_service,
        memory_store=memory_store,
        shared_memory_service=shared_memory,
        supervisor=SupervisorAgent(llm_client=scripted),
        recognition_agent=RecognitionAgent(llm_client=scripted),
        compute_agent=ComputeAgent(phase_diagram_runtime=object(), lammps_runtime=object()),  # type: ignore[arg-type]
        chat_agent=ChatAgent(llm_client=scripted, artifact_service=artifact_service),
    )


class SharedMemoryAgentIntegrationTests(unittest.TestCase):
    def test_supervisor_writes_lammps_user_constraints_to_shared_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_store = MemoryStore(root_dir=root)
            shared_memory = SharedMemoryService(root_dir=memory_store.paths.root_dir)
            graph = _graph(root, shared_memory)
            request = AgentChatRequest(
                conversation_id="shared-agent",
                message="请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，NVT。",
            )
            state = _base_state(request)
            state.update(graph.load_memory_node(state))
            supervisor_update = graph.supervisor_node(state)
            state.update(supervisor_update)
            items = shared_memory.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="shared-agent", include_global=False),
                statuses=["active"],
                limit=100,
            )

        constraints = {(item.subject, item.predicate): item for item in items if item.item_type == "constraint"}
        self.assertEqual(state["route"].name, "lammps.generate")
        self.assertEqual(constraints[("LAMMPS request", "material")].value, "Cu")
        self.assertEqual(constraints[("LAMMPS request", "target_temperature")].value, 800.0)
        self.assertEqual(constraints[("LAMMPS request", "steps")].value, 4000)
        self.assertEqual(constraints[("LAMMPS request", "ensemble")].value, "NVT")
        self.assertGreaterEqual(supervisor_update["response_metadata"]["shared_memory"]["write_count"], 5)
        self.assertTrue(supervisor_update["shared_memory_context"]["forced_retention_ids"])

    def test_supervisor_surfaces_locked_constraint_conflict_in_shared_memory_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_store = MemoryStore(root_dir=root)
            shared_memory = SharedMemoryService(root_dir=memory_store.paths.root_dir)
            locked = shared_memory.write(
                MemoryItem(
                    scope_type="conversation",
                    scope_id="locked-agent",
                    item_type="constraint",
                    subject="LAMMPS request",
                    predicate="target_temperature",
                    value=800,
                    unit="K",
                    text="The user locked the LAMMPS target temperature at 800 K.",
                    authority="user",
                    source_refs=["user:locked-temperature"],
                    metadata={"locked": True},
                )
            ).item
            graph = _graph(root, shared_memory)
            request = AgentChatRequest(
                conversation_id="locked-agent",
                message="请用 LAMMPS 做一个 Cu 的 heating 模拟，900K，4000 steps，NVT。",
            )
            state = _base_state(request)
            state.update(graph.load_memory_node(state))
            supervisor_update = graph.supervisor_node(state)
            metadata = supervisor_update["response_metadata"]["shared_memory"]
            writes = metadata["writes"]
            conflicted_writes = [item for item in writes if item.get("conflicted")]

        self.assertEqual(metadata["needs_user_count"], 1)
        self.assertEqual(metadata["conflicted_count"], 1)
        self.assertEqual(metadata["unsafe_write_count"], 1)
        self.assertEqual(len(conflicted_writes), 1)
        self.assertEqual(conflicted_writes[0]["predicate"], "target_temperature")
        self.assertEqual(conflicted_writes[0]["conflict_statuses"], ["needs_user"])
        self.assertIn(locked.memory_id, supervisor_update["shared_memory_context"]["forced_retention_ids"])

    def test_compute_node_writes_lammps_execution_facts_to_shared_memory(self) -> None:
        class StubComputeAgent:
            def run(self, state, decision):  # noqa: ANN001
                _ = state, decision
                response = AgentRunResponse(
                    success=True,
                    run_id="lammps-run-1",
                    conversation_id="shared-agent",
                    route=TaskRoute(name="lammps.generate", compute_domain="lammps"),
                    final_message="LAMMPS task completed.",
                    artifacts=[],
                    plan_steps=[],
                    trace=[],
                    generated_code="in.lammps",
                    termination_reason="review_passed",
                    metadata={"run_mode": "mock"},
                    summary={
                        "mode": "mock",
                        "request": {
                            "material": "Cu",
                            "task_type": "heating",
                            "temperature": 800,
                            "steps": 4000,
                            "ensemble": "NVT",
                        },
                        "metrics": {"final_temperature": 795.2},
                        "validation": {"warnings": ["mock execution"]},
                    },
                    run_status="completed",
                    stdout="",
                    stderr="",
                    html_content=None,
                    html_path=None,
                )
                return {
                    "route": response.route,
                    "lammps_result": response,
                    "plan_steps": response.plan_steps,
                    "trace": response.trace,
                    "artifact_messages": response.artifacts,
                    "messages": state.get("messages", []),
                    "last_run_context": state.get("last_run_context"),
                    "success": True,
                    "termination_reason": response.termination_reason,
                    "response_metadata": response.metadata,
                    "error": "",
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_store = MemoryStore(root_dir=root)
            shared_memory = SharedMemoryService(root_dir=memory_store.paths.root_dir)
            graph = _graph(root, shared_memory)
            graph.compute_agent = StubComputeAgent()  # type: ignore[assignment]
            request = AgentChatRequest(
                conversation_id="shared-agent",
                message="请用 LAMMPS 做一个 Cu 的 heating 模拟，800K，4000 steps，NVT。",
            )
            state = _base_state(request)
            state.update({"compute_domain": "lammps", "route": TaskRoute(name="lammps.generate", compute_domain="lammps")})
            result = graph.compute_node(state)
            items = shared_memory.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="shared-agent", include_global=False),
                statuses=["active"],
                limit=100,
            )

        facts = {(item.subject, item.predicate): item for item in items if item.item_type == "fact"}
        self.assertEqual(facts[("LAMMPS request", "material")].value, "Cu")
        self.assertEqual(facts[("LAMMPS request", "target_temperature")].value, 800)
        self.assertEqual(facts[("LAMMPS request", "steps")].value, 4000)
        self.assertEqual(facts[("LAMMPS run lammps-run-1", "metric:final_temperature")].value, 795.2)
        self.assertGreaterEqual(result["response_metadata"]["shared_memory"]["write_count"], 7)
        self.assertTrue(result["shared_memory_context"]["selected_item_ids"])

    def test_chat_node_writes_materials_rag_evidence_to_shared_memory(self) -> None:
        class StubChatAgent:
            def run(self, state):  # noqa: ANN001
                _ = state
                return {
                    "final_answer": "RAG answer",
                    "messages": [],
                    "success": True,
                    "error": "",
                    "artifact_messages": [],
                    "html_content": "",
                    "html_path": "",
                    "response_metadata": {"materials_rag": {"used": True, "hit_count": 1}},
                    "response_summary": {"materials_rag": {"used": True}},
                    "rag_evidence": {
                        "kind": "materials_rag",
                        "query": "LAMMPS rdf 怎么分析？",
                        "domain": "lammps",
                        "doc_type": "command_card",
                        "material": "Cu",
                        "hits": [
                            {
                                "document": {
                                    "id": "lammps.command.rdf",
                                    "domain": "lammps",
                                    "doc_type": "command_card",
                                    "title": "LAMMPS compute rdf",
                                    "content": "Use compute rdf for radial distribution function analysis.",
                                    "source": "LAMMPS manual",
                                    "source_url": "https://docs.lammps.org/compute_rdf.html",
                                    "trust_level": "high",
                                },
                                "score": 3.2,
                                "lexical_score": 1.0,
                                "bm25_score": 2.0,
                                "vector_score": 0.2,
                                "embedding_backend": "local_hash",
                                "matched_fields": ["title", "bm25"],
                            }
                        ],
                    },
                    "termination_reason": "conversation_answered",
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_store = MemoryStore(root_dir=root)
            shared_memory = SharedMemoryService(root_dir=memory_store.paths.root_dir)
            graph = _graph(root, shared_memory)
            graph.chat_agent = StubChatAgent()  # type: ignore[assignment]
            request = AgentChatRequest(conversation_id="rag-agent", message="LAMMPS rdf 怎么分析？")
            state = _base_state(request)
            result = graph.chat_node(state)
            items = shared_memory.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="rag-agent", include_global=False),
                item_types=["evidence"],
                statuses=["active"],
                limit=100,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].subject, "materials_rag:lammps.command.rdf")
        self.assertEqual(items[0].metadata["stage"], "chat_materials_rag")
        self.assertEqual(items[0].source_refs, ["https://docs.lammps.org/compute_rdf.html"])
        self.assertEqual(result["response_metadata"]["shared_memory"]["write_count"], 1)
        self.assertIn(items[0].memory_id, result["shared_memory_context"]["selected_item_ids"])

    def test_compute_node_writes_lammps_materials_rag_evidence_to_shared_memory(self) -> None:
        class StubComputeAgent:
            def run(self, state, decision):  # noqa: ANN001
                _ = state, decision
                response = AgentRunResponse(
                    success=True,
                    run_id="lammps-rag-run",
                    conversation_id="rag-agent",
                    route=TaskRoute(name="lammps.generate", compute_domain="lammps"),
                    final_message="LAMMPS completed.",
                    artifacts=[],
                    plan_steps=[],
                    trace=[],
                    generated_code="in.lammps",
                    termination_reason="review_passed",
                    metadata={"run_mode": "mock"},
                    summary={
                        "mode": "mock",
                        "request": {"material": "Cu", "task_type": "heating", "temperature": 800, "steps": 4000, "ensemble": "NVT"},
                        "metrics": {},
                        "validation": {},
                        "materials_rag": {
                            "planning": {
                                "query": "Cu heating",
                                "material": "Cu",
                                "hits": [
                                    {
                                        "id": "lammps.potential.eam",
                                        "title": "EAM potential for Cu",
                                        "domain": "lammps",
                                        "doc_type": "potential_card",
                                        "content_excerpt": "Use EAM potentials for Cu metallic simulations.",
                                        "source": "curated",
                                        "source_url": "https://example.org/cu-eam",
                                        "score": 2.5,
                                    }
                                ],
                            }
                        },
                    },
                    run_status="completed",
                    stdout="",
                    stderr="",
                    html_content=None,
                    html_path=None,
                )
                return {
                    "route": response.route,
                    "lammps_result": response,
                    "plan_steps": [],
                    "trace": [],
                    "artifact_messages": [],
                    "messages": state.get("messages", []),
                    "last_run_context": state.get("last_run_context"),
                    "success": True,
                    "termination_reason": response.termination_reason,
                    "response_metadata": response.metadata,
                    "error": "",
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_store = MemoryStore(root_dir=root)
            shared_memory = SharedMemoryService(root_dir=memory_store.paths.root_dir)
            graph = _graph(root, shared_memory)
            graph.compute_agent = StubComputeAgent()  # type: ignore[assignment]
            request = AgentChatRequest(conversation_id="rag-agent", message="请用 LAMMPS 做 Cu heating，800K，4000 steps。")
            state = _base_state(request)
            result = graph.compute_node(state)
            items = shared_memory.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="rag-agent", include_global=False),
                item_types=["evidence"],
                statuses=["active"],
                limit=100,
            )

        evidence = [item for item in items if item.subject == "materials_rag:lammps.potential.eam"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].metadata["stage"], "lammps_planning_materials_rag")
        self.assertEqual(evidence[0].source_refs, ["https://example.org/cu-eam"])
        self.assertGreaterEqual(result["response_metadata"]["shared_memory"]["write_count"], 1)

    def test_compute_node_writes_thermo_rag_evidence_to_shared_memory(self) -> None:
        class StubComputeAgent:
            def run(self, state, decision):  # noqa: ANN001
                _ = state, decision
                response = AgentRunResponse(
                    success=True,
                    run_id="phase-rag-run",
                    conversation_id="thermo-rag-agent",
                    route=TaskRoute(name="phase_diagram.generate", compute_domain="phase_diagram"),
                    final_message="Phase diagram completed.",
                    artifacts=[],
                    plan_steps=[],
                    trace=[],
                    generated_code="code.py",
                    termination_reason="review_passed",
                    metadata={
                        "thermo_lookup": {
                            "matched": True,
                            "query": "Al-Zn",
                            "selection_strategy": "rag_auto_select",
                            "embedding_backend": "local_hash",
                            "candidates": [
                                {
                                    "system_name": "Al-Zn",
                                    "database_name": "alzn_mey.tdb",
                                    "summary": "Al-Zn binary TDB card.",
                                    "score": 1.2,
                                    "lexical_score": 1.0,
                                    "source_url": "https://example.org/alzn",
                                    "match_reasons": ["exact_system_or_alias_match"],
                                    "components": ["AL", "ZN"],
                                    "phases": ["LIQUID", "FCC_A1"],
                                }
                            ],
                        }
                    },
                    summary={"system_name": "Al-Zn", "diagram_type": "binary"},
                    run_status="completed",
                    stdout="",
                    stderr="",
                    html_content=None,
                    html_path=None,
                )
                return {
                    "route": response.route,
                    "phase_diagram_result": response,
                    "plan_steps": [],
                    "trace": [],
                    "artifact_messages": [],
                    "messages": state.get("messages", []),
                    "last_run_context": state.get("last_run_context"),
                    "success": True,
                    "termination_reason": response.termination_reason,
                    "response_metadata": response.metadata,
                    "error": "",
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_store = MemoryStore(root_dir=root)
            shared_memory = SharedMemoryService(root_dir=memory_store.paths.root_dir)
            graph = _graph(root, shared_memory)
            graph.compute_agent = StubComputeAgent()  # type: ignore[assignment]
            request = AgentChatRequest(conversation_id="thermo-rag-agent", message="请生成 Al-Zn 相图。")
            state = _base_state(request)
            result = graph.compute_node(state)
            items = shared_memory.store.list_items(
                scope=MemoryScope(scope_type="conversation", scope_id="thermo-rag-agent", include_global=False),
                item_types=["evidence"],
                statuses=["active"],
                limit=100,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].subject, "thermo_rag:Al-Zn")
        self.assertEqual(items[0].predicate, "database_candidate")
        self.assertEqual(items[0].authority, "rag")
        self.assertEqual(items[0].source_refs, ["https://example.org/alzn"])
        self.assertEqual(result["response_metadata"]["shared_memory"]["write_count"], 1)


if __name__ == "__main__":
    unittest.main()
