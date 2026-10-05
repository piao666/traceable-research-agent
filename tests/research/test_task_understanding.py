from copy import deepcopy

import pytest

from app.research.task_understanding import TaskUnderstandingError, merge_task_understanding


def _base():
    return {
        "original_task": "Compare two approaches",
        "as_of": "2026-10-04",
        "source_constraints": {"mode": "restrict", "domains": ["example.org"], "official_only": True},
        "output_constraints": {"language": "en", "sentence_count": 2},
        "questions": [{"question_id": "q-1", "text": "What is the task?", "requirement_ids": ["r-1"]}],
        "requirements": [{"requirement_id": "r-1", "question_id": "q-1", "predicate": "existing", "required": True}],
        "evidence_scope_requirements": [],
    }


def test_merge_adds_generic_questions_requirements_and_scope_without_mutating_constraints():
    base = _base()
    before = deepcopy(base)
    merged = merge_task_understanding(base, {
        "questions": [{"question_id": "q-2", "text": "What evidence addresses the second dimension?", "requirement_ids": ["r-2"]}],
        "requirements": [{"requirement_id": "r-2", "question_id": "q-2", "kind": "comparison", "dimension": "cost", "required": True, "min_independent_sources": 2}],
        "evidence_scope_requirements": [{"requirement_id": "r-3", "question_id": "q-2", "source_scope": "external_web", "match_mode": "all_components"}],
    })
    assert base == before
    assert merged["source_constraints"] == before["source_constraints"]
    assert merged["output_constraints"] == before["output_constraints"]
    assert merged["original_task"] == before["original_task"]
    assert [item["requirement_id"] for item in merged["requirements"]] == ["r-1", "r-2"]
    assert merged["evidence_scope_requirements"][0]["requirement_id"] == "r-3"


@pytest.mark.parametrize("proposal", [
    {"questions": [{"question_id": "", "text": "question"}]},
    {"questions": [{"question_id": "q-1", "text": "duplicate"}]},
    {"requirements": [{"requirement_id": "r-1", "question_id": "q-1"}]},
    {"requirements": [{"requirement_id": "r-2", "question_id": "unknown"}]},
    {"questions": [{"question_id": "q-2", "text": "dangling", "requirement_ids": ["r-unknown"]}]},
    {"questions": [{"question_id": "q-2", "text": "question without a body obligation"}]},
    {"evidence_scope_requirements": [{"requirement_id": "r-2", "source_scope": "external_web"}, {"requirement_id": "r-2", "source_scope": "local_project"}]},
    {"evidence_scope_requirements": [{"requirement_id": "r-2", "unknown": "field"}]},
    {"requirements": [{"requirement_id": "r-2", "question_id": "q-1", "required": False}]},
    {"requirements": [{"requirement_id": "r-2", "question_id": "q-1", "min_independent_sources": 0}]},
    {"requirements": [{"requirement_id": "r-2", "question_id": "q-1", "acceptable_content_basis": ["snippet_only"]}]},
    {"evidence_scope_requirements": [{"requirement_id": "r-2", "source_scope": "anything"}],
     "requirements": [{"requirement_id": "r-3", "question_id": "q-1"}]},
    {"evidence_scope_requirements": [{"requirement_id": "r-2", "match_mode": "invented"}],
     "requirements": [{"requirement_id": "r-3", "question_id": "q-1"}]},
    {"evidence_scope_requirements": [{"requirement_id": "r-2", "entity": 7}],
     "requirements": [{"requirement_id": "r-3", "question_id": "q-1"}]},
])
def test_rejects_malformed_duplicate_or_unknown_ids_atomically(proposal):
    with pytest.raises(TaskUnderstandingError):
        merge_task_understanding(_base(), proposal)


@pytest.mark.parametrize("proposal", [
    {"source_constraints": {"mode": "unspecified"}},
    {"as_of": "1900-01-01"},
    {"original_task": "different"},
    {"output_constraints": {"language": "zh"}},
])
def test_model_cannot_propose_server_owned_constraint_changes(proposal):
    with pytest.raises(TaskUnderstandingError):
        merge_task_understanding(_base(), proposal)


def test_rejects_invalid_evidence_threshold_instead_of_lowering_gate():
    with pytest.raises(TaskUnderstandingError):
        merge_task_understanding(_base(), {
            "evidence_scope_requirements": [{"requirement_id": "r-2", "min_independent_sources": -1}]
        })
