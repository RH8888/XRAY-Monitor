from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    """Structured fields extracted from one raw Xray/3x-ui log line.

    Parser output is intentionally nullable: unknown or malformed log formats should still be
    represented by a raw-line event while leaving fields that could not be inferred as ``None``.
    """

    timestamp: datetime | None
    protocol: str | None
    domain: str | None
    ip: str | None
    port: int | None
    user_identifier: str | None
    inbound_tag: str | None
    outbound_tag: str | None
    outbound_type: str | None
    status: str | None
    reason: str | None
    raw_line: str
    parser_name: str
    confidence: float = 0.0
    source: str | None = None

    @property
    def event_type(self) -> str:
        """Return a coarse event type suitable for the current events table."""

        if self.status == "rejected":
            return "blocked"
        if self.outbound_type:
            return self.outbound_type
        if self.outbound_tag:
            return self.outbound_tag
        if self.status == "accepted":
            return "proxy"
        if self.status == "routed":
            return "route"
        return "xray_log"
