"""LLM cost estimation from token usage and provider pricing.

Pricing is approximate (RMB per 1M tokens) and should be updated periodically.
All costs are estimates — actual billing may differ.
"""

from __future__ import annotations

from app.llm.base import LLMUsage

# Approximate pricing in RMB per 1M tokens (as of 2025-2026)
PRICING: dict[str, dict[str, float]] = {
    "deepseek": {
        "deepseek-chat": {"prompt": 1.0, "completion": 2.0},
        "deepseek-reasoner": {"prompt": 4.0, "completion": 16.0},
    },
    "qwen": {
        "qwen-plus": {"prompt": 2.0, "completion": 6.0},
        "qwen-turbo": {"prompt": 0.3, "completion": 0.6},
        "qwen-max": {"prompt": 20.0, "completion": 60.0},
        "qwen-plus-latest": {"prompt": 2.0, "completion": 6.0},
    },
}


def estimate_cost(
    provider: str,
    model: str | None,
    usage: LLMUsage | None,
) -> float:
    """Estimate cost in RMB from token usage.

    Returns 0.0 if usage is None or pricing is unavailable.
    """
    if usage is None or usage.total_tokens == 0:
        return 0.0

    provider_pricing = PRICING.get(provider.lower(), {})
    model_pricing = provider_pricing.get(
        (model or "").lower(),
        provider_pricing.get("default", {"prompt": 1.0, "completion": 2.0}),
    )

    prompt_cost = (usage.prompt_tokens / 1_000_000) * model_pricing.get("prompt", 1.0)
    completion_cost = (usage.completion_tokens / 1_000_000) * model_pricing.get("completion", 2.0)
    return round(prompt_cost + completion_cost, 6)


def estimate_cost_from_tokens(
    provider: str,
    model: str | None,
    token_in: int,
    token_out: int,
) -> float:
    """Estimate cost directly from token counts without a usage object."""
    if token_in == 0 and token_out == 0:
        return 0.0
    usage = LLMUsage(
        prompt_tokens=token_in,
        completion_tokens=token_out,
        total_tokens=token_in + token_out,
    )
    return estimate_cost(provider, model, usage)
