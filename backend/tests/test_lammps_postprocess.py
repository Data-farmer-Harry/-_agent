from __future__ import annotations

import tempfile
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import patch

from app.lammps.config import detect_ovito_backend
from app.lammps.postprocess import generate_diffusion_trajectory_if_applicable, resolve_dump_path


class LammpsPostprocessTests(unittest.TestCase):
    def test_real_heating_tasks_for_supported_metals_enter_diffusion_branch(self) -> None:
        for material in ("Al", "Cu", "Ni"):
            with self.subTest(material=material):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    output_dir = Path(tmp_dir)
                    dump_name = f"{material.lower()}_heating_trajectory.lammpstrj"
                    (output_dir / dump_name).write_text("ITEM: TIMESTEP\n0\n", encoding="utf-8")
                    with patch("app.lammps.postprocess.detect_ovito_backend", return_value={"ovito_available": False, "ovito_backend": "not found", "ovito_location": ""}):
                        status = generate_diffusion_trajectory_if_applicable(
                            output_dir=output_dir,
                            request={"material": material, "task_type": "heating", "dump_file": dump_name},
                            mode="real",
                        )

                self.assertTrue(status["supported_task"])
                self.assertFalse(status["generated"])
                self.assertEqual(status["reason"], "OVITO not installed or not detected")

    def test_resolve_dump_path_prefers_requested_dump_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            requested = output_dir / "custom_trajectory.lammpstrj"
            fallback = output_dir / "dump.atom"
            fallback.write_text("fallback", encoding="utf-8")
            requested.write_text("requested", encoding="utf-8")

            resolved = resolve_dump_path(output_dir, "custom_trajectory.lammpstrj")

        self.assertEqual(resolved.name, "custom_trajectory.lammpstrj")

    def test_detect_ovito_backend_does_not_treat_desktop_binary_as_script_runner(self) -> None:
        def fake_which(candidate: str) -> str | None:
            if candidate == "ovito":
                return "/opt/test/bin/ovito"
            return None

        with patch("app.lammps.config.shutil.which", side_effect=fake_which), patch("importlib.util.find_spec", return_value=None):
            status = detect_ovito_backend()

        self.assertFalse(status["ovito_available"])
        self.assertEqual(status["ovito_location"], "/opt/test/bin/ovito")
        self.assertIn("no scripting", str(status["ovito_backend"]))

    def test_detect_ovito_backend_prefers_python_module_when_no_ovitos_runner_exists(self) -> None:
        module_spec = ModuleSpec("ovito", loader=None, is_package=True)
        module_spec.submodule_search_locations = ["/tmp/site-packages/ovito"]

        with patch("app.lammps.config.shutil.which", return_value=None), patch("importlib.util.find_spec", return_value=module_spec):
            status = detect_ovito_backend()

        self.assertTrue(status["ovito_available"])
        self.assertEqual(status["ovito_backend"], "python module")
        self.assertEqual(status["ovito_location"], "/tmp/site-packages/ovito")


if __name__ == "__main__":
    unittest.main()
