from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class ThreeXUIClientError(RuntimeError):
    """Raised when the 3x-ui log API returns an invalid or unsuccessful response."""


@dataclass(frozen=True)
class XrayLogsRequest:
    """Form fields accepted by the 3x-ui xraylogs endpoint."""

    filter: str | None = None
    show_direct: bool | None = None
    show_blocked: bool | None = None
    show_proxy: bool | None = None

    def as_form_data(self) -> dict[str, str]:
        form: dict[str, str] = {}
        if self.filter is not None:
            form["filter"] = self.filter
        if self.show_direct is not None:
            form["showDirect"] = _form_bool(self.show_direct)
        if self.show_blocked is not None:
            form["showBlocked"] = _form_bool(self.show_blocked)
        if self.show_proxy is not None:
            form["showProxy"] = _form_bool(self.show_proxy)
        return form


def _form_bool(value: bool) -> str:
    return "true" if value else "false"


class ThreeXUIClient:
    """Async 3x-ui HTTP client for fetching Xray log chunks."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._max_retries = max(1, max_retries)
        self._retry_backoff_seconds = retry_backoff_seconds
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=str(settings.three_xui_base_url),
            timeout=httpx.Timeout(timeout_seconds),
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        if self._settings.three_xui_token:
            return {"Authorization": f"Bearer {self._settings.three_xui_token}"}
        return {}

    async def fetch_xray_logs(
        self,
        count: int,
        *,
        filter: str | None = None,
        show_direct: bool | None = None,
        show_blocked: bool | None = None,
        show_proxy: bool | None = None,
    ) -> str:
        """Fetch a newline-separated Xray log chunk from ``/panel/api/server/xraylogs/{count}``.

        The panel expects form-encoded filter flags. A valid response is a JSON object with
        ``success`` set to true and ``obj`` containing the newline-separated log text.
        """

        request = XrayLogsRequest(
            filter=filter,
            show_direct=show_direct,
            show_blocked=show_blocked,
            show_proxy=show_proxy,
        )
        endpoint = f"/panel/api/server/xraylogs/{count}"
        form_data = request.as_form_data()
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                logger.info(
                    "requesting Xray logs from 3x-ui API endpoint=%s log_count=%s attempt=%s/%s",
                    endpoint,
                    count,
                    attempt,
                    self._max_retries,
                    extra={
                        "endpoint": endpoint,
                        "log_count": count,
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                        "has_filter": bool(request.filter),
                    },
                )
                response = await self._client.post(endpoint, data=form_data)
                response.raise_for_status()
                logs = self._validate_xray_logs_payload(response.json())
                logger.info(
                    "xray log fetch succeeded endpoint=%s log_count=%s attempt=%s "
                    "status_code=%s fetched_lines=%s",
                    endpoint,
                    count,
                    attempt,
                    response.status_code,
                    len(logs.splitlines()),
                    extra={
                        "endpoint": endpoint,
                        "log_count": count,
                        "attempt": attempt,
                        "status_code": response.status_code,
                        "fetched_lines": len(logs.splitlines()),
                    },
                )
                return logs
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                status_code = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                logger.warning(
                    "xray log fetch request failed endpoint=%s log_count=%s attempt=%s/%s "
                    "status_code=%s",
                    endpoint,
                    count,
                    attempt,
                    self._max_retries,
                    status_code,
                    extra={
                        "endpoint": endpoint,
                        "log_count": count,
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                        "status_code": status_code,
                    },
                    exc_info=True,
                )
            except ValueError as exc:
                logger.warning(
                    "xray log fetch returned non-JSON response endpoint=%s log_count=%s attempt=%s",
                    endpoint,
                    count,
                    attempt,
                    extra={"endpoint": endpoint, "log_count": count, "attempt": attempt},
                    exc_info=True,
                )
                raise ThreeXUIClientError("3x-ui log API returned non-JSON response") from exc
            except ThreeXUIClientError:
                logger.warning(
                    "xray log fetch returned unsuccessful response endpoint=%s "
                    "log_count=%s attempt=%s",
                    endpoint,
                    count,
                    attempt,
                    extra={"endpoint": endpoint, "log_count": count, "attempt": attempt},
                    exc_info=True,
                )
                raise

            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff_seconds * attempt)

        raise ThreeXUIClientError("failed to fetch Xray logs after retries") from last_error

    async def fetch_raw_logs(self, count: int) -> list[dict[str, Any]]:
        """Backward-compatible wrapper returning one mapping per fetched log line."""

        payload = await self.fetch_xray_logs(count)
        return [{"line": line} for line in payload.splitlines()]

    def _validate_xray_logs_payload(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            raise ThreeXUIClientError("3x-ui log API response must be a JSON object")
        if payload.get("success") is not True:
            message = payload.get("msg") or payload.get("error") or "unknown API error"
            raise ThreeXUIClientError(f"3x-ui log API response was not successful: {message}")
        logs = payload.get("obj")
        if not isinstance(logs, str):
            raise ThreeXUIClientError("3x-ui log API response obj must be a string")
        if "\x00" in logs:
            raise ThreeXUIClientError("3x-ui log API response obj contains invalid NUL bytes")
        return logs

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
