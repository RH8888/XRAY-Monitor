from dataclasses import dataclass


@dataclass(frozen=True)
class WatchlistEntry:
    client_id: str
    label: str | None = None
    limit_bytes: int | None = None


class WatchlistService:
    """Encapsulates tracked clients and leaves storage backend choices behind a service API."""

    async def list_entries(self) -> list[WatchlistEntry]:
        return []
