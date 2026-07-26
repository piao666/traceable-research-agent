"""In-memory Skill Registry."""

from __future__ import annotations

import logging
from pathlib import Path

from app.skills.loader import load_all_skills, validate_skill
from app.skills.models import SkillDefinition, SkillMeta
from app.tools.registry import list_tools as list_registered_tools

logger = logging.getLogger(__name__)

_skills: dict[str, SkillDefinition] = {}
_skill_meta: dict[str, SkillMeta] = {}


def _available_tool_names() -> set[str]:
    return {spec.name for spec in list_registered_tools()}


def init_skill_registry(skills_dir: Path) -> None:
    """Load and register all skills. Call once at startup after tools are registered."""
    global _skills, _skill_meta
    _skills = {}
    _skill_meta = {}

    available_tools = _available_tool_names()
    loaded = load_all_skills(skills_dir)

    for name, skill in loaded.items():
        errors = validate_skill(skill, available_tools)
        _skills[name] = skill
        _skill_meta[name] = SkillMeta(
            name=skill.name,
            version=skill.version,
            description=skill.description,
            required_tools=skill.required_tools,
            parameters={
                k: {
                    "type": p.type if hasattr(p, 'type') else str(p.get("type", "string")),
                    "required": p.required if hasattr(p, 'required') else bool(p.get("required", False)),
                    "default": p.default if hasattr(p, 'default') else p.get("default"),
                }
                for k, p in (skill.parameters or {}).items()
            },
            status="valid" if not errors else "invalid",
            error="; ".join(errors) if errors else None,
        )

    valid_count = sum(1 for m in _skill_meta.values() if m.status == "valid")
    invalid_count = sum(1 for m in _skill_meta.values() if m.status == "invalid")
    logger.info(
        "Skill registry initialized: %d loaded (%d valid, %d invalid)",
        len(_skill_meta), valid_count, invalid_count,
    )


def get_skill(name: str) -> SkillDefinition | None:
    """Return a single Skill by name."""
    return _skills.get(name)


def list_skills() -> list[SkillMeta]:
    """Return metadata for all loaded skills."""
    return list(_skill_meta.values())


def reload_skills(skills_dir: Path) -> None:
    """Re-scan the skills directory and rebuild the registry."""
    init_skill_registry(skills_dir)
