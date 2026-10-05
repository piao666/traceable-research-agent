"""Quick final-report evidence gate must use the shared A2 eligibility rule."""

from app.agent.executor import _quick_evidence_gate_failure


def _plan() -> dict:
    return {
        "research_mode": "quick",
        "quick_output_mode": "limited_research",
        "task_contract": {
            "original_task": "Explain Python asyncio event loops.",
            "evidence_requirement": "substantive",
            "required_content_basis": ["full_text", "partial", "table", "structured"],
        },
    }


def _bundle(*, basis: str, text: str, role: str = "primary_content") -> dict:
    return {
        "source_documents": [{
            "document_id": "d1", "title": "Python asyncio event loops",
            "canonical_uri": "https://docs.python.org/asyncio",
            "metadata": {"evidence_role": role, "source_class": "official"},
        }],
        "source_snapshots": [{
            "snapshot_id": "s1", "document_id": "d1",
            "metadata": {"evidence_role": role},
        }],
        "passages": [{
            "passage_id": "p1", "snapshot_id": "s1", "content_basis": basis,
            "text": text, "metadata": {"evidence_role": role},
        }],
    }


def test_quick_gate_accepts_task_eligible_partial_fetched_body():
    failure = _quick_evidence_gate_failure(
        _plan(),
        _bundle(basis="partial", text="Python asyncio event loops schedule asynchronous tasks."),
    )

    assert failure is None


def test_quick_gate_rejects_discovery_snippet_without_valid_body():
    failure = _quick_evidence_gate_failure(
        _plan(),
        _bundle(basis="snippet_only", text="Python asyncio event loops overview.", role="discovery_index"),
    )

    assert failure is not None
    assert failure.gaps[0].code == "discovery_not_full_text"
