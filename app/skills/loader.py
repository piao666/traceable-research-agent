"""Load and validate Skill JSON files from the workspace/skills directory."""

from __future__ import annotations

import json
from pathlib import Path

from app.skills.models import SkillDefinition, SkillStep


def load_skill_from_file(path: Path) -> SkillDefinition | None:
    """Load a single Skill JSON file. Returns None if the file is unreadable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(raw, dict):
        return None

    # Normalize steps
    steps: list[SkillStep] = []
    for step_data in raw.get("steps") or []:
        if not isinstance(step_data, dict):
            continue
        steps.append(SkillStep(
            tool_name=str(step_data.get("tool_name") or ""),
            goal=str(step_data.get("goal") or ""),
            arguments=step_data.get("arguments") if isinstance(step_data.get("arguments"), dict) else {},
            arguments_from=step_data.get("arguments_from") if isinstance(step_data.get("arguments_from"), dict) else None,
        ))

    # Normalize parameters
    parameters: dict[str, Any] = {}
    for key, param_data in (raw.get("parameters") or {}).items():
        if not isinstance(param_data, dict):
            continue
        parameters[str(key)] = {
            "type": str(param_data.get("type") or "string"),
            "required": bool(param_data.get("required", False)),
            "default": param_data.get("default"),
        }

    return SkillDefinition(
        name=str(raw.get("name") or ""),
        version=str(raw.get("version") or "1.0"),
        description=str(raw.get("description") or ""),
        required_tools=[str(t) for t in (raw.get("required_tools") or [])],
        parameters=parameters,
        steps=steps,
    )


def validate_skill(skill: SkillDefinition, available_tools: set[str]) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: list[str] = []

    if not skill.name:
        errors.append("Skill name is empty")
    if not skill.steps:
        errors.append("Skill has no steps")

    for i, step in enumerate(skill.steps):
        if not step.tool_name:
            errors.append(f"Step {i + 1}: tool_name is empty")
        elif step.tool_name not in available_tools:
            errors.append(
                f"Step {i + 1}: tool '{step.tool_name}' is not registered in the Tool Registry"
            )

    for tool_name in skill.required_tools:
        if tool_name not in available_tools:
            errors.append(
                f"Required tool '{tool_name}' is not registered in the Tool Registry"
            )

    return errors


def load_all_skills(skills_dir: Path) -> dict[str, SkillDefinition]:
    """Scan workspace/skills/*.json and return validated Skill definitions."""
    if not skills_dir.is_dir():
        return {}

    result: dict[str, SkillDefinition] = {}
    for path in sorted(skills_dir.glob("*.json")):
        skill = load_skill_from_file(path)
        if skill and skill.name:
            result[skill.name] = skill
    return result
