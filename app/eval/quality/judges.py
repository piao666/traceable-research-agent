"""LLM-as-judge scoring for research quality evaluation.

Scores two dimensions via LLM:
  - Relevance: Does the report directly answer the research question?
  - Coverage: Does the report cover all important dimensions?

Uses deterministic fallback when LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings
from app.llm.base import LLMClient, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """You are a research quality evaluator. Score the given research report
against the original research question on two dimensions:

1. **Relevance (0-10)**: Does the report directly answer the question? Does it provide
   substantive insights rather than generic statements?

2. **Coverage (0-10)**: Does the report cover all important dimensions of the question?
   Are there significant gaps or missing perspectives?

Output ONLY valid JSON with this schema:
{"relevance_score": <int 0-10>, "relevance_rationale": "<one sentence>",
 "coverage_score": <int 0-10>, "covered_dimensions": ["<dim>", ...],
 "missing_dimensions": ["<dim>", ...]}"""


_DETERMINISTIC_FALLBACK = {
    "relevance_score": 6,
    "relevance_rationale": "Deterministic fallback: LLM judge unavailable.",
    "coverage_score": 6,
    "covered_dimensions": ["core topic"],
    "missing_dimensions": ["unable to assess without LLM"],
}


def judge_report(
    question: str,
    report_text: str,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Score a report with LLM-as-judge, falling back to deterministic defaults."""

    if llm_client is None or not llm_client.is_available():
        return dict(_DETERMINISTIC_FALLBACK)

    # Truncate report to avoid excessive token usage
    truncated = report_text[:6000] if len(report_text) > 6000 else report_text

    messages = [
        LLMMessage(role="system", content=JUDGE_SYSTEM_PROMPT),
        LLMMessage(
            role="user",
            content=f"Research question: {question}\n\nReport:\n{truncated}",
        ),
    ]

    try:
        response: LLMResponse = llm_client.complete(messages, temperature=0.0, max_tokens=500)
        if not response.success or not response.content:
            logger.warning("LLM judge returned empty response, using fallback")
            return dict(_DETERMINISTIC_FALLBACK)

        # Extract JSON from response
        content = response.content.strip()
        # Handle markdown code fences
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:]) if len(lines) > 1 else content
            if content.endswith("```"):
                content = content[:-3]
        result = json.loads(content)
        result.setdefault("relevance_score", 6)
        result.setdefault("relevance_rationale", "")
        result.setdefault("coverage_score", 6)
        result.setdefault("covered_dimensions", [])
        result.setdefault("missing_dimensions", [])
        return result
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("LLM judge failed: %s, using fallback", exc)
        return dict(_DETERMINISTIC_FALLBACK)