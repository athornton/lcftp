"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from .api import health
from .api.errors import install_error_handlers
from .config import Settings
from .db.session import create_engine, create_sessionmaker

__all__ = ["create_app"]


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    store: object | None = None,
) -> FastAPI:
    """Create the LCFTP FastAPI application.

    Parameters
    ----------
    settings
        Configuration; read from the environment if not given.
    engine
        Database engine to use instead of creating one (for tests).
    store
        Object store to use instead of the configured one (for tests).
    """
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = engine or create_engine(config)
        app.state.engine = db
        app.state.sessionmaker = create_sessionmaker(db)
        app.state.store = store
        try:
            yield
        finally:
            if engine is None:
                await db.dispose()

    app = FastAPI(title="Los Cumbres For The People", lifespan=lifespan)
    app.state.settings = config
    install_error_handlers(app)
    app.include_router(health.router)
    return app
