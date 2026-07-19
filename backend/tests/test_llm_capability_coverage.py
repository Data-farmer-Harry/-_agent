from __future__ import annotations

import ast
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (
    BACKEND_ROOT / "app",
)
LLM_CALL_METHODS = {
    "chat_text",
    "chat_json",
    "chat_multimodal_text",
    "chat_multimodal_json",
    "require_configured",
}


class LlmCapabilityCoverageTests(unittest.TestCase):
    def test_every_direct_llm_call_declares_capability(self) -> None:
        missing: list[str] = []
        empty: list[str] = []
        raw_literals: list[str] = []

        for root in RUNTIME_ROOTS:
            for path in sorted(root.rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if node.func.attr not in LLM_CALL_METHODS:
                        continue
                    capability = next((item.value for item in node.keywords if item.arg == "capability"), None)
                    location = f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}"
                    if capability is None:
                        missing.append(location)
                    elif isinstance(capability, ast.Constant) and not str(capability.value or "").strip():
                        empty.append(location)
                    elif isinstance(capability, ast.Constant):
                        raw_literals.append(location)

        self.assertEqual(missing, [], f"LLM calls missing capability=: {missing}")
        self.assertEqual(empty, [], f"LLM calls with an empty capability=: {empty}")
        self.assertEqual(raw_literals, [], f"LLM calls must use the centralized LLMCapability registry: {raw_literals}")

    def test_critical_capabilities_resolve_to_required_minimum_tiers(self) -> None:
        from app.core.llm_routing import LLMRouter

        router = LLMRouter()
        expected = {
            "memory.summary": "fast",
            "prompt.suggest": "fast",
            "supervisor.route": "balanced",
            "rag.answer": "balanced",
            "phase.request.parse": "strong",
            "phase.codegen": "strong",
            "phase.codegen.repair": "strong",
            "phase.review": "strong",
            "lammps.request.parse": "strong",
            "lammps.request.repair": "strong",
            "lammps.review": "strong",
            "vision.recognition": "vision",
        }

        actual = {
            capability: router._minimum_tier_for_capability(capability)  # noqa: SLF001 - routing contract regression.
            for capability in expected
        }
        self.assertEqual(actual, expected)
