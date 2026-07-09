from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.secret_scan import scan_repository, scan_text


class SecretScanTests(unittest.TestCase):
    def test_detects_high_confidence_secret_values(self) -> None:
        text = "OPENROUTER_API_KEY=sk-or-v1-" + "a" * 40

        issues = scan_text("backend/configs/local.env", text)

        self.assertTrue(any(issue.rule_id == "openrouter_key" for issue in issues))
        self.assertTrue(any(issue.rule_id == "sensitive_assignment" for issue in issues))

    def test_allows_empty_and_placeholder_example_values(self) -> None:
        text = "\n".join(
            [
                "PHASE_DIAGRAM_LLM_API_KEY=",
                "PHASE_DIAGRAM_LLM_API_KEY=your-api-key",
                "settings.materials_rag_embedding_api_key = \"embedding-key\"",
            ]
        )

        self.assertEqual(scan_text("backend/.env.example", text), [])

    def test_detects_private_workstation_paths(self) -> None:
        issues = scan_text("backend/benchmarks/datasets/demo.jsonl", '{"asset_path": "/Users/harry/Desktop/private.png"}')

        self.assertEqual([issue.rule_id for issue in issues], ["private_path"])

    def test_allows_documented_private_path_patterns_in_scanner_tests(self) -> None:
        self.assertEqual(
            scan_text(
                "backend/tests/test_benchmark_versioning.py",
                'uploaded_assets=[{"path": "/Users/harry/Desktop/local-fixture.png"}]',
            ),
            [],
        )

    def test_flags_trackable_non_example_env_files(self) -> None:
        issues = scan_text(".env.production", "PHASE_DIAGRAM_LLM_API_KEY=your-api-key")

        self.assertEqual([issue.rule_id for issue in issues], ["tracked_env_file"])

    def test_repository_scan_skips_binary_and_ignored_style_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            safe = root / "README.md"
            unsafe = root / "src" / "config.py"
            skipped = root / "node_modules" / "package" / "index.js"
            image = root / "image.jpg"
            unsafe.parent.mkdir()
            skipped.parent.mkdir(parents=True)
            safe.write_text("PHASE_DIAGRAM_LLM_API_KEY=your-api-key\n", encoding="utf-8")
            unsafe.write_text("api_key = 'sk-or-v1-" + "b" * 40 + "'\n", encoding="utf-8")
            skipped.write_text("api_key = 'sk-or-v1-" + "c" * 40 + "'\n", encoding="utf-8")
            image.write_bytes(b"\xff\xd8fake sk-or-v1-" + b"d" * 40)

            issues = scan_repository(root, [safe, unsafe, skipped, image])

        self.assertEqual(len(issues), 2)
        self.assertEqual({issue.rule_id for issue in issues}, {"openrouter_key", "sensitive_assignment"})
        self.assertTrue(all(issue.path == "src/config.py" for issue in issues))


if __name__ == "__main__":
    unittest.main()
