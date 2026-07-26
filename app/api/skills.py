"""Skill Registry API — list, inspect, and reload user-customizable research templates."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.schemas import SkillListResponse, SkillDetailResponse, SkillReloadResponse
from app.security import require_api_key, require_request_context
from app.skills.registry import get_skill, list_skills, reload_skills

router = APIRouter(
    prefix="/skills",
    tags=["skills"],
    dependencies=[Depends(require_api_key), Depends(require_request_context)],
)

SKILLS_DIR = Path("workspace/skills")


@router.get("", response_model=SkillListResponse)
async def get_skills() -> SkillListResponse:
    """Return metadata for all installed Skills."""
    return SkillListResponse(skills=list_skills())


@router.get("/{name}", response_model=SkillDetailResponse)
async def get_skill_detail(name: str) -> SkillDetailResponse:
    """Return a complete Skill definition by name."""
    skill = get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return SkillDetailResponse(
        name=skill.name,
        version=skill.version,
        description=skill.description,
        required_tools=skill.required_tools,
        parameters=skill.parameters,
        steps=[s.model_dump() for s in skill.steps],
    )


@router.post("/reload", response_model=SkillReloadResponse)
async def reload_skills_endpoint() -> SkillReloadResponse:
    """Re-scan workspace/skills/ and rebuild the registry."""
    reload_skills(SKILLS_DIR)
    skills = list_skills()
    return SkillReloadResponse(
        status="ok",
        count=len(skills),
    )
