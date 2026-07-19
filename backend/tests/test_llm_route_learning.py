from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.core.llm_route_learning import LearnedPolicyConfig, NeuralRouteModel, extract_route_features, feature_names
from app.core.llm_routing import LLMRoute, LLMRouter, LLMRoutingConfig
from benchmarks.train_llm_route_mlp import (
    build_simulated_production_telemetry,
    build_synthetic_route_dataset,
    load_telemetry_route_dataset,
    train_route_mlp,
)


class LearnedLLMRouteTests(unittest.TestCase):
    def test_shadow_policy_records_neural_recommendation_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_path = self._constant_prediction_model("strong", Path(tmp) / "model.json")
            router = LLMRouter(
                LLMRoutingConfig(
                    learned_policy=LearnedPolicyConfig(enabled=True, mode="shadow", model_path=str(model_path)),
                    routes={"fast": LLMRoute(), "strong": LLMRoute()},
                    fallbacks={"fast": "", "strong": ""},
                )
            )

            decision = router.decide(
                system_prompt="Answer briefly.",
                user_prompt="你好，请用一句话介绍系统。",
                max_tokens=250,
                temperature=0.1,
            )

        self.assertEqual(decision.tier, "fast")
        self.assertTrue(any(reason.startswith("learned_shadow:strong") for reason in decision.reasons))

    def test_guarded_policy_can_upgrade_low_risk_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_path = self._constant_prediction_model("balanced", Path(tmp) / "model.json")
            router = LLMRouter(
                LLMRoutingConfig(
                    learned_policy=LearnedPolicyConfig(enabled=True, mode="guarded", model_path=str(model_path)),
                    routes={"fast": LLMRoute(), "balanced": LLMRoute()},
                    fallbacks={"fast": "", "balanced": ""},
                )
            )

            decision = router.decide(
                system_prompt="Answer briefly.",
                user_prompt="你好，请用一句话介绍系统。",
                max_tokens=250,
                temperature=0.1,
            )

        self.assertEqual(decision.tier, "balanced")
        self.assertIn("learned_override:fast->balanced", decision.reasons)

    def test_guarded_policy_cannot_downgrade_lammps_minimum_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_path = self._constant_prediction_model("fast", Path(tmp) / "model.json")
            router = LLMRouter(
                LLMRoutingConfig(
                    learned_policy=LearnedPolicyConfig(
                        enabled=True,
                        mode="guarded",
                        model_path=str(model_path),
                        allow_downgrade=True,
                    ),
                    routes={"fast": LLMRoute(), "strong": LLMRoute()},
                    fallbacks={"fast": "", "strong": ""},
                    capability_min_tiers={"lammps": "strong"},
                )
            )

            decision = router.decide(
                system_prompt="You repair and review a structured LAMMPS request. Return JSON only.",
                user_prompt="LAMMPS Cu EAM NPT failed. Use MODIFY and VERIFY patch for physical consistency.",
                max_tokens=1200,
                temperature=0.1,
                capability="lammps.review",
            )

        self.assertEqual(decision.tier, "strong")
        self.assertIn("learned_guard_clamped:fast->strong", decision.reasons)

    def test_small_synthetic_training_run_reports_metrics(self) -> None:
        rows = build_synthetic_route_dataset(samples_per_class=14, seed=7)
        difficulties = {str(row.get("difficulty")) for row in rows}

        model, metrics, splits = train_route_mlp(rows, hidden_dim=12, epochs=45, seed=7)

        self.assertEqual(model.labels, ("fast", "balanced", "strong", "vision"))
        self.assertTrue({"mixed", "adversarial"}.issubset(difficulties))
        self.assertGreater(len(splits["train"]), len(splits["test"]))
        self.assertIn("accuracy", metrics["test"])
        self.assertIn("macro_f1", metrics["test"])
        self.assertIn("confusion_matrix", metrics["test"])
        self.assertIn("probe", metrics)
        self.assertIn("dataset_distribution", metrics["metadata"])
        self.assertIn("calibration", metrics)
        self.assertGreater(model.calibration_temperature, 0.0)
        self.assertGreater(model.ood_threshold, 0.0)
        self.assertIn("expected_calibration_error", metrics["test"])

    def test_simulated_production_rows_are_explicitly_marked_and_privacy_safe(self) -> None:
        rows = build_simulated_production_telemetry(samples=120, seed=19)

        self.assertEqual(len(rows), 120)
        self.assertTrue(all(row["source"] == "simulated_production_telemetry" for row in rows))
        self.assertTrue(all(row["simulation"]["not_real_user_traffic"] is True for row in rows))
        self.assertTrue(all("feature_values" in row for row in rows))
        self.assertTrue(all("system_prompt" not in row and "user_prompt" not in row for row in rows))
        self.assertGreaterEqual(len({str(row["label"]) for row in rows}), 3)

    def test_calibrated_recommendation_exposes_margin_entropy_and_ood(self) -> None:
        rows = [
            *build_synthetic_route_dataset(samples_per_class=12, seed=23),
            *build_simulated_production_telemetry(samples=180, seed=24),
        ]
        model, _metrics, _splits = train_route_mlp(rows, hidden_dim=12, epochs=45, seed=23)
        features = extract_route_features(
            system_prompt="Repair and review a LAMMPS request.",
            user_prompt="LAMMPS Cu EAM NVT failed; verify locked constraints.",
            max_tokens=1100,
            temperature=0.1,
            capability="lammps.review",
        )

        recommendation = model.recommend(features)

        self.assertGreaterEqual(recommendation.probability_margin, 0.0)
        self.assertLessEqual(recommendation.probability_margin, 1.0)
        self.assertGreaterEqual(recommendation.normalized_entropy, 0.0)
        self.assertLessEqual(recommendation.normalized_entropy, 1.0)
        self.assertGreaterEqual(recommendation.ood_score, 0.0)

    def test_telemetry_rows_train_without_prompt_text(self) -> None:
        features = extract_route_features(
            system_prompt="Route current request.",
            user_prompt="private prompt should not be stored",
            max_tokens=900,
            temperature=0.1,
            capability="supervisor.router",
            multimodal=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.jsonl"
            events_path.write_text(
                json.dumps(
                    {
                        "event": "llm.routing_call",
                        "success": True,
                        "tier": "balanced",
                        "capability": "supervisor.router",
                        "requested_max_tokens": 900,
                        "temperature": 0.1,
                        "multimodal": False,
                        "prompt_hash": "abc123",
                        "run_id": "run1",
                        "request_id": "req1",
                        "feature_schema": "llm-route-features/v1",
                        "feature_values": {
                            name: value
                            for name, value in zip(features.names, features.values, strict=True)
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            telemetry_rows = load_telemetry_route_dataset(events_path)
            rows = [*build_synthetic_route_dataset(samples_per_class=8, seed=11), *telemetry_rows]
            model, metrics, _splits = train_route_mlp(rows, hidden_dim=10, epochs=30, seed=11)

        self.assertEqual(len(telemetry_rows), 1)
        self.assertNotIn("system_prompt", telemetry_rows[0])
        self.assertNotIn("user_prompt", telemetry_rows[0])
        self.assertEqual(telemetry_rows[0]["label"], "balanced")
        self.assertEqual(model.feature_names, feature_names())
        self.assertGreaterEqual(metrics["test"]["accuracy"], 0.0)
        self.assertEqual(metrics["metadata"]["dataset_distribution"]["source"]["observability_telemetry"], 1)

    @staticmethod
    def _constant_prediction_model(label: str, path: Path) -> Path:
        labels = ("fast", "balanced", "strong", "vision")
        input_dim = len(feature_names())
        hidden_dim = 4
        bias2 = np.full((len(labels),), -4.0)
        bias2[labels.index(label)] = 4.0
        model = NeuralRouteModel(
            labels=labels,
            feature_names=feature_names(),
            feature_mean=np.zeros((input_dim,), dtype=float),
            feature_std=np.ones((input_dim,), dtype=float),
            weights1=np.zeros((input_dim, hidden_dim), dtype=float),
            bias1=np.ones((hidden_dim,), dtype=float),
            weights2=np.zeros((hidden_dim, len(labels)), dtype=float),
            bias2=bias2,
            metadata={"test_model": True},
        )
        model.save(path)
        return path


if __name__ == "__main__":
    unittest.main()
