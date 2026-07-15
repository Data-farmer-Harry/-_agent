from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import build_settings
from app.desktop import attach_desktop_frontend


class DesktopPackagingTests(unittest.TestCase):
    def test_desktop_runtime_uses_writable_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_file = root / "config.json"
            config_file.write_text('{"llm_model":"desktop-model"}', encoding="utf-8")
            settings = build_settings(
                environ={
                    "PHASE_DIAGRAM_TMP_DIR": str(root / "outputs"),
                    "PHASE_DIAGRAM_PYTHON_EXECUTABLE": "bundled-python",
                },
                env_files=(),
                json_file=config_file,
            )

        self.assertEqual(settings.tmp_dir, (root / "outputs").resolve())
        self.assertEqual(settings.python_executable, "bundled-python")
        self.assertEqual(settings.llm_model, "desktop-model")

    def test_desktop_frontend_serves_assets_and_spa_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            frontend = Path(tmp_dir)
            (frontend / "assets").mkdir()
            (frontend / "index.html").write_text("<main>MatterLab Desktop</main>", encoding="utf-8")
            (frontend / "assets" / "app.js").write_text("window.ready=true", encoding="utf-8")
            app = FastAPI()

            @app.get("/api/health")
            def health() -> dict[str, str]:
                return {"status": "ok"}

            self.assertTrue(attach_desktop_frontend(app, frontend))
            self.assertFalse(attach_desktop_frontend(app, frontend))
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
                self.assertEqual(client.get("/api/not-a-real-endpoint").status_code, 404)
                self.assertIn("MatterLab Desktop", client.get("/conversation/example").text)
                self.assertIn("window.ready", client.get("/assets/app.js").text)


if __name__ == "__main__":
    unittest.main()
