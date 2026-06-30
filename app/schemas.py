"""Pydantic schemas for the API surface."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    phase: str
    execution_mode: str = "planned"
    react_enabled: bool = True


class TaskCreateRequest(BaseModel):
    task: str = Field(..., min_length=1)
    report_type: str = "summary"
    source_mode: str = "real"
    allowed_tools: list[str] | None = None
    execution_mode_override: str | None = None  # "planned" | "react" | None (use server default)


class TaskCreateResponse(BaseModel):
    run_id: str
    status: str
    status_url: str
    trace_url: str
    report_url: str
    plan_url: str | None = None
    run_url: str | None = None


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
    execution_mode: str = "planned"
    requested_execution_mode: str | None = None
    planner_source: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


class PlanStepResponse(BaseModel):
    step_no: int
    goal: str
    tool_name: str
    arguments: dict[str, Any]
    expected_output: str
    completion_criteria: str
    risk_level: str
    requires_confirmation: bool
    confirmation_reason: str | None = None
    confirmation_details: dict[str, Any] | None = None


class TaskPlanResponse(BaseModel):
    run_id: str
    version: str
    task: str
    source_mode: str
    allowed_tools: list[str]
    steps: list[PlanStepResponse]
    notes: list[str]
    confirmation: dict[str, Any] | None = None
    planner_source: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    execution_mode: str | None = None
    requested_execution_mode: str | None = None
    react_state: dict[str, Any] | None = None


class TaskRunResponse(BaseModel):
    run_id: str
    status: str
    current_step: int
    total_steps: int
    total_tool_calls: int
    report_url: str
    trace_url: str
    error_message: str | None = None
    message: str | None = None
    execution_mode: str = "planned"
    planner_source: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


class AsyncRunResponse(BaseModel):
    run_id: str
    status: str
    status_url: str
    trace_url: str
    report_url: str
    message: str
    execution_mode: str = "planned"


class TaskConfirmRequest(BaseModel):
    approved: bool
    comment: str | None = None
    resume: bool = True


class TaskConfirmResponse(BaseModel):
    run_id: str
    status: str
    approved: bool
    comment: str | None = None
    resumed: bool
    message: str
    run_result: TaskRunResponse | None = None


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
    output: Any | None = None
    metadata: dict[str, Any] | None = None


class EvidenceItemResponse(BaseModel):
    evidence_id: str
    run_id: str
    trace_id: str | None = None
    step_no: int | None = None
    tool_name: str
    source_type: str
    source_ref: str | None = None
    title: str
    snippet: str
    status: str
    confidence: str
    metadata: dict[str, Any]
    is_mock: bool = False
    is_fallback: bool = False
    unsupported_reason: str | None = None


class EvidenceGroupResponse(BaseModel):
    source_type: str
    evidence_ids: list[str]
    count: int
    mock_count: int = 0
    fallback_count: int = 0
    unsupported_count: int = 0


class ClaimEvidenceMapResponse(BaseModel):
    claim_id: str
    claim: str
    evidence_ids: list[str]
    support_level: str
    notes: str | None = None


class EvidenceBundleResponse(BaseModel):
    run_id: str
    task: str
    total_evidence_items: int
    source_groups: list[EvidenceGroupResponse]
    claims: list[ClaimEvidenceMapResponse]
    evidence_items: list[EvidenceItemResponse]
    unsupported_claims: list[ClaimEvidenceMapResponse]
    warnings: list[str]


class EvidenceExportResponse(BaseModel):
    run_id: str
    format: str
    export_path: str
    item_count: int
    created_at: str


class EvidenceExportContentResponse(BaseModel):
    run_id: str
    format: str
    export_path: str
    content: str
    content_type: str
    item_count: int
    created_at: str


class ToolInfo(BaseModel):
    name: str
    description: str
    risk_level: str
    requires_confirmation: bool
    enabled: bool
    timeout_seconds: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tags: list[str]


class ToolListResponse(BaseModel):
    tools: list[ToolInfo]


class ToolExecuteRequest(BaseModel):
    arguments: dict[str, Any] | None = None
    run_id: str | None = None
    step_no: int = 1


class ToolExecuteResponse(BaseModel):
    success: bool
    output: Any | None = None
    output_summary: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any]


class ReportResponse(BaseModel):
    run_id: str
    markdown: str
    report_path: str | None = None
    exists: bool = False
    message: str | None = None
