from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.skills.models import SkillDecision, SkillSpec
from app.skills.registry import SkillRegistry


@dataclass
class SkillRouter:
    registry: SkillRegistry
    max_skills: int = 2
    max_context_chars: int = 6000

    def decide(self, state: dict[str, Any]) -> SkillDecision:
        message = state["request"].message
        lowered = message.lower()
        scored: list[tuple[float, SkillSpec, list[str]]] = []
        for skill in self.registry.list_skills():
            matches: list[str] = []
            for trigger in skill.triggers:
                token = trigger.lower()
                if token and (token in lowered or trigger in message):
                    matches.append(trigger)
            if not matches:
                continue
            score = min(1.0, 0.45 + 0.15 * len(matches))
            scored.append((score, skill, matches))
        if not scored:
            return SkillDecision(selected_skills=[], confidence=0.0, reason="no_skill_trigger_matched")
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [item[1] for item in scored[: self.max_skills]]
        matched = [match for _, _, matches in scored[: self.max_skills] for match in matches[:3]]
        return SkillDecision(
            selected_skills=selected,
            confidence=max(score for score, _, _ in scored),
            reason=f"matched triggers: {', '.join(matched)}",
        )

    def build_context(self, decision: SkillDecision) -> str:
        if not decision.selected_skills:
            return "(none)"
        chunks: list[str] = []
        remaining = self.max_context_chars
        for skill in decision.selected_skills:
            header = (
                f"Skill: {skill.name}\n"
                f"ID: {skill.skill_id}\n"
                f"Description: {skill.description}\n"
                f"Preferred tools: {', '.join(skill.preferred_tools) if skill.preferred_tools else 'none'}\n"
                "Instructions:\n"
            )
            content = skill.content.strip()
            chunk = header + content
            if len(chunk) > remaining:
                chunk = chunk[: max(0, remaining - 1)] + "…"
            chunks.append(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                break
        return "\n\n---\n\n".join(chunks)
