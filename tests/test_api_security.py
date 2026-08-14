from fastapi.testclient import TestClient

from efds_agent.api.app import create_app
from efds_agent.config import Settings


def test_optional_server_secret_does_not_replace_public_or_user_auth():
    app = create_app(Settings(_env_file=None, agent_shared_secret="server-only"))
    client = TestClient(app)
    assert client.post("/v1/query", json={"query": "What is EFDS?", "scope": "public"}).status_code == 403
    response = client.post("/v1/query", headers={"X-EFDS-Agent-Secret": "server-only"}, json={"query": "What is EFDS?", "scope": "public"})
    assert response.status_code == 200
    assert response.json()["trace"]["provider"] == "none"
