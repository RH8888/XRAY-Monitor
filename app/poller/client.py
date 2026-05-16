from typing import Any

import httpx

from app.config import Settings


class ThreeXUIClient:
    """Minimal async 3x-ui HTTP client wrapper.

    Endpoint-specific parsing is intentionally left outside this client so alternate panels or
    direct Xray log sources can be added without changing polling orchestration.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=str(settings.three_xui_base_url),
            timeout=30.0,
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        if self._settings.three_xui_token:
            return {"Authorization": f"Bearer {self._settings.three_xui_token}"}
        return {}

    async def fetch_raw_logs(self, count: int) -> list[dict[str, Any]]:
        """Fetch recent panel log records.

        The exact endpoint varies by 3x-ui fork/deployment; this placeholder documents the seam
        where the production endpoint implementation will live.
        """

        response = await self._client.get("/panel/api/server/getLogs", params={"count": count})
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("obj"), list):
            return payload["obj"]
        return []

    async def close(self) -> None:
        await self._client.aclose()
