from efds_agent.config import Settings
from efds_agent.providers.base import ModelProvider
from efds_agent.providers.openai import OpenAIProvider


def build_provider(settings: Settings) -> ModelProvider:
    if settings.ai_provider == "openai":
        return OpenAIProvider(settings)
    raise RuntimeError(f"Unsupported AI_PROVIDER={settings.ai_provider!r}")
