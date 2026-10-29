from fastapi import FastAPI

from software_factory import __version__
from software_factory.config import Settings
from software_factory.linear.webhook import router as linear_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="software-factory", version=__version__)
    app.state.settings = settings or Settings.from_env()
    app.include_router(linear_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
