"""Liveness and readiness endpoints."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lcftp_models.errors import ErrorCode, LcftpError

from .deps import SessionDep

__all__ = ["router"]

router = APIRouter(tags=["health"])


class Health(BaseModel):
    """Liveness response."""

    status: str


class Readiness(BaseModel):
    """Readiness response."""

    database: str
    candidates_as_of: datetime | None


@router.get("/health")
async def health() -> Health:
    """Report that the process is alive."""
    return Health(status="ok")


@router.get("/health/ready")
async def ready(session: SessionDep) -> Readiness:
    """Report database health and candidate cache freshness."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as e:
        msg = "Database unavailable"
        raise LcftpError(ErrorCode.internal, msg, 503) from e
    return Readiness(database="ok", candidates_as_of=None)
