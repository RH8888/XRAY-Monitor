from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Event, Watchlist
from app.database.repositories.watchlists import WatchHitRepository, WatchlistRepository
from app.watchlist.matcher import (
    matches_cidr,
    matches_exact_domain,
    matches_exact_ip,
    matches_wildcard_domain,
    normalize_domain,
)
from app.watchlist.types import WatchlistMatch, WatchlistTarget, WatchTargetType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchlistEntry:
    client_id: str
    label: str | None = None
    limit_bytes: int | None = None


class WatchlistService:
    """Loads enabled watchlist targets, evaluates events, and records hits."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._watchlists = WatchlistRepository(session) if session is not None else None
        self._watch_hits = WatchHitRepository(session) if session is not None else None

    async def list_enabled_targets(self, *, limit: int = 1000) -> list[WatchlistTarget]:
        """Return typed targets derived from enabled database watchlist rows."""

        if self._watchlists is None:
            return []

        watchlists = await self._watchlists.list_enabled(limit=limit)
        targets: list[WatchlistTarget] = []
        for watchlist in watchlists:
            targets.extend(_targets_for_watchlist(watchlist))
        return targets

    async def evaluate_event(self, event: Event, *, limit: int = 1000) -> list[WatchlistMatch]:
        """Evaluate an event against enabled watchlists and create WatchHit rows for matches."""

        if self._watch_hits is None:
            return []

        matches: list[WatchlistMatch] = []
        for target in await self.list_enabled_targets(limit=limit):
            if not _target_matches_event(target, event):
                continue

            match = WatchlistMatch(
                watchlist_id=target.watchlist_id,
                label=target.label,
                target_type=target.target_type,
                value=target.value,
                reason=_match_reason(target),
            )
            await self._watch_hits.add(
                watchlist_id=match.watchlist_id,
                event_id=event.id,
                hit_at=datetime.now(UTC),
                reason=match.reason,
            )
            matches.append(match)

        return matches

    async def list_entries(self) -> list[WatchlistEntry]:
        return []


def _targets_for_watchlist(watchlist: Watchlist) -> list[WatchlistTarget]:
    targets: list[WatchlistTarget] = []
    if watchlist.domain_pattern:
        target_type = _domain_target_type(watchlist.domain_pattern)
        if target_type is None:
            logger.warning(
                "skipping invalid watchlist domain target",
                extra={"watchlist_id": watchlist.id, "value": watchlist.domain_pattern},
            )
        else:
            targets.append(
                WatchlistTarget(
                    watchlist_id=watchlist.id,
                    label=watchlist.label,
                    value=watchlist.domain_pattern,
                    target_type=target_type,
                )
            )

    if watchlist.ip_pattern:
        target_type = _ip_target_type(watchlist.ip_pattern)
        if target_type is None:
            logger.warning(
                "skipping invalid watchlist IP target",
                extra={"watchlist_id": watchlist.id, "value": watchlist.ip_pattern},
            )
        else:
            targets.append(
                WatchlistTarget(
                    watchlist_id=watchlist.id,
                    label=watchlist.label,
                    value=watchlist.ip_pattern,
                    target_type=target_type,
                )
            )
    return targets


def _domain_target_type(value: str) -> WatchTargetType | None:
    stripped = value.strip().lower().rstrip(".")
    if stripped.startswith("*."):
        return WatchTargetType.WILDCARD_DOMAIN if normalize_domain(stripped[2:]) else None
    return WatchTargetType.EXACT_DOMAIN if normalize_domain(stripped) else None


def _ip_target_type(value: str) -> WatchTargetType | None:
    stripped = value.strip()
    if "/" in stripped:
        return WatchTargetType.CIDR if _valid_cidr(stripped) else None
    return WatchTargetType.EXACT_IP if matches_exact_ip(stripped, stripped) else None


def _valid_cidr(value: str) -> bool:
    from ipaddress import ip_network

    try:
        ip_network(value.strip())
    except ValueError:
        return False
    return True


def _target_matches_event(target: WatchlistTarget, event: Event) -> bool:
    if target.target_type is WatchTargetType.EXACT_DOMAIN:
        return matches_exact_domain(event.domain, target.value)
    if target.target_type is WatchTargetType.WILDCARD_DOMAIN:
        return matches_wildcard_domain(event.domain, target.value)
    if target.target_type is WatchTargetType.EXACT_IP:
        return matches_exact_ip(event.ip_address, target.value)
    if target.target_type is WatchTargetType.CIDR:
        return matches_cidr(event.ip_address, target.value)
    return False


def _match_reason(target: WatchlistTarget) -> str:
    return f"{target.target_type.value} matched {target.value}"
