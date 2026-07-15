from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.artifacts import ArtifactService
from app.core.executor import LocalPythonExecutor
from app.core.sandbox import SandboxLimits, SandboxRunner


class SandboxRunnerTests(unittest.TestCase):
    def _runner(self, root: Path, *, timeout: float = 5.0) -> SandboxRunner:
        return SandboxRunner(
            allowed_roots=(root,),
            limits=SandboxLimits(
                timeout_seconds=timeout,
                cpu_seconds=max(2, int(timeout) + 1),
                memory_mb=2_048,
                max_processes=64,
                max_open_files=128,
                max_file_size_mb=32,
            ),
            enabled=True,
            native_enabled=False,
        )

    def test_runner_uses_clean_environment_and_marks_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            runner = self._runner(root)
            code = (
                "import os; "
                "print(os.getenv('OPENROUTER_API_KEY', 'missing')); "
                "print(os.getenv('MATTERLAB_SANDBOX', '0'))"
            )
            env_name = "OPENROUTER_" + "API_KEY"
            marker_value = "must-not-leak"
            with patch.dict(os.environ, {env_name: marker_value}, clear=False):
                result = runner.run([sys.executable, "-c", code], cwd=root)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(str(result.stdout).strip().splitlines(), ["missing", "1"])
        self.assertIsNotNone(result.sandbox)
        self.assertEqual(result.sandbox.mode, "resource_limits")

    def test_runner_rejects_working_directory_outside_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as allowed_dir, tempfile.TemporaryDirectory() as outside_dir:
            runner = self._runner(Path(allowed_dir))
            with self.assertRaisesRegex(ValueError, "outside allowed roots"):
                runner.run([sys.executable, "-c", "print('no')"], cwd=Path(outside_dir))

    def test_runner_rejects_shell_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            runner = self._runner(root)
            with self.assertRaisesRegex(ValueError, "shell=True"):
                runner.popen([sys.executable, "-c", "print('no')"], cwd=root, shell=True)

    def test_runner_enforces_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            runner = self._runner(root, timeout=0.2)
            started = time.monotonic()
            result = runner.run(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                cwd=root,
                limits=SandboxLimits(timeout_seconds=0.2, cpu_seconds=2, memory_mb=2_048),
            )
            duration = time.monotonic() - started

        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(duration, 3.0)
        self.assertIn("Sandbox timeout exceeded", str(result.stderr))

    def test_generated_python_executor_uses_same_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            artifact_service = ArtifactService(root_dir=root / "outputs")
            runner = SandboxRunner(
                allowed_roots=(root,),
                limits=SandboxLimits(timeout_seconds=5, cpu_seconds=5, memory_mb=2_048),
                enabled=True,
                native_enabled=False,
            )
            executor = LocalPythonExecutor(
                artifact_service=artifact_service,
                python_executable=sys.executable,
                sandbox_runner=runner,
            )

            result = executor.execute("sandbox-python", "from pathlib import Path\nPath('result.html').write_text('<h1>ok</h1>')")

        self.assertTrue(result.success)
        self.assertIn("phase-diagram-agent-layout", result.html_content or "")


if __name__ == "__main__":
    unittest.main()
