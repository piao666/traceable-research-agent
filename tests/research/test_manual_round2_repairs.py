"""Contracts exposed by the second manual verification (offline fixtures)."""
import json
import re
from copy import deepcopy
from unittest.mock import patch
import pytest
from app.evidence.citation_validator import CitationValidationDetail, CitationValidationReport, validate_citations
from app.llm.base import LLMResponse
from app.reporting.claim_occurrence import normalize_limitation_citations, is_evidence_limitation_statement
from app.reporting.writing_evidence import build_writing_evidence
from app.research.answer_coverage import assess_answer_coverage, _verified_audit
from app.research.comparison_scope import comparison_spec, select_comparison_candidates, selection_query, relevant_selection_urls
from app.research.recovery import recover_answer_evidence
from app.tools.base import ToolResult
from app.retrieval.contracts import FetchBackend, FetchRequest, FetchFailureCode
from app.retrieval.router import RetrievalRouter
from .test_manual_run_repairs import Understanding, candidate_bundle
from .conftest import create_root
from tests.retrieval.test_adaptive_router import _success, _failure


def test_limitation_marker_normalization_preserves_text_and_facts():
    raw = '本次证据未提供 AgentAlpha 的典型应用场景 [CIT-001-01]。AgentBeta 用于研究自动化 [CIT-002-01]。'
    normalized = normalize_limitation_citations(raw)
    assert normalized == raw.replace('[CIT-001-01]', '')
    assert normalize_limitation_citations(normalized) == normalized
    factual = '本次证据未提供框架说明，但 AgentAlpha 提速 20 倍 [CIT-001-01]。'
    assert normalize_limitation_citations(factual) == factual
    assert not is_evidence_limitation_statement(factual)


@pytest.mark.parametrize('beta', ['missing', 'inventory_borrow', 'complete'])
def test_each_listed_framework_needs_its_own_supported_application(tmp_path, monkeypatch, beta):
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    answer = '框架包括 AgentAlpha、AgentBeta [CIT-001-01]。AgentAlpha 用于研究自动化 [CIT-001-01]。'
    if beta == 'complete':
        answer += 'AgentBeta 用于工具调用 [CIT-001-01]。'
    else:
        answer += '本次证据未提供 AgentBeta 的典型应用场景 [CIT-001-01]。'
    markers = [m.start() for m in re.finditer(r'\[CIT-', answer)]
    contract = {'original_task': '给我常用的框架，给出这些的典型应用场景',
                'requirements': [{'requirement_id': 'r', 'question_id': 'q', 'predicate': '给出这些的典型应用场景'}]}
    row = {'requirement_id': 'r', 'complete': True, 'marker_starts': markers,
           'comparison_cells': [{'entity': 'AgentAlpha', 'facet': 'application', 'complete': True, 'marker_starts': [markers[1]],
               'application_kind': 'concrete_task', 'application_workload_quote': 'AgentAlpha 用于研究自动化'}]}
    if beta != 'missing':
        row['comparison_cells'].append({'entity': 'AgentBeta', 'facet': 'application', 'complete': True,
            'application_kind': 'concrete_task', 'application_workload_quote': 'AgentBeta 用于工具调用',
            'marker_starts': [markers[2]] if beta == 'complete' else markers[:2]})
    validation = CitationValidationReport(details=[CitationValidationDetail('CIT-001-01', 'supported', '', '', 1, marker_start=m) for m in markers])
    result = assess_answer_coverage(answer, contract, validation, Understanding({'requirements': [row]}), provenance=candidate_bundle('Frameworks with applications.'))
    assert result['complete'] is (beta == 'complete')
    assert _verified_audit(result, contract) is (beta == 'complete')
    if beta != 'complete':
        assert any(g.get('entity') == 'AgentBeta' for g in result['gaps'])


def test_unmapped_inventory_cannot_be_silently_completed(tmp_path, monkeypatch):
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    contract = {'original_task': 'List frameworks and their applications', 'requirements': [{'requirement_id': 'r', 'question_id': 'q', 'predicate': 'their applications'}]}
    result = assess_answer_coverage('AgentAlpha 用于研究 [CIT-001-01]。', contract,
        CitationValidationReport(details=[CitationValidationDetail('CIT-001-01', 'supported', '', '', 1, marker_start=17)]), Understanding({'requirements': []}))
    assert not result['complete'] and result['gaps'][0]['cause'] == 'coverage_mapping_missing'


def multi_window_bundle(texts, different_date_source=False):
    result = {key: [] for key in candidate_bundle('placeholder')}
    for i, text in enumerate(texts):
        item = candidate_bundle(text)
        for key, rows in item.items():
            for row in rows:
                row = deepcopy(row)
                for field in ['passage_id', 'snapshot_id', 'document_id', 'trace_id']:
                    if field in row:
                        row[field] += str(i)
                if 'citation_label' in row:
                    row['citation_label'] = f'CIT-{i + 1:03}-01'
                if key == 'passages' and i == len(texts) - 1 and not different_date_source:
                    row['snapshot_id'] = 's2'
                if different_date_source and i == len(texts) - 1 and 'canonical_uri' in row:
                    row['canonical_uri'] = 'https://other.test/unrelated-date'
                result[key].append(row)
    return result


@pytest.mark.parametrize('wrong', [None, 'date_source', 'other_products', 'old'])
def test_cohort_category_criterion_and_date_use_separate_provenance_windows(tmp_path, monkeypatch, wrong):
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    categories = ['AgentAlpha is a personal assistant that executes tasks.', 'AgentBeta is an autonomous agent that executes tasks.']
    criterion = 'AgentAlpha and AgentBeta are compared in this hands-on product review.'
    if wrong == 'other_products':
        criterion = 'AgentGamma and AgentDelta are compared in this hands-on product review.'
    dated = 'Published on October 1, ' + ('2020' if wrong == 'old' else '2026') + '.'
    payload = {'candidates': [{'name': name, 'citation': f'CIT-{i + 1:03}-01', 'evidence_quote': categories[i],
        'selection_citation': 'CIT-003-01', 'selection_quote': criterion, 'date_citation': 'CIT-004-01', 'date_quote': dated}
        for i, name in enumerate(['AgentAlpha', 'AgentBeta'])], 'criteria': 'Dated comparative product review', 'time_scope': '2026'}
    task = '比较最近几款个人智能体的记忆'
    contract = {'original_task': task, 'as_of': '2026-10-07', 'comparison_scope': comparison_spec(task)}
    result = select_comparison_candidates(contract, multi_window_bundle(categories + [criterion, dated], wrong == 'date_source'), Understanding(payload))
    assert result['comparison_scope']['entities'] == (['AgentAlpha', 'AgentBeta'] if wrong is None else [])
    if wrong is None:
        candidate = result['comparison_scope']['selection']['candidates'][0]
        assert candidate['selection_identity']['citation'] == 'CIT-003-01'
        assert candidate['date_identity']['citation'] == 'CIT-004-01'
    assert contract['comparison_scope']['entities'] == []


class PluralJudge:
    def __init__(self, quotes, bad_identity=False, legacy=False):
        self.quotes, self.bad_identity, self.legacy = quotes, bad_identity, legacy
    def is_available(self):
        return True
    def structured_complete(self, messages, **kwargs):
        case = json.loads(messages[-1].content)['cases'][0]
        identity = dict(case['identity'])
        if self.bad_identity:
            identity['passage_id'] = 'unrelated'
        row = {**identity, 'verdict': 'supported', 'evidence_quote' if self.legacy else 'evidence_quotes': self.quotes}
        return LLMResponse(success=True, provider='fixture', content=json.dumps({'verdicts': [row]}))


@pytest.mark.parametrize('wrong', [None, 'fabricated', 'fragment', 'identity', 'ellipsis'])
def test_disjoint_exact_quotes_have_audited_plural_contract(tmp_path, monkeypatch, wrong):
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    quotes = ['AgentAlpha stores persistent memory in files.', 'AgentAlpha uses a graph to orchestrate tool calls.']
    bundle = candidate_bundle(quotes[0] + '\nAn unrelated paragraph describes installation.\n' + quotes[1])
    returned = list(quotes)
    if wrong == 'fabricated':
        returned[1] = 'AgentAlpha executes invented functionality.'
    if wrong == 'fragment':
        returned[1] = 'a graph'
    if wrong == 'ellipsis':
        returned = ' ... '.join(quotes)
    result = validate_citations('AgentAlpha 将记忆持久化到文件并用图编排工具调用 [CIT-001-01]。', bundle,
        writing_evidence=build_writing_evidence(bundle), task_contract={'output_constraints': {'language': 'zh'}},
        multilingual_llm_client=PluralJudge(returned, wrong == 'identity', wrong == 'ellipsis'))
    assert result.supported == (1 if wrong is None else 0)
    if wrong is None:
        assert result.details[0].evidence_quotes == quotes
        assert result.to_dict()['details'][0]['evidence_quotes'] == quotes


def test_query_variation_and_discovery_relevance():
    task = '比较最近很火的几款国内外Personal Agent，拆分对比他们的记忆'
    contract = {'original_task': task, 'as_of': '2026-10-07', 'comparison_scope': comparison_spec(task)}
    queries = [selection_query(contract, i) for i in range(4)]
    assert len(set(queries)) == 4 and all(len(q) < 160 and '2026' in q for q in queries)
    assert '国内' in queries[1]
    urls = ['https://example.test/agents', 'https://example.test/consumer', 'https://unknown.test/article']
    output = {'results': [{'url': urls[0], 'title': 'Personal AI assistants compared'}, {'url': urls[1], 'title': 'Popular consumer brands and dropshipping trends'}]}
    assert relevant_selection_urls(output, urls, contract) == [urls[0], urls[2]]


def test_global_selection_gap_does_not_rotate_technical_requirement_budget(db, r12_settings, tmp_path, monkeypatch):
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    root = create_root(db)
    task = '比较最近几款国内外Personal Agent的记忆和框架'
    plan = {'task_contract': {'obligation_version': 'v2', 'original_task': task, 'as_of': '2026-10-07',
        'comparison_scope': comparison_spec(task), 'requirements': [{'requirement_id': 's', 'question_id': 'q', 'predicate': '选择比较对象'}],
        'requirement_focus': {'s': {'facet': 'selection'}}}, 'allowed_tools': ['tavily_search', 'web_fetcher']}
    called = []
    def execute(name, args, *_):
        called.append((name, args))
        return ToolResult(success=True, output={'fetch_candidates': ['https://example.test/consumer'],
            'results': [{'url': 'https://example.test/consumer', 'title': 'Consumer products and dropshipping'}]})
    with patch('app.research.recovery.execute_governed_operation', side_effect=execute), patch('app.research.recovery.materialize_execution_provenance'):
        for rid, facet in [('r1', 'memory'), ('r2', 'framework'), ('r3', 'mechanism')]:
            recover_answer_evidence(db, root.run_id, plan, r12_settings, {'answer_gaps': [{'requirement_id': rid,
                'entity': rid, 'facet': facet, 'cause': 'comparison_selection_missing'}]})
    assert len(called) == 2 and all(name == 'tavily_search' for name, args in called)
    assert called[0][1]['query'] != called[1][1]['query']
    assert plan['answer_recovery']['stop_reason'] == 'same_gap_repair_round_limit'


@pytest.mark.parametrize('browser', [True, False])
@pytest.mark.parametrize('failure', [FetchFailureCode.JAVASCRIPT_REQUIRED, FetchFailureCode.TIMEOUT])
def test_slow_http_reserves_browser_time_with_shared_deadline(monkeypatch, browser, failure):
    elapsed = [0.0]
    monkeypatch.setattr('app.retrieval.router.time.monotonic', lambda: elapsed[0])
    calls = []
    class Http:
        def fetch(self, req):
            calls.append(('http', req.timeout_seconds))
            elapsed[0] += req.timeout_seconds
            return _failure(FetchBackend.HTTP, failure)
    class Browser:
        enabled = browser
        def fetch(self, req):
            calls.append(('browser', req.timeout_seconds))
            return _success(FetchBackend.BROWSER)
    result = RetrievalRouter(http_backend=Http(), browser_backend=Browser()).fetch(FetchRequest(url='https://example.com/a', timeout_seconds=10, allow_browser=browser))
    assert calls == ([('http', 5), ('browser', 5)] if browser else [('http', 10)])
    assert result.usable is browser

@pytest.mark.parametrize('qualified', [False, True])
def test_plural_quotes_preserve_source_conditions(tmp_path, monkeypatch, qualified):
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    quotes = ['If durability=NORMAL, AgentAlpha delays synchronization until checkpoint.',
              'AgentAlpha stores persistent memory in files.']
    bundle = candidate_bundle('\n'.join(quotes))
    qualifier = '当 durability=NORMAL 时，' if qualified else ''
    result = validate_citations(qualifier + 'AgentAlpha 将同步延迟到 checkpoint 并把记忆写入文件 [CIT-001-01]。', bundle,
        writing_evidence=build_writing_evidence(bundle), task_contract={'output_constraints': {'language': 'zh'}},
        multilingual_llm_client=PluralJudge(quotes))
    assert result.supported == (1 if qualified else 0)
    if not qualified:
        assert result.details[0].application_reason == 'missing_source_condition'


def test_real_report_pipeline_normalizes_limits_but_keeps_missing_application_open(tmp_path, monkeypatch):
    import gzip
    from types import SimpleNamespace
    from app.agent.reporter import generate_markdown_report
    monkeypatch.setenv('EVIDENCE_ARTIFACT_ROOT', str(tmp_path))
    task = '给我常用的框架，给出这些的典型应用场景'
    contract = {'obligation_version': 'v2', 'original_task': task,
        'requirements': [{'requirement_id': 'r', 'question_id': 'q', 'predicate': '给出这些的典型应用场景'}]}
    raw = '框架包括 AgentAlpha、AgentBeta [CIT-001-01]。AgentAlpha 用于自动研究 [CIT-001-01]。本次证据未提供 AgentBeta 的典型应用场景 [CIT-001-01]。'
    normalized = normalize_limitation_citations(raw)
    markers = [m.start() for m in re.finditer(r'\[CIT-', normalized)]
    validation = CitationValidationReport(details=[CitationValidationDetail('CIT-001-01', 'supported', '', '', 1, marker_start=m) for m in markers])
    row = {'requirement_id': 'r', 'complete': True, 'marker_starts': markers,
           'comparison_cells': [{'entity': 'AgentAlpha', 'facet': 'application', 'complete': True, 'marker_starts': [markers[1]]}]}
    run = SimpleNamespace(run_id='fixture', task=task, source_mode='real', status='running', report_type='summary',
        current_step=1, total_steps=1, total_tool_calls=1, created_at=None)
    plan = {'task_contract': contract, 'source_mode': 'real'}
    with patch('app.agent.reporter._llm_synthesize_answer', return_value=raw), patch('app.evidence.citation_validator.validate_citations', return_value=validation):
        report = generate_markdown_report(run, plan, [], [], llm_client=Understanding({'requirements': [row]}),
            provenance_bundle=candidate_bundle('AgentAlpha and AgentBeta are frameworks.'))
    assert normalized in report
    assert not plan['answer_coverage']['complete']
    assert plan['report_draft_result']['integrity'] == 'incomplete'
    audits = [json.loads(gzip.decompress(p.read_bytes())) for p in tmp_path.rglob('*.json.gz')]
    saved = next(a for a in audits if a['kind'] == 'answer_limitation_normalization')
    assert saved['inputs']['original_draft'] == raw
    assert saved['outputs']['normalized_draft'] == normalized
    assert saved['outputs']['grants_answer_coverage'] is False


def test_inventory_does_not_silently_discard_items_after_twenty_four():
    from app.research.referential_scope import enumerated_items
    names = [f'AgentItem{i}' for i in range(26)]
    task = {'original_task': '列出框架并说明这些的典型应用场景'}
    assert enumerated_items([{'text': '框架包括 ' + '、'.join(names) + '等。'}], task) == names


def test_discovery_keeps_category_alias_and_unknown_descriptors():
    contract = {'original_task': '比较近期 personal Agent'}
    rows = [{'url': 'https://example.test/a', 'title': 'AI companions comparison'},
            {'url': 'https://example.test/b', 'title': 'Introducing ProjectNova'},
            {'url': 'https://example.test/c', 'title': 'Popular consumer products'},
            {'url': 'https://example.test/d', 'title': 'AI brand rankings'}]
    urls = [r['url'] for r in rows]
    assert relevant_selection_urls({'results': rows}, urls, contract) == [urls[0], urls[1], urls[3]]
