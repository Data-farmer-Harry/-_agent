from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import api as api_module
from app.agents.chat import ChatAgent
from app.agents.supervisor import SupervisorAgent
from app.config import settings
from app.core.artifacts import ArtifactService
from app.lammps.config import LammpsConfig
from app.materials_rag import retriever as materials_retriever
from app.materials_rag import vector as materials_vector
from app.materials_rag.document_store import load_materials_rag_documents
from app.materials_rag.normalizer import canonical_terms, extract_materials
from app.materials_rag.service import MaterialsRagService
from app.rag.query_rewrite import rewrite_materials_query
from app.runtimes.lammps import LammpsRuntime
from app.state import AgentGraphState, LastRunContext
from tests.support import ScriptedLLMClient, build_request


class _RagAwareLLM(ScriptedLLMClient):
    def chat_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.1,
        capability: str = "",
    ) -> str:
        self.calls.append(
            {
                "method": "chat_text",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "capability": capability,
            }
        )
        if "Materials RAG context:" in user_prompt and "LAMMPS fix nvt" in user_prompt:
            return "RAG_CONTEXT_INCLUDED: fix nvt explanation is grounded."
        return super().chat_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            capability=capability,
        )


class MaterialsRagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_backend = settings.materials_rag_embedding_backend
        self._old_enabled = settings.materials_rag_enabled
        self._old_embedding_base_url = settings.materials_rag_embedding_api_base_url
        self._old_embedding_api_key = settings.materials_rag_embedding_api_key
        self._old_embedding_model = settings.materials_rag_embedding_model
        self._old_dimensions = settings.materials_rag_embedding_dimensions
        self._old_weight = settings.materials_rag_vector_weight
        self._old_batch_size = settings.materials_rag_embedding_api_batch_size
        self._old_vector_store_path = settings.rag_vector_store_path
        self._rag_tmp = tempfile.TemporaryDirectory()
        settings.rag_vector_store_path = str(Path(self._rag_tmp.name) / "vectors.sqlite3")
        settings.materials_rag_enabled = True
        settings.materials_rag_embedding_backend = "local_hash"
        settings.materials_rag_embedding_api_base_url = ""
        settings.materials_rag_embedding_api_key = ""
        settings.materials_rag_embedding_dimensions = 128
        settings.materials_rag_vector_weight = 0.24
        materials_vector._REMOTE_BACKEND_FAILURES.clear()
        materials_vector._fetch_remote_query_embedding_cached.cache_clear()

    def tearDown(self) -> None:
        settings.materials_rag_embedding_backend = self._old_backend
        settings.materials_rag_enabled = self._old_enabled
        settings.materials_rag_embedding_api_base_url = self._old_embedding_base_url
        settings.materials_rag_embedding_api_key = self._old_embedding_api_key
        settings.materials_rag_embedding_model = self._old_embedding_model
        settings.materials_rag_embedding_dimensions = self._old_dimensions
        settings.materials_rag_vector_weight = self._old_weight
        settings.materials_rag_embedding_api_batch_size = self._old_batch_size
        settings.rag_vector_store_path = self._old_vector_store_path
        materials_vector._REMOTE_BACKEND_FAILURES.clear()
        materials_vector._fetch_remote_query_embedding_cached.cache_clear()
        self._rag_tmp.cleanup()

    def test_embedding_api_can_use_dedicated_openai_compatible_endpoint(self) -> None:
        settings.materials_rag_embedding_backend = "llm_api"
        settings.materials_rag_embedding_api_base_url = "https://openrouter.ai/api/v1"
        settings.materials_rag_embedding_api_key = "embedding-key"
        settings.materials_rag_embedding_model = "openai/text-embedding-3-small"
        settings.materials_rag_embedding_dimensions = 4
        settings.materials_rag_embedding_api_batch_size = 10

        captured: dict[str, object] = {}

        class FakeResponse:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "data": [
                            {"embedding": [1.0, 0.0, 0.0, 0.0]},
                            {"embedding": [0.0, 1.0, 0.0, 0.0]},
                        ]
                    }
                ).encode("utf-8")

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        def fake_urlopen(request_obj, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = request_obj.full_url
            captured["headers"] = dict(request_obj.header_items())
            captured["timeout"] = timeout
            captured["body"] = json.loads(request_obj.data.decode("utf-8"))
            return FakeResponse()

        with patch("app.materials_rag.vector.urllib_request.urlopen", side_effect=fake_urlopen):
            vectors, backend = materials_vector.build_embeddings(["LAMMPS fix nvt", "CALPHAD TDB"])

        self.assertEqual(backend, "llm_api")
        self.assertEqual(captured["url"], "https://openrouter.ai/api/v1/embeddings")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer embedding-key")
        self.assertEqual(captured["body"]["model"], "openai/text-embedding-3-small")
        self.assertEqual(captured["body"]["dimensions"], 4)
        self.assertEqual(captured["body"]["input"], ["LAMMPS fix nvt", "CALPHAD TDB"])
        self.assertEqual(len(vectors), 2)

    def test_document_store_loads_expanded_materials_rag_corpus(self) -> None:
        documents = load_materials_rag_documents()

        self.assertGreaterEqual(len(documents), 100)
        ids = {document.id for document in documents}
        self.assertIn("lammps.command.fix_nvt", ids)
        self.assertIn("lammps.potential.meam", ids)
        self.assertIn("lammps.error.out_of_range_atoms", ids)
        self.assertIn("thermo.concept.calphad", ids)
        self.assertIn("materials.database.materials_project", ids)
        self.assertIn("materials.concept.energy_above_hull", ids)
        self.assertIn("materials.card.ti_hcp", ids)

    def test_search_retrieves_lammps_command_with_vector_scores(self) -> None:
        hits = MaterialsRagService.search("fix nvt 怎么用？", domain="lammps", top_k=5)

        self.assertTrue(hits)
        self.assertEqual(hits[0].document.id, "lammps.command.fix_nvt")
        self.assertGreater(hits[0].score, 0)
        self.assertGreaterEqual(hits[0].vector_score, 0)
        self.assertEqual(hits[0].embedding_backend, "local_hash")
        self.assertIn("vector", hits[0].matched_fields)

    def test_exact_lammps_error_uses_high_precision_lexical_shortcut(self) -> None:
        with patch(
            "app.materials_rag.retriever.build_embedding_with_backend",
            side_effect=AssertionError("exact error lookup should not call query embedding"),
        ), patch(
            "app.materials_rag.retriever.rerank_texts",
            side_effect=AssertionError("exact error lookup should not call remote reranker"),
        ):
            hits = MaterialsRagService.search(
                "LAMMPS lost atoms 报错怎么办？",
                domain="lammps",
                doc_type="error_cookbook",
                top_k=4,
            )

        self.assertTrue(hits)
        self.assertEqual(hits[0].document.id, "lammps.error.lost_atoms")
        self.assertEqual(hits[0].embedding_backend, "lexical_shortcut")

    def test_material_document_embeddings_are_reused_after_memory_cache_reset(self) -> None:
        first = materials_retriever._build_index()
        self.assertTrue(first)
        materials_retriever._INDEX_CACHE_KEY = None
        materials_retriever._INDEX_CACHE_DOCUMENTS = ()
        materials_retriever._INDEX_CACHE_BM25 = None

        with patch("app.materials_rag.retriever.build_embeddings", side_effect=AssertionError("embedding API should not rerun")):
            second = materials_retriever._build_index()

        self.assertEqual(len(second), len(first))

    def test_online_request_reuses_content_current_index_when_preferred_backend_changes(self) -> None:
        first = materials_retriever._build_index()
        self.assertTrue(first)
        settings.materials_rag_embedding_backend = "llm_api"
        settings.materials_rag_embedding_api_base_url = "https://openrouter.ai/api/v1"
        settings.materials_rag_embedding_api_key = "embedding-key"
        settings.materials_rag_embedding_model = "openai/text-embedding-3-small"
        materials_retriever._INDEX_CACHE_KEY = None
        materials_retriever._INDEX_CACHE_DOCUMENTS = ()
        materials_retriever._INDEX_CACHE_BM25 = None

        with patch("app.materials_rag.retriever.build_embeddings", side_effect=AssertionError("online reindex must not run")):
            second = materials_retriever._build_index()

        self.assertEqual(len(second), len(first))
        self.assertEqual(second[0].embedding_backend, "local_hash")

    def test_search_retrieves_msd_diffusion_command(self) -> None:
        hits = MaterialsRagService.search("MSD 怎么算扩散系数？", domain="lammps", top_k=5)

        self.assertTrue(hits)
        self.assertEqual(hits[0].document.id, "lammps.command.compute_msd")

    def test_search_retrieves_potential_and_error_cards(self) -> None:
        potential_hits = MaterialsRagService.search("Cu heating 用 EAM 势函数合适吗？", domain="lammps", doc_type="potential_card", top_k=5)
        error_hits = MaterialsRagService.search("LAMMPS lost atoms 报错怎么办？", domain="lammps", doc_type="error_cookbook", top_k=5)

        self.assertTrue(any(hit.document.id == "lammps.potential.eam_metals" for hit in potential_hits))
        self.assertTrue(any(hit.document.id == "lammps.error.lost_atoms" for hit in error_hits))

    def test_top_k_and_doc_type_filters_are_enforced(self) -> None:
        command_hits = MaterialsRagService.search("LAMMPS thermo 输出和 trajectory 怎么设置？", domain="lammps", doc_type="command_card", top_k=2)

        self.assertEqual(len(command_hits), 2)
        self.assertTrue(all(hit.document.doc_type == "command_card" for hit in command_hits))

    def test_search_retrieves_thermodynamics_concepts(self) -> None:
        hits = MaterialsRagService.search("CALPHAD 和 TDB 热力学数据库是什么关系？", domain="thermodynamics", top_k=5)

        self.assertTrue(hits)
        self.assertEqual(hits[0].document.id, "thermo.concept.calphad")
        self.assertEqual(hits[0].embedding_backend, "local_hash")

    def test_search_retrieves_materials_database_cards(self) -> None:
        hits = MaterialsRagService.search("Materials Project API 能查 band gap 吗？", domain="materials", top_k=5)

        ids = {hit.document.id for hit in hits}
        self.assertIn("materials.database.materials_project_api", ids)
        self.assertIn("materials.concept.band_gap", ids)

    def test_search_retrieves_materials_stability_concepts(self) -> None:
        hits = MaterialsRagService.search("formation energy 和 energy above hull 有什么区别？", domain="materials", top_k=5)

        ids = {hit.document.id for hit in hits}
        self.assertIn("materials.concept.formation_energy", ids)
        self.assertIn("materials.concept.energy_above_hull", ids)

    def test_query_rewrite_expands_mixed_language_materials_questions(self) -> None:
        query = "AlCoCrFeNi HEA 的 ReaxFF 力场和 DFT结果偏差很大，是不是训练域不够？"
        rewrite = rewrite_materials_query(query)

        self.assertTrue(rewrite.changed)
        self.assertEqual(extract_materials("AlCoCrFeNi"), ("Al", "Co", "Cr", "Fe", "Ni"))
        self.assertIn("concept:force_field_validation", canonical_terms(query))
        self.assertIn("force field validation", rewrite.expansion_terms)
        self.assertIn("high entropy alloy", rewrite.expansion_terms)
        self.assertIn("retrieval rewrite terms", rewrite.search_query)

    def test_query_rewrite_recovers_previous_first_stage_miss_patterns(self) -> None:
        force_field_query = (
            "一个为AlCoCrFeNi HEA训练的ReaxFF力场，在模拟Cr空位迁移时预测的形成能与DFT结果偏差超过1.5 eV，"
            "这是否意味着其训练域未覆盖高浓度缺陷或非平衡态结构？"
        )
        biomaterial_query = "用于人工关节的钛合金为何被认为具有优异的生物相容性？其表面氧化层是否在防止离子释放和促进骨整合中起关键作用？"
        high_entropy_query = (
            "与传统合金中以Fe、Cu等为主元不同，高熵合金为何采用五种以上元素等摩尔比设计？"
            "这种设计如何通过提升混合熵来稳定固溶体结构并抑制析出相形成？"
        )

        force_field_ids = {
            hit.document.id
            for hit in MaterialsRagService.search(force_field_query, domain="materials", doc_type="concept_card", top_k=20)
        }
        biomaterial_ids = {
            hit.document.id
            for hit in MaterialsRagService.search(biomaterial_query, domain="materials", top_k=20)
        }
        high_entropy_ids = {
            hit.document.id
            for hit in MaterialsRagService.search(high_entropy_query, domain="metallurgy", top_k=20)
        }

        self.assertIn("materials.concept.force_field_validation", force_field_ids)
        self.assertTrue(
            {"wikipedia.en.biomaterial.chunk1", "wikipedia.en.biomaterial.chunk2"}.intersection(biomaterial_ids)
        )
        self.assertTrue(
            {"wikipedia.en.high-entropy-alloy.chunk1", "wikipedia.en.high-entropy-alloy.chunk2"}.intersection(high_entropy_ids)
        )

    def test_search_retrieves_mechanical_and_phonon_concepts(self) -> None:
        hits = MaterialsRagService.search("JARVIS DFT elastic tensor 和声子数据有什么用？", domain="materials", top_k=5)

        ids = {hit.document.id for hit in hits}
        self.assertIn("materials.database.jarvis_dft", ids)
        self.assertIn("materials.concept.elastic_tensor", ids)
        self.assertIn("materials.concept.phonon_stability", ids)

    def test_search_retrieves_material_cards_and_potential_selection_advice(self) -> None:
        ti_hits = MaterialsRagService.search("Ti 是什么晶体结构？", domain="materials", top_k=5)
        oxide_hits = MaterialsRagService.search("Al2O3 氧化物能用 EAM 吗？", domain="materials", top_k=5)
        potential_hits = MaterialsRagService.search("怎么选择 interatomic potential？", domain="materials", top_k=5)

        self.assertEqual(ti_hits[0].document.id, "materials.card.ti_hcp")
        self.assertEqual(oxide_hits[0].document.id, "materials.card.alumina_oxide")
        self.assertTrue(any(hit.document.id == "materials.workflow.potential_selection" for hit in potential_hits))

    def test_materials_rag_debug_endpoint_returns_score_breakdown(self) -> None:
        with TestClient(api_module.app) as client:
            response = client.get("/api/materials-rag/search", params={"q": "fix nvt 怎么用", "top_k": 3})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["matched"])
        self.assertGreaterEqual(len(payload["hits"]), 1)
        first = payload["hits"][0]
        self.assertIn("lexical_score", first)
        self.assertIn("bm25_score", first)
        self.assertIn("vector_score", first)
        self.assertGreater(first["bm25_score"], 0)
        self.assertEqual(first["embedding_backend"], "local_hash")

    def test_chat_agent_injects_materials_rag_context(self) -> None:
        llm = _RagAwareLLM()
        with tempfile.TemporaryDirectory() as tmp:
            agent = ChatAgent(llm_client=llm, artifact_service=ArtifactService(root_dir=Path(tmp)))
            state: AgentGraphState = {
                "request": build_request("fix nvt 怎么用？"),
                "messages": [],
                "uploaded_assets": [],
                "last_run_context": LastRunContext(),
                "current_context_summary": "",
            }

            with patch.object(agent.materials_rag_service, "search", wraps=agent.materials_rag_service.search) as search:
                with patch.object(agent.materials_rag_service, "build_context", side_effect=AssertionError("duplicate search")):
                    result = agent.run(state)

        self.assertIn("RAG_CONTEXT_INCLUDED", result["final_answer"])
        self.assertEqual(search.call_count, 1)
        self.assertTrue(result["response_metadata"]["materials_rag"]["used"])
        self.assertIn("LAMMPS fix nvt", result["response_metadata"]["materials_rag"]["titles"])

    def test_chat_agent_skips_rag_for_simple_materials_definition(self) -> None:
        llm = ScriptedLLMClient()
        with tempfile.TemporaryDirectory() as tmp:
            agent = ChatAgent(llm_client=llm, artifact_service=ArtifactService(root_dir=Path(tmp)))
            state: AgentGraphState = {
                "request": build_request("共析点是什么？"),
                "messages": [],
                "uploaded_assets": [],
                "last_run_context": LastRunContext(),
                "current_context_summary": "",
            }

            with patch.object(agent.materials_rag_service, "search", side_effect=AssertionError("simple question should not retrieve")):
                result = agent.run(state)

        rag_metadata = result["response_metadata"]["materials_rag"]
        self.assertFalse(rag_metadata["requested"])
        self.assertFalse(rag_metadata["used"])
        self.assertEqual(rag_metadata["gate_reason"], "direct_answer_sufficient")

    def test_chat_agent_uses_rag_when_simple_question_explicitly_requests_evidence(self) -> None:
        llm = ScriptedLLMClient()
        with tempfile.TemporaryDirectory() as tmp:
            agent = ChatAgent(llm_client=llm, artifact_service=ArtifactService(root_dir=Path(tmp)))
            state: AgentGraphState = {
                "request": build_request("共析点是什么？请给出知识库依据。"),
                "messages": [],
                "uploaded_assets": [],
                "last_run_context": LastRunContext(),
                "current_context_summary": "",
            }

            result = agent.run(state)

        rag_metadata = result["response_metadata"]["materials_rag"]
        self.assertTrue(rag_metadata["requested"])
        self.assertTrue(rag_metadata["used"])
        self.assertEqual(rag_metadata["gate_reason"], "explicit_grounding_request")

    def test_supervisor_routes_lammps_explanation_to_chat_not_runtime(self) -> None:
        supervisor = SupervisorAgent(llm_client=ScriptedLLMClient())
        state: AgentGraphState = {
            "request": build_request("fix nvt 和 fix npt 有什么区别？"),
            "messages": [],
            "uploaded_assets": [],
            "last_run_context": LastRunContext(),
            "current_context_summary": "",
        }

        decision = supervisor.decide(state)

        self.assertEqual(decision["route_name"], "conversation.answer")
        self.assertEqual(decision["intent"], "explain_lammps_or_materials_concept")

    def test_lammps_runtime_uses_materials_rag_for_planning_and_error_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp)),
                llm_client=ScriptedLLMClient(),
            )
            planning = runtime._planning_materials_rag(build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。"))
            error = runtime._error_materials_rag(
                error_text="ERROR: Lost atoms: original 256 current 250",
                material="Cu",
                request_message="Cu heating 800K",
            )

        self.assertEqual(planning["material"], "Cu")
        self.assertTrue(planning["hits"])
        self.assertTrue(error["hits"])
        self.assertEqual(error["hits"][0].document.id, "lammps.error.lost_atoms")
        self.assertIn("lost atoms", error["context"].lower())

    def test_lammps_execution_failure_retrieves_error_cookbook_from_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LammpsRuntime(
                artifact_service=ArtifactService(root_dir=Path(tmp)),
                llm_client=ScriptedLLMClient(),
                config_loader=lambda: LammpsConfig(
                    allow_mock_fallback=False,
                    force_mock=False,
                    lammps_command="/bin/echo",
                    potentials_dir=str(Path(tmp)),
                    max_retries=0,
                ),
            )
            with patch("app.runtimes.lammps.run_lammps", side_effect=RuntimeError("ERROR: Lost atoms: original 256 current 250")):
                result = runtime.run(
                    run_id="materials-rag-lammps-error",
                    request=build_request("请用 LAMMPS 做 Cu heating，800K，1000 steps。"),
                )

        self.assertFalse(result.success)
        self.assertEqual(result.termination_reason, "lammps_execution_failed")
        self.assertIn("Lost atoms", result.final_message)
        self.assertIn("materials_rag", result.metadata)
        error_diagnosis = result.metadata["materials_rag"]["error_diagnosis"]
        self.assertTrue(error_diagnosis["hits"])
        self.assertEqual(error_diagnosis["hits"][0]["title"], "LAMMPS lost atoms error")


if __name__ == "__main__":
    unittest.main()
