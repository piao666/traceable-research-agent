from app.agent.capability_requirements import admit_task_capabilities, task_required_capabilities
from app.config import Settings


def test_explicit_js_requires_browser_and_static_fetch_does_not_satisfy_it():
    settings = Settings(fetch_browser_enabled=False, fetch_remote_extract_enabled=False)
    plan = {"task_contract": {"original_task": "分析这个 javascript 动态渲染页面"}}
    assert "browser" in task_required_capabilities(plan)
    blockers, _ = admit_task_capabilities(plan, settings, [{"name": "web_fetcher", "configured": True, "verification": "unknown", "usable": True}])
    assert any(item["capability"] == "browser" for item in blockers)


def test_unknown_configured_capability_is_attemptable_but_warned():
    settings = Settings(fetch_browser_enabled=True)
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["full_text"]}}
    blockers, warnings = admit_task_capabilities(plan, settings, [{"name": "web_fetcher", "configured": True, "verification": "unknown", "usable": True}])
    assert blockers == []
    assert any("unverified" in warning for warning in warnings)


def test_explicit_missing_pdf_is_blocked():
    settings = Settings(pdf_reader_enabled=False)
    plan = {"task_contract": {"required_capabilities": ["pdf"]}}
    blockers, _ = admit_task_capabilities(plan, settings, [{"name": "pdf_reader", "configured": False, "verification": "unknown", "usable": False}])
    assert blockers and blockers[0]["capability"] == "pdf"

def test_all_known_backends_failed_blocks():
    settings = Settings()
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["full_text"]}}
    rows = [{"name":"web_fetcher","configured":True,"verification":"probed","usable":False},{"name":"browser","configured":True,"verification":"probed","usable":False},{"name":"remote_extract","configured":False,"verification":"probed","usable":False}]
    blockers, _ = admit_task_capabilities(plan, settings, rows)
    assert any(x["code"] == "capability_unavailable" for x in blockers)


def test_failed_http_with_verified_remote_candidate_passes():
    settings = Settings()
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["full_text"]}}
    rows = [{"name":"web_fetcher","configured":True,"verification":"probed","usable":True}]
    assert admit_task_capabilities(plan, settings, rows)[0] == []


def test_pdf_permission_is_required():
    settings = Settings()
    plan = {"allowed_tools": [], "task_contract": {"required_capabilities": ["pdf"]}}
    blockers, _ = admit_task_capabilities(plan, settings, [{"name":"pdf_reader","configured":True,"verification":"probed","usable":True}])
    assert blockers and blockers[0]["capability"] == "pdf"


def test_search_permission_is_required():
    settings = Settings(tavily_api_key="x")
    plan = {"allowed_tools": [], "task_contract": {"required_capabilities": ["search"]}}
    blockers, _ = admit_task_capabilities(plan, settings, [{"name":"tavily","configured":True,"verification":"probed","usable":True}])
    assert blockers and blockers[0]["capability"] == "search"


def test_academic_permission_is_required():
    settings = Settings()
    plan = {"allowed_tools": [], "task_contract": {"required_capabilities": ["academic"]}}
    blockers, _ = admit_task_capabilities(plan, settings, [{"name":"academic_search","configured":True,"verification":"probed","usable":True}])
    assert blockers and blockers[0]["capability"] == "academic"


def test_remote_without_provider_is_not_candidate():
    settings = Settings(fetch_browser_enabled=False, fetch_remote_extract_enabled=True, fetch_remote_extract_provider_order="firecrawl")
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["browser"]}}
    blockers, _ = admit_task_capabilities(plan, settings, [{"name":"remote_extract","configured":False,"verification":"unknown","usable":False}])
    assert blockers


def test_existing_browser_probe_false_is_not_overwritten():
    settings = Settings(fetch_browser_enabled=True)
    plan = {"allowed_tools": ["web_fetcher"], "task_contract": {"required_capabilities": ["browser"]}}
    rows = [{"name":"browser","configured":True,"verification":"probed","usable":False},{"name":"web_fetcher","configured":True,"verification":"unknown","usable":True}]
    blockers, _ = admit_task_capabilities(plan, settings, rows)
    assert any(x["code"] == "capability_unavailable" for x in blockers)


def test_config_fingerprint_changes_with_secret_without_exposing_it():
    from app.runtime.preflight import _config_fingerprint
    a = Settings(llm_api_key="secret-alpha-unique")
    b = Settings(llm_api_key="secret-beta-unique")
    assert _config_fingerprint(a) != _config_fingerprint(b)
    assert "secret-alpha" not in _config_fingerprint(a) and "secret-beta" not in _config_fingerprint(b)
