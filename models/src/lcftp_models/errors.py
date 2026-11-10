"""Error codes and error responses shared by server and client."""

from enum import StrEnum

from pydantic import JsonValue

from ._base import ContractModel

__all__ = ["ErrorCode", "ErrorResponse", "LcftpError"]


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes."""

    not_authenticated = "not_authenticated"
    forbidden = "forbidden"
    not_found = "not_found"
    invalid_request = "invalid_request"
    plan_superseded = "plan_superseded"
    frame_not_in_plan = "frame_not_in_plan"
    frame_too_large = "frame_too_large"
    conflict = "conflict"
    internal = "internal"


class ErrorResponse(ContractModel):
    """Body of every error response from the LCFTP API."""

    code: ErrorCode
    message: str
    detail: JsonValue | None = None


class LcftpError(Exception):
    """An error to report to an API client as an `ErrorResponse`."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status: int,
        detail: JsonValue | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def to_response(self) -> ErrorResponse:
        """Convert to the response body."""
        return ErrorResponse(
            code=self.code, message=self.message, detail=self.detail
        )
