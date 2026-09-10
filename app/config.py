"""Application configuration for the FastAPI skeleton."""

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from dotenv import load_dotenv


load_dotenv()


RESEARCH_PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "deep": {
        "offline_mode": False,
        "external_tools_default_mode": "real",
        "allow_mock_fallback": False,
        "execution_mode": "react",
        "react_enabled": True,
        "react_max_steps": 12,
        "react_same_tool_max_calls": 6,
        "llm_planner_enabled": True,
        "report_generation_mode": "llm",
        "deep_research_enabled": True,
        "research_max_tool_calls": 80,
        "research_max_llm_calls": 64,
        "research_max_tokens": 200000,
        "research_max_seconds": 1800,
    },
    "standard": {
        "offline_mode": False,
        "external_tools_default_mode": "real",
        "allow_mock_fallback": False,
        "execution_mode": "planned",
        "react_enabled": True,
        "react_max_steps": 8,
        "react_same_tool_max_calls": 3,
        "llm_planner_enabled": True,
        "report_generation_mode": "llm",
        "deep_research_enabled": False,
        "research_max_tool_calls": 40,
        "research_max_llm_calls": 40,
        "research_max_tokens": 100000,
        "research_max_seconds": 900,
    },
    "offline": {
        "offline_mode": True,
        "external_tools_default_mode": "mock",
        "allow_mock_fallback": False,
        "execution_mode": "planned",
        "react_enabled": False,
        "react_max_steps": 8,
        "react_same_tool_max_calls": 3,
        "llm_planner_enabled": False,
        "report_generation_mode": "deterministic",
        "deep_research_enabled": False,
        "research_max_tool_calls": 20,
        "research_max_llm_calls": 12,
        "research_max_tokens": 40000,
        "research_max_seconds": 300,
    },
}

# An installation that has not adopted RESEARCH_PROFILE keeps the pre-R10
# defaults. Copying the new .env.example opts into the real Deep profile.
LEGACY_RUNTIME_DEFAULTS: dict[str, object] = {
    "offline_mode": False,
    "external_tools_default_mode": "real",
    "allow_mock_fallback": False,
    "execution_mode": "planned",
    "react_enabled": True,
    "react_max_steps": 8,
    "react_same_tool_max_calls": 3,
    "llm_planner_enabled": False,
    "report_generation_mode": "deterministic",
    "deep_research_enabled": False,
    "research_max_tool_calls": 40,
    "research_max_llm_calls": 40,
    "research_max_tokens": 100000,
    "research_max_seconds": 900,
}


class Settings(BaseModel):
    """Runtime settings loaded from environment variables.

    The object is intentionally flat for the demo so scripts, Docker, and
    Streamlit can share the same names. Split it into nested settings groups
    before adding another large feature family.
    """

    service_name: str = "traceable-research-agent"
    phase: str = "traceable-research-agent"
    api_prefix: str = "/api"
    auth_enabled: bool = False
    demo_api_key: str | None = None
    auth_header_name: str = "X-API-Key"
    allow_auth_disabled_in_dev: bool = True
    async_run_enabled: bool = True
    async_run_poll_interval_seconds: int = 1
    research_profile: str = "standard"
    search_provider: str = "tavily"
    external_tools_default_mode: str = "real"
    offline_mode: bool = False
    allow_mock_fallback: bool = False
    github_tool_default_mode: str = "public_api"
    github_token: str | None = None
    github_public_api_enabled: bool = True
    github_search_cache_enabled: bool = True
    github_search_cache_path: str = "workspace/cache/github_search_cache.json"
    github_search_cache_ttl_seconds: int = 3600
    github_public_api_timeout_seconds: int = 10
    github_public_api_max_retries: int = 2
    github_public_api_fallback_to_mock: bool = False
    tavily_api_key: str | None = None
    tavily_search_enabled: bool = True
    tavily_default_max_results: int = 5
    tavily_timeout_seconds: int = 15
    tavily_max_retries: int = 2
    tavily_fallback_to_mock: bool = False
    file_reader_allowed_roots: str = "workspace/docs"
    file_reader_hitl_outside_allowed_roots: bool = True
    mcp_readonly_mode: bool = True
    mcp_adapter_mode: str = "github_tavily_readonly"
    mcp_allow_write_tools: bool = False
    mcp_remote_registry_enabled: bool = False
    mcp_remote_servers: str = ""
    mcp_channel_readonly_servers: str = ""
    mcp_channel_interactive_servers: str = ""
    mcp_channel_write_servers: str = ""
    mcp_remote_registration_attempts: int = 3
    mcp_remote_registration_retry_seconds: int = 1
    parallel_execution_enabled: bool = False
    parallel_max_workers: int = 3
    parallel_group_strategy: str = "independent_tools"
    parallel_timeout_seconds: int = 60
    execution_mode: str = "planned"
    react_enabled: bool = True
    react_max_steps: int = 8
    react_same_tool_max_calls: int = 3
    react_llm_provider: str = "qwen"
    react_llm_model: str = "qwen-plus"
    react_decision_strict_json: bool = True
    react_fallback_to_planned: bool = True
    react_finish_on_invalid_decision: bool = True
    llm_planner_enabled: bool = False
    report_generation_mode: str = "deterministic"
    llm_provider: str = "qwen"
    llm_planner_mode: str = "auto"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    llm_timeout_seconds: int = 20
    llm_max_retries: int = 1
    llm_strict_json: bool = True
    evidence_pipeline_version: str = "v2"
    evidence_extractor_version: str = "v2-rule-2"
    evidence_artifact_root: str = "workspace/artifacts"
    evidence_passage_max_chars: int = 4000
    evidence_reasoning_enabled: bool = True
    source_policy_path: str = "config/source_policy.v2.json"
    deep_research_enabled: bool = False
    deep_research_engine_version: str = "v2"
    deep_research_max_depth: int = 2
    deep_research_breadth: int = 3
    research_max_tool_calls: int = Field(default=40, ge=1)
    research_max_llm_calls: int = Field(default=40, ge=1)
    research_max_tokens: int = Field(default=100000, ge=1)
    research_max_seconds: int = Field(default=900, ge=1)
    research_max_estimated_cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    research_tool_cost_estimate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    research_llm_cost_per_million_tokens: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    memory_llm_extraction_enabled: bool = False
    semantic_scholar_api_key: str | None = None
    citation_validation_enabled: bool = True   # Phase 7.5
    citation_validation_llm_enabled: bool = False  # Phase 7.5
    # ── Phase 8.1: source tier governance ────────────────────────
    default_retrieval_profile: str = "generic"
    oversample_factor: int = 2
    max_discovery_candidates: int = 15
    max_fetch_candidates: int = 10
    max_refetch_rounds: int = 2
    # ── Phase 8.2: local fetch hardening ─────────────────────────
    web_fetcher_max_response_bytes: int = 10_485_760  # 10 MB
    web_fetcher_cache_enabled: bool = True
    web_fetcher_cache_ttl_seconds: int = 3600
    web_fetcher_cache_dir: str = "workspace/cache/fetch"
    web_fetcher_trafilatura_enabled: bool = True
    web_fetcher_playwright_enabled: bool = False
    # ── R11: adaptive retrieval router ─────────────────────────────
    fetch_router_enabled: bool = True
    fetch_http_enabled: bool = True
    fetch_browser_enabled: bool = True
    fetch_remote_extract_enabled: bool = True
    fetch_browser_timeout_seconds: int = 20
    fetch_browser_max_concurrency: int = 2
    fetch_remote_extract_provider_order: str = "firecrawl,exa"
    fetch_quality_min_score: float = Field(default=0.55, ge=0.0, le=1.0)
    url_canonicalization_enabled: bool = True
    content_dedup_enabled: bool = True
    # ── Phase 8.3: PDF reader ──────────────────────────────────────
    pdf_reader_enabled: bool = True
    pdf_reader_max_pages: int = 50
    pdf_reader_max_response_bytes: int = 52_428_800  # 50 MB
    pdf_reader_ocr_enabled: bool = False
    pdf_reader_timeout_seconds: int = 30
    # ── Phase 8.4: reference existence gate ──────────────────────────
    reference_verification_enabled: bool = True
    reference_verifier_timeout_seconds: int = 30
    reference_verifier_cache_ttl_seconds: int = 86400
    reference_verifier_cache_dir: str = "workspace/cache/reference"
    reference_verifier_allowed_indexes: str = "crossref,openalex,arxiv,semantic_scholar"
    # ── Phase 8.5: free academic retrievers ──────────────────────────
    openalex_search_enabled: bool = True
    crossref_search_enabled: bool = True

    @field_validator("external_tools_default_mode", mode="before")
    @classmethod
    def validate_external_tools_default_mode(cls, value: object) -> str:
        normalized = str(value or "real").strip().lower()
        return normalized if normalized in {"real", "mock"} else "real"

    @field_validator("research_profile", mode="before")
    @classmethod
    def validate_research_profile(cls, value: object) -> str:
        normalized = str(value or "standard").strip().lower()
        if normalized not in RESEARCH_PROFILE_DEFAULTS:
            raise ValueError("RESEARCH_PROFILE must be deep, standard, or offline")
        return normalized

    @field_validator("search_provider", mode="before")
    @classmethod
    def validate_search_provider(cls, value: object) -> str:
        normalized = str(value or "tavily").strip().lower()
        if normalized != "tavily":
            raise ValueError("SEARCH_PROVIDER currently supports tavily")
        return normalized

    @field_validator("github_tool_default_mode", mode="before")
    @classmethod
    def validate_github_tool_default_mode(cls, value: object) -> str:
        normalized = str(value or "public_api").strip().lower()
        return normalized if normalized in {"public_api", "mock"} else "public_api"

    @field_validator("execution_mode", mode="before")
    @classmethod
    def validate_execution_mode(cls, value: object) -> str:
        normalized = str(value or "planned").strip().lower()
        return normalized if normalized in {"planned", "react"} else "planned"

    @field_validator("report_generation_mode", mode="before")
    @classmethod
    def validate_report_generation_mode(cls, value: object) -> str:
        normalized = str(value or "deterministic").strip().lower()
        if normalized not in {"deterministic", "llm"}:
            raise ValueError("REPORT_GENERATION_MODE must be deterministic or llm")
        return normalized

    @field_validator("llm_provider", "react_llm_provider", mode="before")
    @classmethod
    def validate_llm_provider(cls, value: object) -> str:
        normalized = str(value or "qwen").strip().lower()
        if normalized not in {"deterministic", "openai_compatible", "qwen", "deepseek"}:
            raise ValueError("LLM provider must be deterministic, openai_compatible, qwen, or deepseek")
        return normalized

    @field_validator("llm_planner_mode", mode="before")
    @classmethod
    def validate_llm_planner_mode(cls, value: object) -> str:
        normalized = str(value or "auto").strip().lower()
        if normalized not in {"deterministic", "auto", "llm"}:
            raise ValueError("LLM_PLANNER_MODE must be deterministic, auto, or llm")
        return normalized

    @field_validator("evidence_pipeline_version", mode="before")
    @classmethod
    def validate_evidence_pipeline_version(cls, value: object) -> str:
        normalized = str(value or "v2").strip().lower()
        if normalized not in {"v1", "v2"}:
            raise ValueError("EVIDENCE_PIPELINE_VERSION must be v1 or v2")
        return normalized

    @field_validator("evidence_passage_max_chars", mode="before")
    @classmethod
    def validate_evidence_passage_max_chars(cls, value: object) -> int:
        return _bounded_value(value, 4000, 500, 20000)

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> "Settings":
        if self.report_generation_mode == "llm":
            if self.offline_mode:
                raise ValueError("REPORT_GENERATION_MODE=llm conflicts with OFFLINE_MODE=true")
            if self.llm_provider == "deterministic":
                raise ValueError("REPORT_GENERATION_MODE=llm requires a remote LLM provider")
            # Credential presence is a per-plan preflight concern, not a startup failure.
        if self.offline_mode and self.llm_planner_enabled and self.llm_planner_mode != "deterministic":
            raise ValueError("Offline mode requires deterministic LLM planner mode")
        if self.offline_mode and self.execution_mode == "react" and self.react_llm_provider != "deterministic":
            raise ValueError("Offline ReAct mode requires REACT_LLM_PROVIDER=deterministic")
        if self.mcp_readonly_mode and self.mcp_allow_write_tools:
            raise ValueError("MCP_ALLOW_WRITE_TOOLS=true conflicts with MCP_READONLY_MODE=true")
        if self.evidence_reasoning_enabled:
            if self.evidence_pipeline_version != "v2":
                raise ValueError("Evidence reasoning requires EVIDENCE_PIPELINE_VERSION=v2")
            if not Path(self.source_policy_path).is_file():
                raise ValueError(f"Source policy file does not exist: {self.source_policy_path}")
            from app.evidence.policy import load_source_policy

            try:
                load_source_policy(self.source_policy_path)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Invalid source policy: {self.source_policy_path}") from exc
        return self

    @field_validator("parallel_max_workers", mode="before")
    @classmethod
    def validate_parallel_max_workers(cls, value: object) -> int:
        return _bounded_value(value, 3, 1, 8)

    @field_validator("parallel_timeout_seconds", mode="before")
    @classmethod
    def validate_parallel_timeout_seconds(cls, value: object) -> int:
        return _bounded_value(value, 60, 5, 300)

    @field_validator("parallel_group_strategy", mode="before")
    @classmethod
    def validate_parallel_group_strategy(cls, value: object) -> str:
        normalized = str(value or "independent_tools").strip().lower()
        return normalized if normalized in {"independent_tools"} else "independent_tools"

    @field_validator("deep_research_max_depth", mode="before")
    @classmethod
    def validate_deep_research_max_depth(cls, value: object) -> int:
        return _bounded_value(value, 2, 1, 5)

    @field_validator("deep_research_engine_version", mode="before")
    @classmethod
    def validate_deep_research_engine_version(cls, value: object) -> str:
        normalized = str(value or "v2").strip().lower()
        if normalized != "v2":
            raise ValueError("DEEP_RESEARCH_ENGINE_VERSION only supports v2")
        return normalized

    @field_validator("deep_research_breadth", mode="before")
    @classmethod
    def validate_deep_research_breadth(cls, value: object) -> int:
        return _bounded_value(value, 3, 1, 10)

    @field_validator("react_max_steps", mode="before")
    @classmethod
    def validate_react_max_steps(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 8
        return min(max(parsed, 1), 20)

    @field_validator("react_same_tool_max_calls", mode="before")
    @classmethod
    def validate_react_same_tool_max_calls(cls, value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 3
        return min(max(parsed, 1), 20)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment without exposing secret values."""

        profile_value = os.getenv("RESEARCH_PROFILE")
        profile = str(profile_value or "standard").strip().lower() or "standard"
        if profile not in RESEARCH_PROFILE_DEFAULTS:
            raise ValueError("RESEARCH_PROFILE must be deep, standard, or offline")
        defaults = RESEARCH_PROFILE_DEFAULTS[profile] if profile_value is not None else LEGACY_RUNTIME_DEFAULTS
        provider_default = (
            "qwen" if profile_value is None else "deterministic" if profile == "offline" else "openai_compatible"
        )
        provider = os.getenv("LLM_PROVIDER", provider_default).strip().lower() or provider_default
        llm_model = _env_optional("LLM_MODEL")
        react_provider = os.getenv("REACT_LLM_PROVIDER", provider).strip().lower() or provider
        react_model = os.getenv("REACT_LLM_MODEL", llm_model or "").strip()
        return cls(
            research_profile=profile,
            search_provider=_env_choice("SEARCH_PROVIDER", "tavily", {"tavily"}),
            research_max_tool_calls=_env_int("RESEARCH_MAX_TOOL_CALLS", int(defaults["research_max_tool_calls"])),
            research_max_llm_calls=_env_int("RESEARCH_MAX_LLM_CALLS", int(defaults["research_max_llm_calls"])),
            research_max_tokens=_env_int("RESEARCH_MAX_TOKENS", int(defaults["research_max_tokens"])),
            research_max_seconds=_env_int("RESEARCH_MAX_SECONDS", int(defaults["research_max_seconds"])),
            research_max_estimated_cost=os.getenv("RESEARCH_MAX_ESTIMATED_COST", "0"),
            research_tool_cost_estimate=_env_optional("RESEARCH_TOOL_COST_ESTIMATE"),
            research_llm_cost_per_million_tokens=_env_optional("RESEARCH_LLM_COST_PER_MILLION_TOKENS"),
            service_name=os.getenv("SERVICE_NAME", "traceable-research-agent"),
            phase=os.getenv("APP_PHASE", "traceable-research-agent"),
            api_prefix=os.getenv("API_PREFIX", "/api"),
            auth_enabled=_env_bool("AUTH_ENABLED", False),
            demo_api_key=_env_optional("DEMO_API_KEY"),
            auth_header_name=os.getenv("AUTH_HEADER_NAME", "X-API-Key").strip()
            or "X-API-Key",
            allow_auth_disabled_in_dev=_env_bool("ALLOW_AUTH_DISABLED_IN_DEV", True),
            async_run_enabled=_env_bool("ASYNC_RUN_ENABLED", True),
            async_run_poll_interval_seconds=_env_int(
                "ASYNC_RUN_POLL_INTERVAL_SECONDS", 1
            ),
            external_tools_default_mode=_env_choice(
                "EXTERNAL_TOOLS_DEFAULT_MODE", str(defaults["external_tools_default_mode"]), {"real", "mock"}
            ),
            offline_mode=_env_bool("OFFLINE_MODE", bool(defaults["offline_mode"])),
            allow_mock_fallback=_env_bool("ALLOW_MOCK_FALLBACK", bool(defaults["allow_mock_fallback"])),
            github_tool_default_mode=_env_choice(
                "GITHUB_TOOL_DEFAULT_MODE", "public_api", {"public_api", "mock"}
            ),
            github_token=_env_optional("GITHUB_TOKEN"),
            github_public_api_enabled=_env_bool("GITHUB_PUBLIC_API_ENABLED", True),
            github_search_cache_enabled=_env_bool(
                "GITHUB_SEARCH_CACHE_ENABLED", True
            ),
            github_search_cache_path=os.getenv(
                "GITHUB_SEARCH_CACHE_PATH",
                "workspace/cache/github_search_cache.json",
            ).strip()
            or "workspace/cache/github_search_cache.json",
            github_search_cache_ttl_seconds=_env_int(
                "GITHUB_SEARCH_CACHE_TTL_SECONDS", 3600
            ),
            github_public_api_timeout_seconds=_env_int(
                "GITHUB_PUBLIC_API_TIMEOUT_SECONDS", 10
            ),
            github_public_api_max_retries=_env_int(
                "GITHUB_PUBLIC_API_MAX_RETRIES", 2
            ),
            github_public_api_fallback_to_mock=_env_bool(
                "GITHUB_PUBLIC_API_FALLBACK_TO_MOCK", False
            ),
            tavily_api_key=_env_optional("TAVILY_API_KEY"),
            tavily_search_enabled=_env_bool("TAVILY_SEARCH_ENABLED", True),
            tavily_default_max_results=_env_bounded_int(
                "TAVILY_DEFAULT_MAX_RESULTS", 5, 1, 20
            ),
            tavily_timeout_seconds=_env_bounded_int(
                "TAVILY_TIMEOUT_SECONDS", 15, 1, 120
            ),
            tavily_max_retries=_env_bounded_int("TAVILY_MAX_RETRIES", 2, 0, 5),
            tavily_fallback_to_mock=_env_bool("TAVILY_FALLBACK_TO_MOCK", False),
            file_reader_allowed_roots=os.getenv(
                "FILE_READER_ALLOWED_ROOTS", "workspace/docs"
            ).strip()
            or "workspace/docs",
            file_reader_hitl_outside_allowed_roots=_env_bool(
                "FILE_READER_HITL_OUTSIDE_ALLOWED_ROOTS", True
            ),
            mcp_readonly_mode=_env_bool("MCP_READONLY_MODE", True),
            mcp_adapter_mode=os.getenv(
                "MCP_ADAPTER_MODE", "github_tavily_readonly"
            ).strip()
            or "github_tavily_readonly",
            mcp_allow_write_tools=_env_bool("MCP_ALLOW_WRITE_TOOLS", False),
            mcp_remote_registry_enabled=_env_bool(
                "MCP_REMOTE_REGISTRY_ENABLED", False
            ),
            mcp_remote_servers=os.getenv("MCP_REMOTE_SERVERS", "").strip(),
            mcp_channel_readonly_servers=os.getenv(
                "MCP_CHANNEL_READONLY_SERVERS", ""
            ).strip(),
            mcp_channel_interactive_servers=os.getenv(
                "MCP_CHANNEL_INTERACTIVE_SERVERS", ""
            ).strip(),
            mcp_channel_write_servers=os.getenv(
                "MCP_CHANNEL_WRITE_SERVERS", ""
            ).strip(),
            mcp_remote_registration_attempts=_env_bounded_int(
                "MCP_REMOTE_REGISTRATION_ATTEMPTS", 3, 1, 10
            ),
            mcp_remote_registration_retry_seconds=_env_bounded_int(
                "MCP_REMOTE_REGISTRATION_RETRY_SECONDS", 1, 0, 10
            ),
            parallel_execution_enabled=_env_bool(
                "PARALLEL_EXECUTION_ENABLED", False
            ),
            parallel_max_workers=_env_bounded_int("PARALLEL_MAX_WORKERS", 3, 1, 8),
            parallel_group_strategy=_env_choice(
                "PARALLEL_GROUP_STRATEGY",
                "independent_tools",
                {"independent_tools"},
            ),
            parallel_timeout_seconds=_env_bounded_int(
                "PARALLEL_TIMEOUT_SECONDS", 60, 5, 300
            ),
            execution_mode=_env_choice(
                "EXECUTION_MODE", str(defaults["execution_mode"]), {"planned", "react"}
            ),
            react_enabled=_env_bool("REACT_ENABLED", bool(defaults["react_enabled"])),
            react_max_steps=_env_bounded_int("REACT_MAX_STEPS", int(defaults["react_max_steps"]), 1, 20),
            react_same_tool_max_calls=_env_bounded_int(
                "REACT_SAME_TOOL_MAX_CALLS", int(defaults["react_same_tool_max_calls"]), 1, 20
            ),
            react_llm_provider=react_provider,
            react_llm_model=react_model,
            react_decision_strict_json=_env_bool(
                "REACT_DECISION_STRICT_JSON", True
            ),
            react_fallback_to_planned=_env_bool(
                "REACT_FALLBACK_TO_PLANNED", True
            ),
            react_finish_on_invalid_decision=_env_bool(
                "REACT_FINISH_ON_INVALID_DECISION", True
            ),
            llm_planner_enabled=_env_bool("LLM_PLANNER_ENABLED", bool(defaults["llm_planner_enabled"])),
            report_generation_mode=os.getenv(
                "REPORT_GENERATION_MODE", str(defaults["report_generation_mode"])
            ),
            llm_provider=provider,
            llm_planner_mode=os.getenv("LLM_PLANNER_MODE", "auto").strip() or "auto",
            llm_model=llm_model,
            llm_base_url=_env_optional("LLM_BASE_URL"),
            llm_api_key=_env_optional("LLM_API_KEY"),
            deepseek_api_key=_env_optional("DEEPSEEK_API_KEY"),
            qwen_api_key=_env_optional("QWEN_API_KEY"),
            llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 20),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", 1),
            llm_strict_json=_env_bool("LLM_STRICT_JSON", True),
            evidence_pipeline_version=os.getenv(
                "EVIDENCE_PIPELINE_VERSION", "v2"
            ),
            evidence_extractor_version=os.getenv(
                "EVIDENCE_EXTRACTOR_VERSION", "v2-rule-2"
            ).strip()
            or "v2-rule-2",
            evidence_artifact_root=os.getenv(
                "EVIDENCE_ARTIFACT_ROOT", "workspace/artifacts"
            ).strip()
            or "workspace/artifacts",
            evidence_passage_max_chars=_env_bounded_int(
                "EVIDENCE_PASSAGE_MAX_CHARS", 4000, 500, 20000
            ),
            evidence_reasoning_enabled=_env_bool("EVIDENCE_REASONING_ENABLED", True),
            source_policy_path=os.getenv(
                "SOURCE_POLICY_PATH", "config/source_policy.v2.json"
            ).strip()
            or "config/source_policy.v2.json",
            deep_research_enabled=_env_bool("DEEP_RESEARCH_ENABLED", bool(defaults["deep_research_enabled"])),
            deep_research_engine_version=os.getenv(
                "DEEP_RESEARCH_ENGINE_VERSION", "v2"
            ).strip().lower() or "v2",
            deep_research_max_depth=_env_bounded_int("DEEP_RESEARCH_MAX_DEPTH", 2, 1, 5),
            deep_research_breadth=_env_bounded_int("DEEP_RESEARCH_BREADTH", 3, 1, 10),
            memory_llm_extraction_enabled=_env_bool("MEMORY_LLM_EXTRACTION_ENABLED", False),
            semantic_scholar_api_key=_env_optional("SEMANTIC_SCHOLAR_API_KEY"),
            citation_validation_enabled=_env_bool("CITATION_VALIDATION_ENABLED", True),
            citation_validation_llm_enabled=_env_bool("CITATION_VALIDATION_LLM_ENABLED", False),
            # Phase 8.1
            default_retrieval_profile=_env_str("DEFAULT_RETRIEVAL_PROFILE", "generic"),
            oversample_factor=_env_bounded_int("OVERSAMPLE_FACTOR", 2, 1, 3),
            max_discovery_candidates=_env_bounded_int("MAX_DISCOVERY_CANDIDATES", 15, 3, 50),
            max_fetch_candidates=_env_bounded_int("MAX_FETCH_CANDIDATES", 10, 2, 30),
            max_refetch_rounds=_env_bounded_int("MAX_REFETCH_ROUNDS", 2, 1, 5),
            # Phase 8.2
            web_fetcher_max_response_bytes=_env_bounded_int("WEB_FETCHER_MAX_RESPONSE_BYTES", 10_485_760, 1024, 100_000_000),
            web_fetcher_cache_enabled=_env_bool("WEB_FETCHER_CACHE_ENABLED", True),
            web_fetcher_cache_ttl_seconds=_env_bounded_int("WEB_FETCHER_CACHE_TTL_SECONDS", 3600, 60, 86400),
            web_fetcher_cache_dir=_env_str("WEB_FETCHER_CACHE_DIR", "workspace/cache/fetch"),
            web_fetcher_trafilatura_enabled=_env_bool("WEB_FETCHER_TRAFILATURA_ENABLED", True),
            web_fetcher_playwright_enabled=_env_bool("WEB_FETCHER_PLAYWRIGHT_ENABLED", False),
            # R11
            fetch_router_enabled=_env_bool("FETCH_ROUTER_ENABLED", True),
            fetch_http_enabled=_env_bool("FETCH_HTTP_ENABLED", True),
            fetch_browser_enabled=_env_bool("FETCH_BROWSER_ENABLED", True),
            fetch_remote_extract_enabled=_env_bool("FETCH_REMOTE_EXTRACT_ENABLED", True),
            fetch_browser_timeout_seconds=_env_bounded_int("FETCH_BROWSER_TIMEOUT_SECONDS", 20, 3, 120),
            fetch_browser_max_concurrency=_env_bounded_int("FETCH_BROWSER_MAX_CONCURRENCY", 2, 1, 8),
            fetch_remote_extract_provider_order=_env_str("FETCH_REMOTE_EXTRACT_PROVIDER_ORDER", "firecrawl,exa"),
            fetch_quality_min_score=_env_bounded_float("FETCH_QUALITY_MIN_SCORE", 0.55, 0.0, 1.0),
            url_canonicalization_enabled=_env_bool("URL_CANONICALIZATION_ENABLED", True),
            content_dedup_enabled=_env_bool("CONTENT_DEDUP_ENABLED", True),
            # Phase 8.3
            pdf_reader_enabled=_env_bool("PDF_READER_ENABLED", True),
            pdf_reader_max_pages=_env_bounded_int("PDF_READER_MAX_PAGES", 50, 1, 200),
            pdf_reader_max_response_bytes=_env_bounded_int("PDF_READER_MAX_RESPONSE_BYTES", 52_428_800, 1024, 200_000_000),
            pdf_reader_ocr_enabled=_env_bool("PDF_READER_OCR_ENABLED", False),
            pdf_reader_timeout_seconds=_env_bounded_int("PDF_READER_TIMEOUT_SECONDS", 30, 5, 120),
            # Phase 8.4
            reference_verification_enabled=_env_bool("REFERENCE_VERIFICATION_ENABLED", True),
            reference_verifier_timeout_seconds=_env_bounded_int("REFERENCE_VERIFIER_TIMEOUT_SECONDS", 30, 5, 120),
            reference_verifier_cache_ttl_seconds=_env_int("REFERENCE_VERIFIER_CACHE_TTL_SECONDS", 86400),
            reference_verifier_cache_dir=_env_str("REFERENCE_VERIFIER_CACHE_DIR", "workspace/cache/reference"),
            reference_verifier_allowed_indexes=_env_str("REFERENCE_VERIFIER_ALLOWED_INDEXES", "crossref,openalex,arxiv,semantic_scholar"),
            # Phase 8.5
            openalex_search_enabled=_env_bool("OPENALEX_SEARCH_ENABLED", True),
            crossref_search_enabled=_env_bool("CROSSREF_SEARCH_ENABLED", True),
        )

    def get_llm_api_key(self, provider: str) -> str | None:
        """Return the configured API key for provider without logging it."""

        normalized = provider.lower()
        if normalized == "openai_compatible":
            return self.llm_api_key
        if normalized == "deepseek":
            return self.deepseek_api_key or self.llm_api_key
        if normalized == "qwen":
            return self.qwen_api_key or self.llm_api_key
        return None

    def get_llm_provider_config(self, provider: str) -> dict:
        """Return non-secret provider config."""

        normalized = provider.lower()
        use_overrides = normalized == self.llm_provider.lower()
        if normalized == "openai_compatible":
            return {
                "provider": "openai_compatible",
                "api_key_env_name": "LLM_API_KEY",
                "default_base_url": None,
                "default_model": None,
                "base_url": self.llm_base_url,
                "model": self.llm_model,
                "has_key": bool(self.llm_api_key),
            }
        if normalized == "deepseek":
            return {
                "provider": "deepseek",
                "api_key_env_name": "DEEPSEEK_API_KEY",
                "default_base_url": "https://api.deepseek.com",
                "default_model": "deepseek-chat",
                "base_url": self.llm_base_url if use_overrides and self.llm_base_url else "https://api.deepseek.com",
                "model": self.llm_model if use_overrides and self.llm_model else "deepseek-chat",
                "has_key": bool(self.get_llm_api_key("deepseek")),
            }
        if normalized == "qwen":
            return {
                "provider": "qwen",
                "api_key_env_name": "QWEN_API_KEY",
                "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "default_model": "qwen-plus",
                "base_url": self.llm_base_url
                if use_overrides and self.llm_base_url
                else "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": self.llm_model if use_overrides and self.llm_model else "qwen-plus",
                "has_key": bool(self.get_llm_api_key("qwen")),
            }
        return {
            "provider": normalized,
            "api_key_env_name": None,
            "default_base_url": None,
            "default_model": None,
            "base_url": None,
            "model": None,
            "has_key": False,
        }

    def get_safe_llm_config_summary(self) -> dict:
        """Return safe LLM configuration without API key values."""

        return {
            "llm_planner_enabled": self.llm_planner_enabled,
            "report_generation_mode": self.report_generation_mode,
            "llm_provider": self.llm_provider,
            "llm_planner_mode": self.llm_planner_mode,
            "llm_model": self.llm_model,
            "llm_api_key_configured": bool(self.llm_api_key),
            "deepseek_has_key": bool(self.deepseek_api_key),
            "qwen_has_key": bool(self.qwen_api_key),
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_max_retries": self.llm_max_retries,
            "llm_strict_json": self.llm_strict_json,
            "execution_mode": self.execution_mode,
            "react_enabled": self.react_enabled,
            "react_max_steps": self.react_max_steps,
            "react_same_tool_max_calls": self.react_same_tool_max_calls,
            "react_llm_provider": self.react_llm_provider,
            "react_llm_model": self.react_llm_model,
            "react_decision_strict_json": self.react_decision_strict_json,
            "react_fallback_to_planned": self.react_fallback_to_planned,
            "react_finish_on_invalid_decision": self.react_finish_on_invalid_decision,
        }

    def get_safe_runtime_config_summary(self) -> dict:
        """Return startup-relevant settings without credential values."""

        return {
            "research_budget": {
                name: value
                for name, value in self.model_dump().items()
                if name.startswith("research_") and name != "research_profile"
            },
            "research_profile": self.research_profile,
            "search_provider": self.search_provider,
            "service_name": self.service_name,
            "phase": self.phase,
            "offline_mode": self.offline_mode,
            "external_tools_default_mode": self.external_tools_default_mode,
            "execution_mode": self.execution_mode,
            "parallel_execution_enabled": self.parallel_execution_enabled,
            "mcp_remote_registry_enabled": self.mcp_remote_registry_enabled,
            "report_generation_mode": self.report_generation_mode,
            "llm_provider": self.llm_provider,
            "llm_planner_enabled": self.llm_planner_enabled,
            "llm_planner_mode": self.llm_planner_mode,
            "llm_provider_has_key": bool(self.get_llm_api_key(self.llm_provider)),
            "evidence_pipeline_version": self.evidence_pipeline_version,
            "evidence_extractor_version": self.evidence_extractor_version,
            "evidence_artifact_root": self.evidence_artifact_root,
            "evidence_passage_max_chars": self.evidence_passage_max_chars,
            "evidence_reasoning_enabled": self.evidence_reasoning_enabled,
            "source_policy_path": self.source_policy_path,
            "github_token_configured": bool(self.github_token),
            "tavily_configured": bool(self.tavily_api_key),
            "deep_research_enabled": self.deep_research_enabled,
            "deep_research_engine_version": self.deep_research_engine_version,
            "deep_research_max_depth": self.deep_research_max_depth,
            "deep_research_breadth": self.deep_research_breadth,
            "memory_llm_extraction_enabled": self.memory_llm_extraction_enabled,
            "semantic_scholar_configured": bool(self.semantic_scholar_api_key),
            "citation_validation_enabled": self.citation_validation_enabled,
            "citation_validation_llm_enabled": self.citation_validation_llm_enabled,
            # Phase 8.1
            "default_retrieval_profile": self.default_retrieval_profile,
            "oversample_factor": self.oversample_factor,
            "max_discovery_candidates": self.max_discovery_candidates,
            "max_fetch_candidates": self.max_fetch_candidates,
            "max_refetch_rounds": self.max_refetch_rounds,
            # Phase 8.2
            "web_fetcher_max_response_bytes": self.web_fetcher_max_response_bytes,
            "web_fetcher_cache_enabled": self.web_fetcher_cache_enabled,
            "web_fetcher_trafilatura_enabled": self.web_fetcher_trafilatura_enabled,
            "web_fetcher_playwright_enabled": self.web_fetcher_playwright_enabled,
            # R11
            "fetch_router_enabled": self.fetch_router_enabled,
            "fetch_http_enabled": self.fetch_http_enabled,
            "fetch_browser_enabled": self.fetch_browser_enabled,
            "fetch_remote_extract_enabled": self.fetch_remote_extract_enabled,
            "fetch_browser_timeout_seconds": self.fetch_browser_timeout_seconds,
            "fetch_browser_max_concurrency": self.fetch_browser_max_concurrency,
            "fetch_remote_extract_provider_order": self.fetch_remote_extract_provider_order,
            "fetch_quality_min_score": self.fetch_quality_min_score,
            "url_canonicalization_enabled": self.url_canonicalization_enabled,
            "content_dedup_enabled": self.content_dedup_enabled,
            # Phase 8.3
            "pdf_reader_enabled": self.pdf_reader_enabled,
            "pdf_reader_max_pages": self.pdf_reader_max_pages,
            "pdf_reader_ocr_enabled": self.pdf_reader_ocr_enabled,
            # Phase 8.4
            "reference_verification_enabled": self.reference_verification_enabled,
            "reference_verifier_allowed_indexes": self.reference_verifier_allowed_indexes.split(",") if self.reference_verifier_allowed_indexes else [],
            # Phase 8.5
            "openalex_search_enabled": self.openalex_search_enabled,
            "crossref_search_enabled": self.crossref_search_enabled,
        }

    def get_safe_auth_config_summary(self) -> dict:
        """Return authentication settings without secrets."""

        return {
            "auth_enabled": self.auth_enabled,
            "demo_api_key_configured": bool(self.demo_api_key),
            "auth_header_name": self.auth_header_name,
            "allow_auth_disabled_in_dev": self.allow_auth_disabled_in_dev,
            "async_run_enabled": self.async_run_enabled,
            "async_run_poll_interval_seconds": self.async_run_poll_interval_seconds,
        }

    def get_safe_github_mcp_config_summary(self) -> dict:
        """Return GitHub/MCP settings without token contents."""

        return {
            "external_tools_default_mode": self.external_tools_default_mode,
            "offline_mode": self.offline_mode,
            "allow_mock_fallback": self.allow_mock_fallback,
            "github_tool_default_mode": self.github_tool_default_mode,
            "github_token_configured": bool(self.github_token),
            "github_public_api_enabled": self.github_public_api_enabled,
            "github_search_cache_enabled": self.github_search_cache_enabled,
            "github_search_cache_path": self.github_search_cache_path,
            "github_search_cache_ttl_seconds": self.github_search_cache_ttl_seconds,
            "github_public_api_timeout_seconds": self.github_public_api_timeout_seconds,
            "github_public_api_max_retries": self.github_public_api_max_retries,
            "github_public_api_fallback_to_mock": self.github_public_api_fallback_to_mock,
            "tavily_configured": bool(self.tavily_api_key),
            "tavily_search_enabled": self.tavily_search_enabled,
            "tavily_default_max_results": self.tavily_default_max_results,
            "tavily_timeout_seconds": self.tavily_timeout_seconds,
            "tavily_max_retries": self.tavily_max_retries,
            "tavily_fallback_to_mock": self.tavily_fallback_to_mock,
            "file_reader_allowed_roots": self.file_reader_allowed_roots,
            "file_reader_hitl_outside_allowed_roots": self.file_reader_hitl_outside_allowed_roots,
            "mcp_readonly_mode": self.mcp_readonly_mode,
            "mcp_adapter_mode": self.mcp_adapter_mode,
            "mcp_allow_write_tools": self.mcp_allow_write_tools,
            "mcp_remote_registry_enabled": self.mcp_remote_registry_enabled,
            "mcp_remote_servers_configured": bool(self.mcp_remote_servers),
            "mcp_channel_readonly_servers_configured": bool(self.mcp_channel_readonly_servers),
            "mcp_channel_interactive_servers_configured": bool(self.mcp_channel_interactive_servers),
            "mcp_channel_write_servers_configured": bool(self.mcp_channel_write_servers),
            "mcp_remote_registration_attempts": self.mcp_remote_registration_attempts,
            "mcp_remote_registration_retry_seconds": self.mcp_remote_registration_retry_seconds,
            "parallel_execution_enabled": self.parallel_execution_enabled,
            "parallel_max_workers": self.parallel_max_workers,
            "parallel_group_strategy": self.parallel_group_strategy,
            "parallel_timeout_seconds": self.parallel_timeout_seconds,
        }

def _env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _env_bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    return min(max(_env_int(name, default), minimum), maximum)


def _env_bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    value = os.getenv(name)
    try:
        parsed = float(value.strip()) if value is not None else default
    except ValueError:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _bounded_value(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    return value if value in choices else default


settings = Settings.from_env()
