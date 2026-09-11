"""Resolve report synthesis dependencies from explicit runtime policy."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMClient, LLMResponse
from app.llm.cost import estimate_cost
from app.llm.errors import LLM_ERROR_TYPES
from app.llm.providers import create_llm_client
from app.trace.logger import record_trace_event


def resolve_report_llm_client(
    settings: Settings,
    injected_client: LLMClient | None = None,
) -> LLMClient | None:
    """Return an LLM client only when report synthesis is explicitly enabled."""

    if settings.report_generation_mode != "llm":
        return None
    from app.agent.budget import budget_client
    return budget_client(
        injected_client
        or create_llm_client(
            settings,
            settings.llm_provider,
            settings.llm_model,
        )
    )


def record_report_synthesis_trace(
    db,
    run_id: str,
    traces: list,
    response: LLMResponse,
    *,
    success: bool,
):
    """Persist one non-secret Synthesizer outcome for either executor path."""

    usage = response.usage
    error_type = str(response.metadata.get("error_type") or "").strip()
    if not success and error_type not in LLM_ERROR_TYPES:
        error_type = "malformed_response" if response.success else "provider_unavailable"
    provider = str(response.provider or "unknown")[:80]
    model = str(response.model or "")[:160] or None
    message = None if success else f"Report synthesis failed ({error_type})."
    metadata = {
        "provider": provider,
        "model": model,
        "error_type": error_type or None,
        "retryable": bool(response.metadata.get("retryable", False)),
    }
    return record_trace_event(
        db=db,
        run_id=run_id,
        step_no=max((trace.step_no for trace in traces), default=0) + 1,
        tool_name="report_synthesis",
        status="success" if success else "failed",
        input_data={"provider": provider, "model": model},
        output_summary=(
            "LLM report synthesis completed."
            if success
            else "LLM report synthesis did not produce an admissible report."
        ),
        output_data={"metadata": metadata},
        error_message=message,
        token_in=usage.prompt_tokens if usage else 0,
        token_out=usage.completion_tokens if usage else 0,
        estimated_cost=estimate_cost(provider, model, usage),
    )
