"""Pydantic models for the Skill system."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillDecompose(BaseModel):
    """Sub-question decomposition configuration for a Skill."""
    enabled: bool = False
    method: str = "llm"          # "llm" | "rule" | "auto"
    max_sub_queries: int = 4     # 2-6
    force: bool = False          # skip broad-task heuristic


class SkillParameter(BaseModel):
    type: str = "string"
    required: bool = False
    default: Any = None


class SkillStep(BaseModel):
    tool_name: str
    goal: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_from: dict[str, Any] | None = None


class SkillDefinition(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    required_tools: list[str] = Field(default_factory=list)
    parameters: dict[str, SkillParameter] = Field(default_factory=dict)
    steps: list[SkillStep] = Field(default_factory=list)
    changelog: list[dict[str, Any]] | None = None
    decompose: SkillDecompose | None = None


class SkillMeta(BaseModel):
    """Lightweight metadata for the list endpoint."""
    name: str
    version: str
    description: str
    required_tools: list[str]
    parameters: dict[str, Any]
    status: str = "valid"
    error: str | None = None
