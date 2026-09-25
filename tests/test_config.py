import pytest

from efds_agent.config import Settings


def test_production_settings_fail_closed_without_required_credentials() -> None:
    with pytest.raises(
        ValueError, match="SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY, AI_API_KEY, AGENT_SHARED_SECRET"
    ):
        Settings(_env_file=None, app_env="production")


def test_production_settings_accept_a_publishable_key_without_a_legacy_anon_key() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        ai_api_key="test-only",
        agent_shared_secret="test-only-shared-secret-long-enough",
    )
    assert settings.is_supabase_configured
    assert settings.supabase_api_key == "sb_publishable_test"
