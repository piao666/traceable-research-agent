"""Pydantic schemas for the API surface."""

from datetime import datetime
from typing import Any, Literal

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
    scenario_template: str | None = None
    scenario_template_key: str | None = None
    session_id: str | None = None
    skill_name: str | None = None
    require_plan_approval: bool = False  # Phase 7.4: pause for plan review before execution
    retrieval_profile: str | None = None  # Phase 8.1: source tier retrieval profile


class RuntimeCapabilitiesResponse(BaseModel):
    offline_mode: bool
    tavily_configured: bool
    llm_provider: str
    llm_configured: bool
    react_provider: str
    react_configured: bool
    react_enabled: bool
    deep_research_enabled: bool
    report_generation_mode: str
    connectivity_verified: bool = False


class RuntimeCheck(BaseModel):
    name: str
    status: Literal["ok", "error"]
    message: str


class RuntimeDiagnosticsResponse(BaseModel):
    checked_at: datetime
    checks: list[RuntimeCheck]
    capabilities: RuntimeCapabilitiesResponse
    execution_mode: str
    memory_llm_extraction_enabled: bool
    mcp_enabled: bool
    mcp_configured: bool


class PreflightIssue(BaseModel):
    code: str
    capability: str
    environment_variable: str
    message: str


class TaskPreflightResponse(BaseModel):
    ready: bool
    blockers: list[PreflightIssue]
    warnings: list[str]
    capabilities: RuntimeCapabilitiesResponse


class TaskCreateResponse(BaseModel):
    run_id: str
    status: str
    status_url: str
    trace_url: str
    report_url: str
    plan_url: str | None = None
    run_url: str | None = None


class ResearchIntegrityResponse(BaseModel):
    research_outcome: dict[str, Any] | None = None
    requires_review: bool = False
    citation_evaluated: bool = False
    quality_warnings: list[str] = Field(default_factory=list)


class TaskListItem(ResearchIntegrityResponse):
    """Lightweight item for task list endpoint."""
    run_id: str
    task: str
    status: str
    report_type: str
    execution_mode: str = "planned"
    total_tool_calls: int = 0
    estimated_cost: float = 0.0
    session_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    """Paginated task list."""
    tasks: list[TaskListItem]
    total: int
    limit: int
    offset: int


class TaskCancelRequest(BaseModel):
    reason: str = ""


class TaskRetryRequest(BaseModel):
    reuse_plan: bool = True
    from_failed_step: bool = False


class SessionUpdateRequest(BaseModel):
    title: str | None = None


class TaskStatusResponse(ResearchIntegrityResponse):
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
    citation_total: int = 0
    citation_supported: int = 0
    citation_weakly_supported: int = 0
    citation_unsupported: int = 0
    citation_accuracy: float = 0.0
    created_at: datetime
    updated_at: datetime
    execution_mode: str = "planned"
    requested_execution_mode: str | None = None
    planner_source: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    skill_routing: dict[str, Any] | None = None
    adaptive_gate_pending: bool = False
    adaptive_upgrade: bool = False
    adaptive_upgrade_reason: str | None = None
    adaptive_upgrade_failed: bool = False
    adaptive_phase: str | None = None
    deepening_pending: bool = False
    deepening_phase: str | None = None


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


class ExecutionBudgetLimits(BaseModel):
    max_tool_calls: int
    max_llm_calls: int
    max_tokens: int
    max_seconds: int
    max_estimated_cost: float
    tool_cost_estimate: float | None = None
    llm_cost_per_million_tokens: float | None = None


class ExecutionBudgetResponse(BaseModel):
    version: str
    root_run_id: str
    limits: ExecutionBudgetLimits
    tool_calls: int
    llm_calls: int
    accounted_tokens: int
    estimated_cost: float
    cost_currency: str
    cost_evaluable: bool
    deadline: float
    stop_reason: str | None = None


class RecoveryToolResponse(BaseModel):
    name: str
    status: str
    reason: str | None = None
    attempts: int | None = None
    remaining_attempts: int | None = None
    blocked_input_count: int = 0
    retry_at: float | None = None


class SourceCandidateResponse(BaseModel):
    source_id: str
    url: str
    title: str
    snippet: str
    fetch_status: str
    content_basis: str
    trace_ids: list[str]
    run_ids: list[str]
    tools: list[str]
    fetch_attempts: int


class SourceGapsResponse(BaseModel):
    pending_fetch: int
    failed_fetch: int
    fetched: int
    full_text_missing: int
    no_sources: bool


class SourceContextResponse(BaseModel):
    version: str
    sources: list[SourceCandidateResponse]
    omitted_count: int
    gaps: SourceGapsResponse
    untrusted_content: bool


class ExecutionInsightsResponse(BaseModel):
    version: str
    sampled_at: float
    source_mode: str
    allowed_tools: list[str]
    recovery_recorded: bool
    tools: list[RecoveryToolResponse]
    source_context: SourceContextResponse


class TaskPlanResponse(BaseModel):
    run_id: str
    execution_budget: ExecutionBudgetResponse | None = None
    execution_insights: ExecutionInsightsResponse | None = None
    evidence_mapping_version: str | None = None
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
    skill_routing: dict[str, Any] | None = None
    adaptive_gate_pending: bool = False
    adaptive_upgrade: bool = False
    adaptive_upgrade_reason: str | None = None
    adaptive_upgrade_failed: bool = False
    adaptive_upgrade_error: str | None = None
    adaptive_phase: str | None = None
    deepening_pending: bool = False
    deepening_phase: str | None = None
    deepening_total_rounds: int = 0
    deepening_learnings: list[str] = Field(default_factory=list)
    deepening_sub_run_ids: list[str] = Field(default_factory=list)


class TaskRunResponse(ResearchIntegrityResponse):
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
    adaptive_upgrade: bool = False
    adaptive_upgrade_reason: str | None = None
    adaptive_upgrade_failed: bool = False
    adaptive_phase: str | None = None
    deepening_pending: bool = False
    deepening_phase: str | None = None


class AsyncRunResponse(ResearchIntegrityResponse):
    run_id: str
    status: str
    status_url: str
    trace_url: str
    report_url: str
    message: str
    execution_mode: str = "planned"
    adaptive_gate_pending: bool = False
    adaptive_upgrade: bool = False
    adaptive_phase: str | None = None


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


# ── Phase 7.4: Plan approval schemas ──────────────────────────────────

class PlanReviewStep(BaseModel):
    step_no: int
    tool_name: str
    goal: str
    arguments: dict[str, Any]
    risk_level: str
    requires_confirmation: bool
    estimated_tokens: int = 500
    raw_step: dict[str, Any] = Field(default_factory=dict)


class PlanReviewResponse(BaseModel):
    run_id: str
    task: str
    status: str
    execution_mode: str
    steps: list[PlanReviewStep]
    allowed_tools: list[str]
    source_mode: str | None = None
    estimated_total_tokens: int = 0
    estimated_cost: float = 0.0
    risk_summary: dict[str, int] = Field(default_factory=lambda: {"low": 0, "medium": 0, "high": 0})
    notes: list[str] = Field(default_factory=list)
    planner_source: str | None = None
    skill_routing: dict[str, Any] | None = None
    preflight: TaskPreflightResponse | None = None


class PlanApproveRequest(BaseModel):
    approved: bool
    comment: str | None = None
    modified_steps: list[dict[str, Any]] | None = None


class ToolTraceResponse(BaseModel):
    trace_id: str
    run_id: str
    step_no: int
    tool_name: str
    input_summary: str | None = None
    output_summary: str | None = None
    status: str
    latency_ms: int | None = None
    token_in: int = 0
    token_out: int = 0
    estimated_cost: float = 0.0
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    output: Any | None = None
    metadata: dict[str, Any] | None = None
    sub_query: str | None = None


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


class ProvenanceBundleResponse(BaseModel):
    run_id: str
    schema_version: str
    extractor_version: str
    status: str
    source_documents: list[dict[str, Any]]
    source_snapshots: list[dict[str, Any]]
    passages: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    report_claims: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    integrity: dict[str, Any]
    reasoning: dict[str, Any] | None = None
    reliability_scores: list[dict[str, Any]] = Field(default_factory=list)
    resolutions: list[dict[str, Any]] = Field(default_factory=list)


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
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class ReportResponse(ResearchIntegrityResponse):
    run_id: str
    markdown: str
    report_path: str | None = None
    exists: bool = False
    availability: Literal["available", "not_generated", "missing", "blocked"] = "not_generated"
    message: str | None = None


# ── Session schemas ───────────────────────────────────────────────────

class SessionCreateRequest(BaseModel):
    title: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    title: str | None = None
    turn_count: int = 0
    created_at: datetime
    updated_at: datetime


class ChatTurnResponse(BaseModel):
    turn_id: str
    session_id: str
    role: str
    content: str
    run_id: str | None = None
    created_at: datetime


class SessionDetailResponse(BaseModel):
    session_id: str
    title: str | None = None
    turns: list[ChatTurnResponse]
    created_at: datetime
    updated_at: datetime


# ── Memory schemas ────────────────────────────────────────────────────

class UserMemoryResponse(BaseModel):
    memory_id: str
    kind: str
    extraction_method: str
    content: str
    confidence: float
    status: str
    source_session_id: str | None = None
    source_run_id: str | None = None
    valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    memories: list[UserMemoryResponse]
    total: int
    active_count: int = 0
    pending_count: int = 0


class MemoryConfirmRequest(BaseModel):
    approved: bool


class MemoryDeleteResponse(BaseModel):
    memory_id: str
    deleted: bool
    message: str


class MemoryClearResponse(BaseModel):
    deleted: bool
    count: int
    message: str


class MemoryAuditResponse(BaseModel):
    event_id: str
    action: str
    memory_id: str | None
    affected_count: int
    created_at: datetime


# ── Skill schemas ─────────────────────────────────────────────────────

class SkillSummary(BaseModel):
    name: str
    version: str
    description: str
    required_tools: list[str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: str
    error: str | None = None


class SkillListResponse(BaseModel):
    skills: list[SkillSummary]


class SkillDetailResponse(BaseModel):
    name: str
    version: str
    description: str
    required_tools: list[str]
    parameters: dict[str, Any]
    steps: list[dict[str, Any]]


class SkillReloadResponse(BaseModel):
    status: str
    count: int
