"""Pydantic schemas for the Day 1-3 mock API surface."""

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    phase: str


class TaskCreateRequest(BaseModel):
    task: str = Field(..., min_length=1)
    report_type: str = "summary"
    source_mode: str = "mock"


class TaskCreateResponse(BaseModel):
    run_id: str
    status: str
    status_url: str
    trace_url: str
    report_url: str


class TaskStatusResponse(BaseModel):
    run_id: str
    status: str
    current_step: int
    total_steps: int
    report_path: str | None = None
    error_message: str | None = None
    message: str


class ToolInfo(BaseModel):
    name: str
    description: str
    risk_level: str
    requires_confirmation: bool
    enabled: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolListResponse(BaseModel):
    tools: list[ToolInfo]


class ReportResponse(BaseModel):
    run_id: str
    markdown: str
    report_path: str | None = None
    message: str
