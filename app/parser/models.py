from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TrafficSample(BaseModel):
    """Normalized traffic reading parsed from a 3x-ui/Xray source."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    email: str | None = None
    upload_bytes: int = 0
    download_bytes: int = 0
    total_bytes: int | None = None
    observed_at: datetime

    @property
    def used_bytes(self) -> int:
        return self.upload_bytes + self.download_bytes
