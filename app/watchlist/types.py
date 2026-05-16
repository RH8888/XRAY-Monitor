from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WatchTargetType(StrEnum):
    """Supported watchlist target types."""

    EXACT_DOMAIN = "exact_domain"
    WILDCARD_DOMAIN = "wildcard_domain"
    EXACT_IP = "exact_ip"
    CIDR = "cidr"


@dataclass(frozen=True)
class WatchlistTarget:
    """A typed target derived from a database watchlist entry."""

    watchlist_id: int
    label: str
    value: str
    target_type: WatchTargetType


@dataclass(frozen=True)
class WatchlistMatch:
    """A successful watchlist match for a single event."""

    watchlist_id: int
    label: str
    target_type: WatchTargetType
    value: str
    reason: str
