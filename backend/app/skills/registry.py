from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import BACKEND_ROOT
from app.skills.models import SkillSpec


SKILLS_ROOT = BACKEND_ROOT / "skills"


@dataclass
class SkillRegistry:
    skills: dict[str, SkillSpec] = field(default_factory=dict)

    def register(self, skill: SkillSpec) -> None:
        if not skill.skill_id:
            raise ValueError("Skill id must be non-empty.")
        self.skills[skill.skill_id] = skill

    def get(self, skill_id: str) -> SkillSpec | None:
        return self.skills.get(skill_id)

    def list_skills(self) -> list[SkillSpec]:
        return list(self.skills.values())

    def public_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "description": skill.description,
                "triggers": skill.triggers,
                "preferred_tools": skill.preferred_tools,
                "source_path": str(skill.source_path) if skill.source_path else "",
            }
            for skill in self.list_skills()
        ]


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    _, _, rest = text.partition("---\n")
    header, separator, body = rest.partition("\n---\n")
    if not separator:
        return {}, text
    data: dict[str, Any] = {}
    current_list_key = ""
    for line in header.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key:
            data.setdefault(current_list_key, []).append(stripped[2:].strip().strip('"'))
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if value == "":
            data[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            items = [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
            data[key] = items
            current_list_key = ""
        else:
            data[key] = value.strip('"').strip("'")
            current_list_key = ""
    return data, body.strip()


def _skill_id_from_path(path: Path) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", path.parent.name.lower()).strip("_")


def load_skill_from_file(path: Path) -> SkillSpec:
    raw = path.read_text(encoding="utf-8")
    metadata, content = _parse_front_matter(raw)
    skill_id = str(metadata.get("id") or _skill_id_from_path(path)).strip()
    name = str(metadata.get("name") or skill_id).strip()
    description = str(metadata.get("description") or "").strip()
    triggers = metadata.get("triggers", [])
    preferred_tools = metadata.get("preferred_tools", [])
    return SkillSpec(
        skill_id=skill_id,
        name=name,
        description=description,
        triggers=[str(item) for item in triggers] if isinstance(triggers, list) else [],
        preferred_tools=[str(item) for item in preferred_tools] if isinstance(preferred_tools, list) else [],
        source_path=path,
        content=content,
        metadata=metadata,
    )


def build_default_skill_registry(root: Path = SKILLS_ROOT) -> SkillRegistry:
    registry = SkillRegistry()
    if not root.exists():
        return registry
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            registry.register(load_skill_from_file(path))
        except Exception:
            continue
    return registry
