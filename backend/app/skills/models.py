from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    preferred_tools: list[str] = field(default_factory=list)
    source_path: Path | None = None
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillDecision:
    selected_skills: list[SkillSpec] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    @property
    def has_skills(self) -> bool:
        return bool(self.selected_skills)

    def model_dump(self) -> dict[str, Any]:
        return {
            "selected_skills": [
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "description": skill.description,
                    "preferred_tools": skill.preferred_tools,
                    "source_path": str(skill.source_path) if skill.source_path else "",
                }
                for skill in self.selected_skills
            ],
            "confidence": self.confidence,
            "reason": self.reason,
        }
