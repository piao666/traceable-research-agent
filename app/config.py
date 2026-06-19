"""Application configuration for the FastAPI skeleton."""

import os
from pathlib import Path

from pydantic import BaseModel
from dotenv import load_dotenv


load_dotenv()


class Settings(BaseModel):
    """Small settings object; expanded later for model providers."""

    service_name: str = "traceable-research-agent"
    phase: str = "day31-github-cache-mcp-readonly"
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
    github_token: str | None = None
    github_search_cache_enabled: bool = True
    github_search_cache_path: str = "workspace/cache/github_search_cache.json"
    github_search_cache_ttl_seconds: int = 3600
    github_public_api_timeout_seconds: int = 10
    github_public_api_max_retries: int = 2
    github_public_api_fallback_to_mock: bool = True
    mcp_readonly_mode: bool = True
    mcp_adapter_mode: str = "github_readonly"
    mcp_allow_write_tools: bool = False
    llm_planner_enabled: bool = False
    llm_provider: str = "qwen"
    llm_planner_mode: str = "auto"
    llm_model: str | None = None
    llm_base_url: str | None = None
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    llm_timeout_seconds: int = 20
    llm_max_retries: int = 1
    llm_strict_json: bool = True
    rag_embedding_backend: str = "deterministic"
    rag_vector_backend: str = "json"
    rag_model_path: str | None = r"E:\Models\bge-small-zh-v1.5"
    rag_chroma_dir: str = "workspace/chroma"
    rag_collection_name: str = "traceable_research_docs"
    rag_device: str = "cpu"
    rag_normalize_embeddings: bool = True
    rag_real_backend_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment without exposing secret values."""

        return cls(
            service_name=os.getenv("SERVICE_NAME", "traceable-research-agent"),
            phase=os.getenv("APP_PHASE", "day31-github-cache-mcp-readonly"),
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
            github_token=_env_optional("GITHUB_TOKEN"),
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
                "GITHUB_PUBLIC_API_FALLBACK_TO_MOCK", True
            ),
            mcp_readonly_mode=_env_bool("MCP_READONLY_MODE", True),
            mcp_adapter_mode=os.getenv(
                "MCP_ADAPTER_MODE", "github_readonly"
            ).strip()
            or "github_readonly",
            mcp_allow_write_tools=_env_bool("MCP_ALLOW_WRITE_TOOLS", False),
            llm_planner_enabled=_env_bool("LLM_PLANNER_ENABLED", False),
            llm_provider=os.getenv("LLM_PROVIDER", "qwen").strip() or "qwen",
            llm_planner_mode=os.getenv("LLM_PLANNER_MODE", "auto").strip() or "auto",
            llm_model=_env_optional("LLM_MODEL"),
            llm_base_url=_env_optional("LLM_BASE_URL"),
            deepseek_api_key=_env_optional("DEEPSEEK_API_KEY"),
            qwen_api_key=_env_optional("QWEN_API_KEY"),
            llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 20),
            llm_max_retries=_env_int("LLM_MAX_RETRIES", 1),
            llm_strict_json=_env_bool("LLM_STRICT_JSON", True),
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
            "llm_provider": self.llm_provider,
            "llm_planner_mode": self.llm_planner_mode,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "deepseek_has_key": bool(self.deepseek_api_key),
            "qwen_has_key": bool(self.qwen_api_key),
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_max_retries": self.llm_max_retries,
            "llm_strict_json": self.llm_strict_json,
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
            "github_token_configured": bool(self.github_token),
            "github_search_cache_enabled": self.github_search_cache_enabled,
            "github_search_cache_path": self.github_search_cache_path,
            "github_search_cache_ttl_seconds": self.github_search_cache_ttl_seconds,
            "github_public_api_timeout_seconds": self.github_public_api_timeout_seconds,
            "github_public_api_max_retries": self.github_public_api_max_retries,
            "github_public_api_fallback_to_mock": self.github_public_api_fallback_to_mock,
            "mcp_readonly_mode": self.mcp_readonly_mode,
            "mcp_adapter_mode": self.mcp_adapter_mode,
            "mcp_allow_write_tools": self.mcp_allow_write_tools,
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


settings = Settings.from_env()
