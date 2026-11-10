"""Render errors as `~lcftp_models.errors.ErrorResponse` bodies."""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lcftp_models.errors import ErrorCode, ErrorResponse, LcftpError

__all__ = ["install_error_handlers"]


async def _lcftp_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, LcftpError):  # pragma: no cover
        raise exc
    return JSONResponse(
        exc.to_response().model_dump(mode="json"), status_code=exc.status
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    body = ErrorResponse(
        code=ErrorCode.invalid_request,
        message="Request validation failed",
        detail=jsonable_encoder(exc.errors()),
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=422)


def install_error_handlers(app: FastAPI) -> None:
    """Register exception handlers producing ``ErrorResponse`` bodies."""
    app.add_exception_handler(LcftpError, _lcftp_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
