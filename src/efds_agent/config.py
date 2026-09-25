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
    supabase_publishable_key: SecretStr | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_auth_timeout_seconds: float = 8.0
    ai_provider: Literal["openai"] = "openai"
    # Model tiering. Synthesis uses a mid-tier model; the cheap tier handles
    # query planning and citation verification, where the task is short,
    # structured and latency-tolerant. Cheap-model tiering is the single
    # biggest cost lever, so the expensive tier is opt-in rather than default.
    default_model: str = "gpt-5.6-terra"
    reasoning_model: str = "gpt-6-astra"
    small_model: str = "gpt-5.6-luna"
    # Escalation to the flagship tier is OFF by default: the cost cap matters
    # more than the marginal quality on decomposed questions. Turn it on when a
    # live eval shows the default tier is actually losing multi-hop answers.
    escalate_decomposed_questions: bool = False
    ai_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # Prompt caching requires a stable prefix of at least 1024 tokens on
    # GPT-5.6+; the policy block in agent.prompts is written to clear that
    # threshold precisely so every request reuses the same cached prefix.
    prompt_cache_key: str = "efds-agent-v2"
    enable_query_planning: bool = True
    enable_citation_verification: bool = True
    sub_query_limit: int = 3
    # Reciprocal rank fusion cannot separate a strong lexical hit from a weak
    # semantic one, so the semantic branch needs an explicit floor. Calibrate
    # this against the EFDS corpus with the eval harness before lowering it.
    semantic_floor: float = 0.30
    planner_timeout_seconds: float = 8.0
    verification_timeout_seconds: float = 10.0
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
            if not self.supabase_api_key:
                missing.append("SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY")
            if not self.ai_api_key or not self.ai_api_key.get_secret_value():
                missing.append("AI_API_KEY")
            if not self.agent_shared_secret or len(self.agent_shared_secret.get_secret_value()) < 32:
                missing.append("AGENT_SHARED_SECRET (at least 32 characters)")
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
        return bool(self.supabase_url and self.supabase_api_key)

    @property
    def supabase_api_key(self) -> str | None:
        for key in (self.supabase_publishable_key, self.supabase_anon_key):
            if key and key.get_secret_value():
                return key.get_secret_value()
        return None

    @property
    def retrieval_k(self) -> int:
        """Return the bounded server-side K; browser input never controls it."""
        return min(max(self.agent_retrieval_k, 1), max(self.max_retrieval_k, 1))


@lru_cache
def get_settings() -> Settings:
    return Settings()
