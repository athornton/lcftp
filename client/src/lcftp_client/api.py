"""HTTP client for the LCFTP server API."""

from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from lcftp_models.errors import ErrorCode, ErrorResponse
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import AvailabilityWindow, ObservationPlan, PlanStatus
from lcftp_models.upload import UploadManifest, UploadResult, UploadTicket

__all__ = ["LcftpApiError", "LcftpAuthError", "ServerClient"]


class LcftpApiError(Exception):
    """The server returned an error."""

    def __init__(self, code: ErrorCode, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class LcftpAuthError(LcftpApiError):
    """The device token was rejected (HTTP 401 or 403)."""


class ServerClient:
    """Typed calls to the LCFTP server, authenticated by device token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http or httpx.AsyncClient(timeout=60)
        self._owns_http = http is None

    async def aclose(self) -> None:
        """Close the HTTP client if this object created it."""
        if self._owns_http:
            await self._http.aclose()

    async def whoami(self) -> ObservatoryProfile:
        """Return the profile of the token's observatory."""
        data = await self._request("GET", "/api/device/whoami")
        return ObservatoryProfile.model_validate(data)

    async def request_plan(
        self, observatory_id: str, window: AvailabilityWindow
    ) -> ObservationPlan:
        """Announce availability and receive a plan."""
        data = await self._request(
            "POST",
            f"/api/observatories/{observatory_id}/availability",
            json=window.model_dump(mode="json"),
        )
        return ObservationPlan.model_validate(data)

    async def get_plan(self, plan_id: str) -> PlanStatus:
        """Fetch a plan and whether it was superseded."""
        data = await self._request("GET", f"/api/plans/{plan_id}")
        return PlanStatus.model_validate(data)

    async def create_upload(self, manifest: UploadManifest) -> UploadTicket:
        """Send an upload manifest and receive signed URLs."""
        data = await self._request(
            "POST", "/api/uploads", json=manifest.model_dump(mode="json")
        )
        return UploadTicket.model_validate(data)

    async def complete_upload(self, batch_id: UUID) -> UploadResult:
        """Ask the server to verify an uploaded batch."""
        data = await self._request("POST", f"/api/uploads/{batch_id}/complete")
        return UploadResult.model_validate(data)

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        r = await self._http.request(
            method, self._base + path, json=json, headers=self._headers
        )
        if r.is_success:
            return r.json()
        try:
            error = ErrorResponse.model_validate_json(r.content)
        except ValidationError:
            error = ErrorResponse(
                code=ErrorCode.internal,
                message=f"HTTP {r.status_code}: {r.text}",
            )
        cls = LcftpAuthError if r.status_code in (401, 403) else LcftpApiError
        raise cls(error.code, error.message, r.status_code)
