from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WatchHit, Watchlist


class WatchlistRepository:
    """Async persistence operations for watchlist entries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: int,
        label: str,
        domain_pattern: str | None = None,
        ip_pattern: str | None = None,
        limit_bytes: int | None = None,
        is_enabled: bool = True,
    ) -> Watchlist:
        watchlist = Watchlist(
            user_id=user_id,
            label=label,
            domain_pattern=domain_pattern,
            ip_pattern=ip_pattern,
            limit_bytes=limit_bytes,
            is_enabled=is_enabled,
        )
        self._session.add(watchlist)
        await self._session.flush()
        return watchlist

    async def get(self, watchlist_id: int) -> Watchlist | None:
        return await self._session.get(Watchlist, watchlist_id)

    async def list_enabled_for_user(self, *, user_id: int) -> Sequence[Watchlist]:
        result = await self._session.execute(
            select(Watchlist)
            .where(Watchlist.user_id == user_id, Watchlist.is_enabled.is_(True))
            .order_by(Watchlist.label)
        )
        return result.scalars().all()

    async def list_enabled(self, *, limit: int = 100, offset: int = 0) -> Sequence[Watchlist]:
        result = await self._session.execute(
            select(Watchlist)
            .where(Watchlist.is_enabled.is_(True))
            .order_by(Watchlist.label, Watchlist.id)
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> Sequence[Watchlist]:
        result = await self._session.execute(
            select(Watchlist).order_by(Watchlist.label, Watchlist.id).limit(limit).offset(offset)
        )
        return result.scalars().all()

    async def get_by_label(self, *, user_id: int, label: str) -> Watchlist | None:
        result = await self._session.execute(
            select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.label == label)
        )
        return result.scalar_one_or_none()

    async def remove_by_label(self, *, user_id: int, label: str) -> int:
        watchlist = await self.get_by_label(user_id=user_id, label=label)
        if watchlist is None:
            return 0
        await self._session.delete(watchlist)
        await self._session.flush()
        return 1

    async def set_enabled(self, watchlist: Watchlist, *, is_enabled: bool) -> Watchlist:
        watchlist.is_enabled = is_enabled
        await self._session.flush()
        return watchlist


class WatchHitRepository:
    """Async persistence operations for watchlist hits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        watchlist_id: int,
        hit_at: datetime,
        event_id: int | None = None,
        reason: str | None = None,
    ) -> WatchHit:
        hit = WatchHit(
            watchlist_id=watchlist_id,
            event_id=event_id,
            hit_at=hit_at,
            reason=reason,
        )
        self._session.add(hit)
        await self._session.flush()
        return hit

    async def get(self, watch_hit_id: int) -> WatchHit | None:
        return await self._session.get(WatchHit, watch_hit_id)

    async def list_for_watchlist(
        self,
        *,
        watchlist_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[WatchHit]:
        result = await self._session.execute(
            select(WatchHit)
            .where(WatchHit.watchlist_id == watchlist_id)
            .order_by(WatchHit.hit_at.desc(), WatchHit.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_between(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[WatchHit]:
        result = await self._session.execute(
            select(WatchHit)
            .where(WatchHit.hit_at >= start_at, WatchHit.hit_at < end_at)
            .order_by(WatchHit.hit_at.desc(), WatchHit.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()
