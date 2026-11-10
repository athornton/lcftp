"""Fixtures for LCFTP server tests."""

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from lcftp.config import Settings
from lcftp.db.session import create_engine, create_sessionmaker
from lcftp.main import create_app
from tests.support.db import migrate, postgres_url, truncate_all


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with postgres_url() as url:
        migrate(url)
        yield url


@pytest.fixture
def settings(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("LCFTP_DATABASE_URL", database_url)
    monkeypatch.setenv("LCFTP_GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("LCFTP_GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("LCFTP_SESSION_SECRET", "session-secret")
    monkeypatch.setenv("LCFTP_BASE_URL", "https://lcftp.example.com")
    monkeypatch.setenv("LCFTP_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("LCFTP_STORAGE_BUCKET", "test-bucket")
    return Settings()


@pytest_asyncio.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    yield engine
    await truncate_all(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with create_sessionmaker(engine)() as session:
        yield session


@pytest_asyncio.fixture
async def app(
    settings: Settings, engine: AsyncEngine
) -> AsyncIterator[FastAPI]:
    app = create_app(settings, engine=engine)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    base_url = "https://lcftp.example.com"
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        yield client


@pytest_asyncio.fixture
async def other_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Provide a second, independent client (e.g. a different user)."""
    transport = ASGITransport(app=app)
    base_url = "https://lcftp.example.com"
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        yield client
