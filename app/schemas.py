"""Pydantic schemas for the API surface."""

from datetime import datetime
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
    allowed_tools: list[str] | None = None


class TaskCreateResponse(BaseModel):
    run_id: str
    status: str
    status_url: str
    trace_url: str
    report_url: str


class TaskStatusResponse(BaseModel):
    run_id: str
    task: str
    report_type: str
    source_mode: str
    status: str
    current_step: int
    total_steps: int
    report_path: str | None = None
    error_message: str | None = None
    total_tool_calls: int
    total_latency_ms: int
    estimated_cost: float
    created_at: datetime
    updated_at: datetime


class ToolTraceResponse(BaseModel):
    trace_id: str
    run_id: str
    step_no: int
    tool_name: str
    input_summary: str | None = None
    output_summary: str | None = None
    status: str
    latency_ms: int | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


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
