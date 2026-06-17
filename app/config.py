"""Application configuration for the FastAPI skeleton."""

import os
from pydantic import BaseModel
from dotenv import load_dotenv


load_dotenv()


class Settings(BaseModel):
    """Small settings object; expanded later for model providers."""

    service_name: str = "traceable-research-agent"
    phase: str = "day19"
    api_prefix: str = "/api"
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

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment without exposing secret values."""

        return cls(
            service_name=os.getenv("SERVICE_NAME", "traceable-research-agent"),
            phase=os.getenv("APP_PHASE", "day19"),
            api_prefix=os.getenv("API_PREFIX", "/api"),
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
