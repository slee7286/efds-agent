from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from efds_agent.agent.orchestrator import AgentOrchestrator
from efds_agent.api.routes import router
from efds_agent.config import Settings, get_settings
from efds_agent.observability.tracing import configure_logging
from efds_agent.providers.factory import build_provider
from efds_agent.security.authorization import SupabaseAuthorization


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="EFDS Agent", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.allowed_origins.split(",") if x.strip()], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    app.state.settings = settings
    app.state.authorization = SupabaseAuthorization(settings)
    app.state.orchestrator = AgentOrchestrator(settings, build_provider(settings))
    app.include_router(router)
    return app


app = create_app()
