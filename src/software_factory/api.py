from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from temporalio.client import Client

from software_factory import __version__
from software_factory.config import Settings
from software_factory.linear.webhook import router as linear_router


def create_app(settings: Settings | None = None, temporal_client: Any = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.temporal is None:
            app.state.temporal = await Client.connect(settings.temporal_address)
        yield

    app = FastAPI(title="software-factory", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.temporal = temporal_client
    app.include_router(linear_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
