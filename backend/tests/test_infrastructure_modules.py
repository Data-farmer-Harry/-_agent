from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.agent_protocol import build_agent_envelope
from app.core.artifacts import ArtifactService
from app.core.observability import log_event, structured_log_path
from app.config import (
    build_settings,
    read_runtime_config_file,
    settings,
    update_runtime_config_file,
    update_runtime_env_file,
    update_runtime_llm_config,
)
from app.diagnostics import build_system_diagnostics
from app.lammps.config import load_lammps_config, update_runtime_lammps_config
from app.memory import MemoryStore
from app.rag.data_manager import RagDataManager
from app.runtimes.manager import build_runtime_manager_report
from app.runtimes.telemetry import build_runtime_execution_profile, initialize_runtime_state
from app.state import AgentRunResponse, ArtifactRef, TaskRoute


class InfrastructureModuleTests(unittest.TestCase):
    def test_agent_protocol_envelope_is_stable_json_payload(self) -> None:
        envelope = build_agent_envelope(
            run_id="run-1",
            conversation_id="conv-1",
            sender="SupervisorAgent",
            receiver="ComputeAgent",
            message_type="route_decision",
            payload_schema="TaskRoute/v1",
            payload={"route_name": "phase_diagram.generate", "confidence": 0.9},
            confidence=0.9,
        )

        payload = envelope.model_dump(mode="json")

        self.assertEqual(payload["protocol_version"], "agent-protocol/v1")
        self.assertEqual(payload["sender"], "SupervisorAgent")
        self.assertEqual(payload["receiver"], "ComputeAgent")
        self.assertEqual(payload["payload_schema"], "TaskRoute/v1")
        self.assertEqual(payload["payload"]["route_name"], "phase_diagram.generate")

    def test_artifact_service_writes_provenance_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ArtifactService(root_dir=Path(tmp))
            artifact_path = service.get_artifact_path("run-prov", "result.txt")
            artifact_path.write_text("deterministic result", encoding="utf-8")
            response = AgentRunResponse(
                success=True,
                run_id="run-prov",
                conversation_id="conv-prov",
                route=TaskRoute(
                    name="conversation.answer",
                    compute_domain="none",
                    selected_tool="chat",
                    decision_source="unit_test",
                    decision_confidence=1.0,
                ),
                final_message="ok",
                artifacts=[ArtifactRef(kind="text", name="result.txt", path=str(artifact_path))],
                summary={"request_message": "hello"},
                metadata={
                    "unit": "test",
                    "runtime_profile": {
                        "schema_version": "runtime-profile/v1",
                        "runtime_name": "UnitRuntime",
                        "duration_seconds": 0.01,
                    },
                },
            )

            service.write_run_summary(response)
            record = service.load_run_summary("run-prov")
            provenance_path = service.get_artifact_path("run-prov", "provenance.json")
            manifest_path = service.get_artifact_path("run-prov", "artifact_manifest.json")

            self.assertIsNotNone(record)
            self.assertTrue(provenance_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertIn("provenance", record.metadata)
            self.assertIn("artifact_manifest_path", record.metadata)
            artifact_digest = record.metadata["provenance"]["artifacts"][0]
            self.assertEqual(artifact_digest["name"], "result.txt")
            self.assertEqual(len(artifact_digest["sha256"]), 64)
            self.assertEqual(record.metadata["provenance"]["runtime_profile"]["runtime_name"], "UnitRuntime")
            manifest = manifest_path.read_text(encoding="utf-8")
            self.assertIn("artifact-manifest/v1", manifest)
            self.assertIn("result.txt", manifest)

    def test_artifact_inventory_and_cleanup_dry_run_report_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ArtifactService(root_dir=Path(tmp))
            response = AgentRunResponse(
                success=True,
                run_id="run-cleanup",
                conversation_id="conv-cleanup",
                route=TaskRoute(name="conversation.answer", compute_domain="none"),
                final_message="ok",
                metadata={"request_id": "req-cleanup"},
            )
            service.write_run_summary(response)

            inventory = service.artifact_inventory()
            self.assertEqual(inventory["run_count"], 1)
            self.assertGreaterEqual(inventory["total_size_bytes"], 1)

            dry_report = service.cleanup_runs(keep_latest=0, max_age_days=0, dry_run=True)
            self.assertEqual(dry_report["candidate_count"], 1)
            self.assertEqual(dry_report["deleted_count"], 0)
            self.assertTrue(service.get_run_dir("run-cleanup").exists())

    def test_structured_observability_log_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings, "tmp_dir", Path(tmp)):
                log_event("unit.test", request_id="req-1", run_id="run-1", conversation_id="conv-1", message="hello")
                path = structured_log_path()

            self.assertTrue(path.exists())
            payload = path.read_text(encoding="utf-8").strip()
            self.assertIn('"event": "unit.test"', payload)
            self.assertIn('"request_id": "req-1"', payload)

    def test_rag_data_manager_reports_active_collections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = RagDataManager(benchmark_path=Path(tmp) / "missing.json")
            report = manager.inventory()

        collections = {collection.name: collection for collection in report.collections}
        self.assertIn("materials_rag", collections)
        self.assertIn("thermo_rag", collections)
        self.assertGreaterEqual(collections["materials_rag"].document_count, 100)
        self.assertGreaterEqual(collections["thermo_rag"].document_count, 1)
        self.assertIn("bm25_sparse", collections["materials_rag"].retrieval_modes)
        self.assertIn("sqlite_vec_dense_knn", collections["materials_rag"].retrieval_modes)
        self.assertEqual(collections["materials_rag"].vector_store_backend, "sqlite_vec")
        self.assertTrue(collections["materials_rag"].vector_store_path.endswith("vector_store.sqlite3"))
        self.assertFalse(report.benchmark.available)

    def test_memory_profile_exposes_short_and_long_term_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(root_dir=Path(tmp))
            profile = store.profile("conv-memory")

        self.assertEqual(profile["short_term"]["module"], "ShortTermMemoryStore")
        self.assertEqual(profile["long_term"]["module"], "LongTermMemoryStore")
        self.assertEqual(profile["storage"]["primary"], "sqlite")
        self.assertIn("retention_policy", profile["short_term"])
        self.assertIn("compression_method", profile["long_term"])

    def test_runtime_telemetry_builds_profile_from_state(self) -> None:
        state = {"run_id": "runtime-1", "trace": [], "artifacts": [], "review": {"passed": True}}
        initialize_runtime_state(state, runtime_name="PhaseDiagramRuntime", capability_tags=["pycalphad", "tdb_registry"])

        profile = build_runtime_execution_profile(
            state,
            success=True,
            termination_reason="review_passed",
            result_profile={"trust_level": "high", "warnings": []},
        )

        self.assertEqual(profile["schema_version"], "runtime-profile/v1")
        self.assertEqual(profile["runtime_name"], "PhaseDiagramRuntime")
        self.assertEqual(profile["status"], "completed")
        self.assertEqual(profile["review_passed"], True)
        self.assertIn("pycalphad", profile["capability_tags"])

    def test_runtime_manager_reports_phase_and_lammps_runtimes(self) -> None:
        report = build_runtime_manager_report()

        runtimes = {runtime.name: runtime for runtime in report.runtimes}
        self.assertIn("PhaseDiagramRuntime", runtimes)
        self.assertIn("LammpsRuntime", runtimes)
        self.assertEqual(runtimes["PhaseDiagramRuntime"].compute_domain, "phase_diagram")
        self.assertEqual(runtimes["LammpsRuntime"].compute_domain, "lammps")
        self.assertIn("phase_diagram_codegen", runtimes["PhaseDiagramRuntime"].default_tool_chain)
        self.assertIn("lammps_execute", runtimes["LammpsRuntime"].default_tool_chain)

    def test_system_diagnostics_reports_full_health_surface(self) -> None:
        report = build_system_diagnostics()
        names = {check.name for check in report.checks}

        self.assertIn("Config Center", names)
        self.assertIn("LLM / Multimodal", names)
        self.assertIn("Embedding / Vector Retrieval", names)
        self.assertIn("RAG Knowledge Bases", names)
        self.assertIn("SQLite Memory", names)
        self.assertIn("Artifact Lifecycle", names)
        self.assertIn("Observability Logs", names)
        self.assertIn("Benchmark Report", names)
        embedding_check = next(check for check in report.checks if check.name == "Embedding / Vector Retrieval")
        self.assertEqual(embedding_check.details["vector_store_backend"], "sqlite_vec")
        self.assertTrue(embedding_check.details["sqlite_vec_version"])

    def test_runtime_config_splits_secrets_into_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "llm_config.json"
            env_path = Path(tmp) / ".env"
            with patch("app.config.DEFAULT_JSON_FILE", config_path):
                update_runtime_config_file({"llm_model": "unit-model", "llm_api_key": "plain-test-key"}, config_file=config_path)
                update_runtime_env_file({"llm_api_key": "plain-test-key"}, env_file=env_path)
                persisted = read_runtime_config_file(config_path)

                self.assertEqual(persisted["llm_model"], "unit-model")
                self.assertNotIn("llm_api_key", persisted)

                next_settings = build_settings(environ={}, env_files=(env_path,), json_file=config_path)
                self.assertEqual(next_settings.llm_model, "unit-model")
                self.assertEqual(next_settings.llm_api_key, "plain-test-key")

    def test_env_files_override_json_config_and_process_env_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            config_path = root / "llm_config.json"
            env_path.write_text(
                "\n".join(
                    [
                        "PHASE_DIAGRAM_LLM_MODEL=legacy-env-model",
                        "PHASE_DIAGRAM_LLM_SUPPORTS_VISION=false",
                    ]
                ),
                encoding="utf-8",
            )
            update_runtime_config_file(
                {
                    "llm_model": "canonical-json-model",
                    "llm_supports_vision": True,
                    "llm_supports_embedding": False,
                },
                config_file=config_path,
            )

            json_settings = build_settings(environ={}, env_files=(env_path,), json_file=config_path)
            process_settings = build_settings(
                environ={"PHASE_DIAGRAM_LLM_MODEL": "process-env-model"},
                env_files=(env_path,),
                json_file=config_path,
            )

        self.assertEqual(json_settings.llm_model, "legacy-env-model")
        self.assertFalse(json_settings.llm_supports_vision)
        self.assertFalse(json_settings.llm_supports_embedding)
        self.assertEqual(process_settings.llm_model, "process-env-model")

    def test_runtime_llm_update_writes_api_key_to_env_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "llm_config.json"
            env_path = Path(tmp) / ".env"
            original_model = settings.llm_model
            original_key = settings.llm_api_key
            try:
                with patch("app.config.DEFAULT_JSON_FILE", config_path), patch("app.config.DEFAULT_ENV_FILES", (env_path,)):
                    update_runtime_llm_config({"llm_model": "persisted-model", "llm_api_key": "plain-test-key"})
                    persisted = read_runtime_config_file(config_path)
                    next_settings = build_settings(environ={}, env_files=(env_path,), json_file=config_path)

                self.assertEqual(persisted["llm_model"], "persisted-model")
                self.assertNotIn("llm_api_key", persisted)
                self.assertEqual(next_settings.llm_model, "persisted-model")
                self.assertEqual(next_settings.llm_api_key, "plain-test-key")
            finally:
                settings.llm_model = original_model
                settings.llm_api_key = original_key

    def test_runtime_update_writes_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "llm_config.json"
            original_model = settings.llm_model
            original_supports_vision = settings.llm_supports_vision
            original_lammps = load_lammps_config()
            try:
                with patch("app.config.DEFAULT_JSON_FILE", config_path):
                    update_runtime_llm_config({"llm_model": "persisted-model", "llm_supports_vision": False})
                    update_runtime_lammps_config(
                        {
                            "lammps_command": "/tmp/unit-lmp",
                            "max_retries": 3,
                            "lammps_preflight_dag_enabled": True,
                            "lammps_red_blue_review_enabled": False,
                        }
                    )
                    persisted = read_runtime_config_file(config_path)

                self.assertEqual(persisted["llm_model"], "persisted-model")
                self.assertFalse(persisted["llm_supports_vision"])
                self.assertEqual(persisted["lammps_command"], "/tmp/unit-lmp")
                self.assertEqual(persisted["max_retries"], 3)
                self.assertTrue(persisted["lammps_preflight_dag_enabled"])
                self.assertFalse(persisted["lammps_red_blue_review_enabled"])
            finally:
                settings.llm_model = original_model
                settings.llm_supports_vision = original_supports_vision
                with patch("app.config.DEFAULT_JSON_FILE", config_path):
                    update_runtime_lammps_config(
                        {
                            "lammps_command": original_lammps.lammps_command,
                            "potentials_dir": original_lammps.potentials_dir,
                            "ovito_location": original_lammps.ovito_location,
                            "allow_mock_fallback": original_lammps.allow_mock_fallback,
                            "force_mock": original_lammps.force_mock,
                            "max_retries": original_lammps.max_retries,
                            "lammps_preflight_dag_enabled": original_lammps.lammps_preflight_dag_enabled,
                            "lammps_red_blue_review_enabled": original_lammps.lammps_red_blue_review_enabled,
                        }
                    )


if __name__ == "__main__":
    unittest.main()
