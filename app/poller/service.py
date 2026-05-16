from app.config import Settings
from app.parser import TrafficSample
from app.poller.client import ThreeXUIClient


class PollerService:
    """Coordinates fetching raw panel data and converting it to normalized samples."""

    def __init__(self, settings: Settings, client: ThreeXUIClient) -> None:
        self._settings = settings
        self._client = client

    async def poll_once(self) -> list[TrafficSample]:
        raw_records = await self._client.fetch_raw_logs(self._settings.log_count)
        # Future parser implementations should transform raw_records into TrafficSample objects.
        _ = raw_records
        return []
