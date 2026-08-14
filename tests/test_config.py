import pytest

from efds_agent.config import Settings


def test_production_settings_fail_closed_without_required_credentials() -> None:
    with pytest.raises(ValueError, match="SUPABASE_URL, SUPABASE_ANON_KEY, AI_API_KEY"):
        Settings(_env_file=None, app_env="production")
