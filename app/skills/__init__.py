"""Skill system — user-customizable research templates.

Skills are JSON files in workspace/skills/ that define multi-step research
pipelines. The loader validates them against the Tool Registry; the registry
serves them to the Planner and the /api/skills endpoints.
"""

from app.skills.models import SkillDefinition, SkillMeta, SkillParameter, SkillStep
from app.skills.loader import load_all_skills, load_skill_from_file, validate_skill
from app.skills.registry import (
    get_skill,
    init_skill_registry,
    list_skills,
    reload_skills,
)

__all__ = [
    "SkillDefinition",
    "SkillMeta",
    "SkillParameter",
    "SkillStep",
    "get_skill",
    "init_skill_registry",
    "list_skills",
    "load_all_skills",
    "load_skill_from_file",
    "reload_skills",
    "validate_skill",
]
