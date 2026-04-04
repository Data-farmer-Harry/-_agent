from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
