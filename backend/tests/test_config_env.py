from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import build_settings


class ConfigEnvLoadingTests(unittest.TestCase):
    def test_build_settings_reads_backend_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "PHASE_DIAGRAM_LLM_API_BASE_URL=https://example.test/v1",
                        "PHASE_DIAGRAM_LLM_API_KEY=file-key",
                        "PHASE_DIAGRAM_LLM_MODEL=qwen-test",
                        "PHASE_DIAGRAM_LLM_REQUEST_TIMEOUT_SECONDS=33",
                        "PHASE_DIAGRAM_LLM_MAX_TOKENS=1234",
                    ]
                ),
                encoding="utf-8",
            )

            settings = build_settings(environ={}, env_file=env_file)

        self.assertEqual(settings.llm_api_base_url, "https://example.test/v1")
        self.assertEqual(settings.llm_api_key, "file-key")
        self.assertEqual(settings.llm_model, "qwen-test")
        self.assertEqual(settings.llm_request_timeout_seconds, 33)
        self.assertEqual(settings.llm_max_tokens, 1234)

    def test_process_environment_overrides_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "PHASE_DIAGRAM_LLM_API_BASE_URL=https://example.test/v1",
                        "PHASE_DIAGRAM_LLM_API_KEY=file-key",
                        "PHASE_DIAGRAM_LLM_MODEL=qwen-test",
                    ]
                ),
                encoding="utf-8",
            )

            settings = build_settings(
                environ={
                    "PHASE_DIAGRAM_LLM_API_KEY": "process-key",
                    "PHASE_DIAGRAM_LLM_MODEL": "process-model",
                },
                env_file=env_file,
            )

        self.assertEqual(settings.llm_api_base_url, "https://example.test/v1")
        self.assertEqual(settings.llm_api_key, "process-key")
        self.assertEqual(settings.llm_model, "process-model")
