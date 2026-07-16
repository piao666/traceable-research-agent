"""Application configuration for the FastAPI skeleton."""

import os
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator
from dotenv import load_dotenv


load_dotenv()


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
    tenant_header_name: str = "X-Tenant-ID"
    user_header_name: str = "X-User-ID"
    default_tenant_id: str = "demo"
    default_user_id: str = "local-user"
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
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    llm_timeout_seconds: int = 20
    llm_max_retries: int = 1
    llm_strict_json: bool = True
    evidence_pipeline_version: str = "v2"
    evidence_extractor_version: str = "v2-rule-1"
    evidence_artifact_root: str = "workspace/artifacts"
    evidence_passage_max_chars: int = 4000
    rag_embedding_backend: str = "deterministic"
    rag_vector_backend: str = "json"
    rag_model_path: str | None = r"E:\Models\bge-small-zh-v1.5"
    rag_chroma_dir: str = "workspace/chroma"
    rag_collection_name: str = "traceable_research_docs"
    rag_device: str = "cpu"
    rag_normalize_embeddings: bool = True
    rag_real_backend_enabled: bool = False
    rag_retrieval_mode: str = "hybrid"
    rag_bm25_enabled: bool = True
    rag_hybrid_enabled: bool = True
    rag_rrf_k: int = 60
    rag_dense_candidate_multiplier: int = 2
    rag_bm25_candidate_multiplier: int = 2
    rag_chunk_experiment_sizes: str = "256,512,1024"
    rag_chunk_experiment_output: str = "workspace/eval_outputs/rag_chunk_experiment_results.json"

    @field_validator("external_tools_default_mode", mode="before")
    @classmethod
    def validate_external_tools_default_mode(cls, value: object) -> str:
        normalized = str(value or "real").strip().lower()
        return normalized if normalized in {"real", "mock"} else "real"

    @field_validator("github_tool_default_mode", mode="before")
    @classmethod
    def validate_github_tool_default_mode(cls, value: object) -> str:
        normalized = str(value or "public_api").strip().lower()
        return normalized if normalized in {"public_api", "mock"} else "public_api"

    @field_validator("rag_retrieval_mode", mode="before")
    @classmethod
    def validate_rag_retrieval_mode(cls, value: object) -> str:
        normalized = str(value or "hybrid").strip().lower()
        return normalized if normalized in {"dense", "bm25", "hybrid"} else "hybrid"

    @field_validator("rag_rrf_k", mode="before")
    @classmethod
    def validate_rag_rrf_k(cls, value: object) -> int:
        return _bounded_value(value, 60, 1, 1000)

    @field_validator("rag_dense_candidate_multiplier", "rag_bm25_candidate_multiplier", mode="before")
    @classmethod
    def validate_rag_candidate_multiplier(cls, value: object) -> int:
        return _bounded_value(value, 2, 1, 10)

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
        if normalized not in {"deterministic", "qwen", "deepseek"}:
            raise ValueError("LLM provider must be deterministic, qwen, or deepseek")
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
            if not self.get_llm_api_key(self.llm_provider):
                raise ValueError(
                    f"REPORT_GENERATION_MODE=llm requires {self.llm_provider.upper()} API credentials"
                )
        if self.offline_mode and self.llm_planner_enabled and self.llm_planner_mode != "deterministic":
            raise ValueError("Offline mode requires deterministic LLM planner mode")
        if self.offline_mode and self.execution_mode == "react" and self.react_llm_provider != "deterministic":
            raise ValueError("Offline ReAct mode requires REACT_LLM_PROVIDER=deterministic")
        if self.mcp_readonly_mode and self.mcp_allow_write_tools:
            raise ValueError("MCP_ALLOW_WRITE_TOOLS=true conflicts with MCP_READONLY_MODE=true")
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
        return min(max(parsed, 1), 10)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment without exposing secret values."""

        return cls(
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
            tenant_header_name=os.getenv(
                "TENANT_HEADER_NAME", "X-Tenant-ID"
            ).strip()
            or "X-Tenant-ID",
            user_header_name=os.getenv("USER_HEADER_NAME", "X-User-ID").strip()
            or "X-User-ID",
            default_tenant_id=os.getenv("DEFAULT_TENANT_ID", "demo").strip()
            or "demo",
            default_user_id=os.getenv("DEFAULT_USER_ID", "local-user").strip()
            or "local-user",
            external_tools_default_mode=_env_choice(
                "EXTERNAL_TOOLS_DEFAULT_MODE", "real", {"real", "mock"}
            ),
            offline_mode=_env_bool("OFFLINE_MODE", False),
            allow_mock_fallback=_env_bool("ALLOW_MOCK_FALLBACK", False),
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
                "EXECUTION_MODE", "planned", {"planned", "react"}
            ),
            react_enabled=_env_bool("REACT_ENABLED", True),
            react_max_steps=_env_bounded_int("REACT_MAX_STEPS", 8, 1, 20),
            react_same_tool_max_calls=_env_bounded_int(
                "REACT_SAME_TOOL_MAX_CALLS", 3, 1, 10
            ),
            react_llm_provider=os.getenv("REACT_LLM_PROVIDER", "qwen").strip().lower()
            or "qwen",
            react_llm_model=os.getenv("REACT_LLM_MODEL", "qwen-plus").strip()
            or "qwen-plus",
            react_decision_strict_json=_env_bool(
                "REACT_DECISION_STRICT_JSON", True
            ),
            react_fallback_to_planned=_env_bool(
                "REACT_FALLBACK_TO_PLANNED", True
            ),
            react_finish_on_invalid_decision=_env_bool(
                "REACT_FINISH_ON_INVALID_DECISION", True
            ),
            llm_planner_enabled=_env_bool("LLM_PLANNER_ENABLED", False),
            report_generation_mode=os.getenv(
                "REPORT_GENERATION_MODE", "deterministic"
            ),
            llm_provider=os.getenv("LLM_PROVIDER", "qwen").strip() or "qwen",
            llm_planner_mode=os.getenv("LLM_PLANNER_MODE", "auto").strip() or "auto",
            llm_model=_env_optional("LLM_MODEL"),
            llm_base_url=_env_optional("LLM_BASE_URL"),
            deepseek_api_key=_env_optional("DEEPSEEK_API_KEY"),
            qwen_api_key=_env_optional("QWEN_API_KEY"),
            llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 20),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", 1),
            llm_strict_json=_env_bool("LLM_STRICT_JSON", True),
            evidence_pipeline_version=os.getenv(
                "EVIDENCE_PIPELINE_VERSION", "v2"
            ),
            evidence_extractor_version=os.getenv(
                "EVIDENCE_EXTRACTOR_VERSION", "v2-rule-1"
            ).strip()
            or "v2-rule-1",
            evidence_artifact_root=os.getenv(
                "EVIDENCE_ARTIFACT_ROOT", "workspace/artifacts"
            ).strip()
            or "workspace/artifacts",
            evidence_passage_max_chars=_env_bounded_int(
                "EVIDENCE_PASSAGE_MAX_CHARS", 4000, 500, 20000
            ),
            rag_embedding_backend=os.getenv(
                "RAG_EMBEDDING_BACKEND", "deterministic"
            ).strip()
            or "deterministic",
            rag_vector_backend=os.getenv("RAG_VECTOR_BACKEND", "json").strip()
            or "json",
            rag_model_path=_env_optional("RAG_MODEL_PATH")
            or r"E:\Models\bge-small-zh-v1.5",
            rag_chroma_dir=os.getenv("RAG_CHROMA_DIR", "workspace/chroma").strip()
            or "workspace/chroma",
            rag_collection_name=os.getenv(
                "RAG_COLLECTION_NAME", "traceable_research_docs"
            ).strip()
            or "traceable_research_docs",
            rag_device=os.getenv("RAG_DEVICE", "cpu").strip() or "cpu",
            rag_normalize_embeddings=_env_bool("RAG_NORMALIZE_EMBEDDINGS", True),
            rag_real_backend_enabled=_env_bool("RAG_REAL_BACKEND_ENABLED", False),
            rag_retrieval_mode=_env_choice(
                "RAG_RETRIEVAL_MODE", "hybrid", {"dense", "bm25", "hybrid"}
            ),
            rag_bm25_enabled=_env_bool("RAG_BM25_ENABLED", True),
            rag_hybrid_enabled=_env_bool("RAG_HYBRID_ENABLED", True),
            rag_rrf_k=_env_bounded_int("RAG_RRF_K", 60, 1, 1000),
            rag_dense_candidate_multiplier=_env_bounded_int(
                "RAG_DENSE_CANDIDATE_MULTIPLIER", 2, 1, 10
            ),
            rag_bm25_candidate_multiplier=_env_bounded_int(
                "RAG_BM25_CANDIDATE_MULTIPLIER", 2, 1, 10
            ),
            rag_chunk_experiment_sizes=os.getenv(
                "RAG_CHUNK_EXPERIMENT_SIZES", "256,512,1024"
            ).strip()
            or "256,512,1024",
            rag_chunk_experiment_output=os.getenv(
                "RAG_CHUNK_EXPERIMENT_OUTPUT",
                "workspace/eval_outputs/rag_chunk_experiment_results.json",
            ).strip()
            or "workspace/eval_outputs/rag_chunk_experiment_results.json",
        )

    def get_llm_api_key(self, provider: str) -> str | None:
        """Return the configured API key for provider without logging it."""

        normalized = provider.lower()
        if normalized == "deepseek":
            return self.deepseek_api_key
        if normalized == "qwen":
            return self.qwen_api_key
        return None

    def get_llm_provider_config(self, provider: str) -> dict:
        """Return non-secret provider config."""

        normalized = provider.lower()
        use_overrides = normalized == self.llm_provider.lower()
        if normalized == "deepseek":
            return {
                "provider": "deepseek",
                "api_key_env_name": "DEEPSEEK_API_KEY",
                "default_base_url": "https://api.deepseek.com",
                "default_model": "deepseek-chat",
                "base_url": self.llm_base_url if use_overrides and self.llm_base_url else "https://api.deepseek.com",
                "model": self.llm_model if use_overrides and self.llm_model else "deepseek-chat",
                "has_key": bool(self.deepseek_api_key),
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
                "has_key": bool(self.qwen_api_key),
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
            "llm_base_url": self.llm_base_url,
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
            "github_token_configured": bool(self.github_token),
            "tavily_configured": bool(self.tavily_api_key),
        }

    def get_safe_auth_config_summary(self) -> dict:
        """Return authentication and request-context settings without secrets."""

        return {
            "auth_enabled": self.auth_enabled,
            "demo_api_key_configured": bool(self.demo_api_key),
            "auth_header_name": self.auth_header_name,
            "allow_auth_disabled_in_dev": self.allow_auth_disabled_in_dev,
            "async_run_enabled": self.async_run_enabled,
            "async_run_poll_interval_seconds": self.async_run_poll_interval_seconds,
            "tenant_header_name": self.tenant_header_name,
            "user_header_name": self.user_header_name,
            "default_tenant_id": self.default_tenant_id,
            "default_user_id": self.default_user_id,
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

    def get_safe_rag_config_summary(self) -> dict:
        """Return RAG configuration metadata without reading model contents."""

        model_path = Path(self.rag_model_path) if self.rag_model_path else None
        return {
            "embedding_backend": self.rag_embedding_backend,
            "vector_backend": self.rag_vector_backend,
            "model_path_configured": model_path is not None,
            "model_path_exists": bool(model_path and model_path.exists()),
            "chroma_dir": self.rag_chroma_dir,
            "collection_name": self.rag_collection_name,
            "device": self.rag_device,
            "normalize_embeddings": self.rag_normalize_embeddings,
            "real_backend_enabled": self.rag_real_backend_enabled,
            "retrieval_mode": self.rag_retrieval_mode,
            "bm25_enabled": self.rag_bm25_enabled,
            "hybrid_enabled": self.rag_hybrid_enabled,
            "rrf_k": self.rag_rrf_k,
            "dense_candidate_multiplier": self.rag_dense_candidate_multiplier,
            "bm25_candidate_multiplier": self.rag_bm25_candidate_multiplier,
            "chunk_experiment_sizes": self.rag_chunk_experiment_sizes,
            "chunk_experiment_output": self.rag_chunk_experiment_output,
        }


def _env_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


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
