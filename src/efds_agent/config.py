from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "efds-agent"
    app_env: str = "development"
    log_level: str = "INFO"
    allowed_origins: str = "http://localhost:3000"
    supabase_url: str = ""
    supabase_anon_key: SecretStr | None = None
    supabase_auth_timeout_seconds: float = 8.0
    ai_provider: Literal["openai"] = "openai"
    default_model: str = "gpt-5.4-mini"
    reasoning_model: str = "gpt-5.4-mini"
    ai_api_key: SecretStr | None = None
    agent_retrieval_k: int = Field(default=10, validation_alias="AGENT_RETRIEVAL_K")
    max_retrieval_k: int = Field(default=20, validation_alias="AGENT_MAX_RETRIEVAL_K")
    max_context_items: int = 10
    max_context_tokens: int = 3500
    max_output_tokens: int = 700
    openai_daily_token_budget: int = 2_500_000
    max_query_chars: int = 1000
    request_timeout_seconds: float = 15.0
    max_conversation_turns: int = 4
    max_conversation_chars: int = 4000
    agent_shared_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        """Fail closed when a production process is missing required secrets."""
        if self.app_env == "production":
            missing: list[str] = []
            if not self.supabase_url:
                missing.append("SUPABASE_URL")
            if not self.supabase_anon_key or not self.supabase_anon_key.get_secret_value():
                missing.append("SUPABASE_ANON_KEY")
            if not self.ai_api_key or not self.ai_api_key.get_secret_value():
                missing.append("AI_API_KEY")
            if missing:
                raise ValueError("Missing required production configuration: " + ", ".join(missing))
        return self

    @property
    def rest_url(self) -> str:
        return self.supabase_url.rstrip("/") + "/rest/v1"

    @property
    def auth_url(self) -> str:
        return self.supabase_url.rstrip("/") + "/auth/v1/user"

    @property
    def is_supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key and self.supabase_anon_key.get_secret_value())

    @property
    def retrieval_k(self) -> int:
        """Return the bounded server-side K; browser input never controls it."""
        return min(max(self.agent_retrieval_k, 1), max(self.max_retrieval_k, 1))

@lru_cache
def get_settings() -> Settings:
    return Settings()
