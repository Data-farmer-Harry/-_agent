from __future__ import annotations

import time
import unittest
from server.combined_server import (
    _parse_range_header,
    chat_response,
    get_lammps_template_schema_response,
    get_llm_config_response,
    get_lammps_config_response,
    get_run_response,
    latest_run_response,
    list_runs_response,
    list_artifacts_response,
    run_response,
    update_lammps_config_response,
    update_llm_config_response,
)


class ServerContractTests(unittest.TestCase):
    def test_parse_range_header(self) -> None:
        self.assertEqual(_parse_range_header("bytes=0-99", 1000), (0, 99))
        self.assertEqual(_parse_range_header("bytes=100-", 1000), (100, 999))
        self.assertEqual(_parse_range_header("bytes=-50", 1000), (950, 999))
        self.assertIsNone(_parse_range_header("bytes=200-100", 1000))
        self.assertIsNone(_parse_range_header("items=0-99", 1000))

    def test_chat_and_run_contract_without_socket(self) -> None:
        update_lammps_config_response(
            {
                "lammps_command": "",
                "potentials_dir": "",
                "allow_mock_fallback": True,
                "force_mock": True,
            }
        )
        chat_body, chat_status = chat_response(
            {"message": "Run an EAM equilibration for Al at 500K for 5000 steps."}
        )
        self.assertEqual(chat_status, 200)
        self.assertFalse(chat_body["needs_input"])
        self.assertTrue(chat_body["can_run"])

        run_body, run_status = run_response(
            {
                "user_query": chat_body["state"]["user_query"],
                "normalized_request": chat_body["state"]["normalized_request"],
            }
        )
        self.assertEqual(run_status, 202)
        run_id = run_body["run_id"]

        status_body = {}
        for _ in range(40):
            status_body, status_code = get_run_response(run_id)
            self.assertEqual(status_code, 200)
            if status_body["status"] == "completed":
                break
            time.sleep(0.1)

        self.assertEqual(status_body["status"], "completed")
        self.assertIn("plot.png", status_body["artifacts"])

        artifacts_body, artifacts_status = list_artifacts_response(run_id)
        self.assertEqual(artifacts_status, 200)
        self.assertIn("report.md", artifacts_body["artifacts"])

        latest_body, latest_status = latest_run_response()
        self.assertEqual(latest_status, 200)
        self.assertIn("run_id", latest_body)
        self.assertIn("summary", latest_body)

        runs_body, runs_status = list_runs_response()
        self.assertEqual(runs_status, 200)
        self.assertIn("runs", runs_body)
        self.assertTrue(runs_body["runs"])

    def test_run_rejects_unreasonable_request(self) -> None:
        run_body, run_status = run_response(
            {
                "user_query": "Run an EAM equilibration for Al at 500K for 500099 steps.",
                "normalized_request": {
                    "material": "Al",
                    "potential_family": "eam",
                    "task_type": "equilibration",
                    "temperature": 500,
                    "steps": 500099,
                },
            }
        )
        self.assertEqual(run_status, 400)
        self.assertIn("validation", run_body)

    def test_llm_config_roundtrip(self) -> None:
        body, status = update_llm_config_response(
            {
                "base_url": "https://example.test/v1",
                "model": "demo-model",
                "api_key": "sk-test-12345678",
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["api_key_set"])
        self.assertEqual(body["base_url"], "https://example.test/v1")
        self.assertEqual(body["model"], "demo-model")

        current, get_status = get_llm_config_response()
        self.assertEqual(get_status, 200)
        self.assertEqual(current["base_url"], "https://example.test/v1")
        self.assertEqual(current["model"], "demo-model")
        self.assertTrue(current["api_key_set"])

    def test_lammps_config_roundtrip(self) -> None:
        body, status = update_lammps_config_response(
            {
                "lammps_command": "/opt/homebrew/bin/lmp_serial",
                "potentials_dir": "/opt/homebrew/opt/lammps/share/lammps/potentials",
                "allow_mock_fallback": False,
                "force_mock": False,
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["lammps_command"], "/opt/homebrew/bin/lmp_serial")
        self.assertEqual(body["potentials_dir"], "/opt/homebrew/opt/lammps/share/lammps/potentials")

        current, get_status = get_lammps_config_response()
        self.assertEqual(get_status, 200)
        self.assertTrue(current["lammps_command_exists"])
        self.assertTrue(current["potentials_dir_exists"])
        self.assertIn("ovito_available", current)
        self.assertIn("ovito_backend", current)
        self.assertIn("ovito_location", current)

    def test_template_schema_contract(self) -> None:
        body, status = get_lammps_template_schema_response()
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"]["type"], "layered_form")
        self.assertTrue(body["schema"]["sections"])
        self.assertEqual(body["schema"]["sections"][1]["fields"][0]["key"], "task_type")


if __name__ == "__main__":
    unittest.main()
