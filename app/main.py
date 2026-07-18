"""Framework entrypoint for the Mom3 AI yield agent."""

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()

from app.api.routes import router
from app.core.config import settings
from app.modules.market_intelligence.sync_worker import get_market_sync_worker


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        description=(
            "Live USDC yield research, explainable strategy recommendations, and "
            "user-confirmed Particle Universal Account execution intents."
        ),
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    application.include_router(router)

    @application.on_event("startup")
    async def start_market_sync() -> None:
        get_market_sync_worker().start()

    @application.on_event("shutdown")
    async def stop_market_sync() -> None:
        get_market_sync_worker().stop()

    return application


app = create_app()
