"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.sessions import SessionMiddleware

from lcftp_auth.auth import Auth, build_auth
from lcftp_auth.config import AuthConfig

from .api import health
from .api.deps import SessionDep
from .api.errors import install_error_handlers
from .config import Settings
from .db.session import create_engine, create_sessionmaker
from .stores.tokens import SqlTokenStore
from .stores.users import SqlUserStore

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
        app.state.http = httpx.AsyncClient(timeout=30)
        try:
            yield
        finally:
            await app.state.http.aclose()
            if engine is None:
                await db.dispose()

    app = FastAPI(title="Los Cumbres For The People", lifespan=lifespan)
    app.state.settings = config
    auth = _build_auth(config)
    app.state.auth = auth
    app.add_middleware(SessionMiddleware, **auth.session_middleware_kwargs())
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    return app


def get_http(request: Request) -> httpx.AsyncClient:
    """Return the shared outbound HTTP client."""
    http: httpx.AsyncClient = request.app.state.http
    return http


def get_user_store(session: SessionDep) -> SqlUserStore:
    """Return a user store bound to the request's session."""
    return SqlUserStore(session)


def get_token_store(session: SessionDep) -> SqlTokenStore:
    """Return a token store bound to the request's session."""
    return SqlTokenStore(session)


def _build_auth(settings: Settings) -> Auth:
    config = AuthConfig(
        github_client_id=settings.github_client_id,
        github_client_secret=settings.github_client_secret,
        github_org=settings.github_org,
        base_url=settings.base_url,
        session_secret=settings.session_secret,
    )
    return build_auth(
        config,
        user_store=get_user_store,
        token_store=get_token_store,
        http=get_http,
    )
