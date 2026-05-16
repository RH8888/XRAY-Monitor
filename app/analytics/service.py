from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, case, cast, desc, distinct, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Event, User, WatchHit, Watchlist
from app.parser import TrafficSample


@dataclass(frozen=True, slots=True)
class UserTotals:
    user_id: int | None
    client_id: str | None
    display_name: str | None
    email: str | None
    event_count: int
    unique_domains: int
    unique_ips: int
    first_seen: datetime | None
    last_seen: datetime | None


@dataclass(frozen=True, slots=True)
class DestinationCount:
    destination: str
    count: int
    percentage: float


@dataclass(frozen=True, slots=True)
class HourlyActivity:
    hour: int
    count: int
    percentage: float


@dataclass(frozen=True, slots=True)
class RecentActivity:
    event_id: int
    timestamp: datetime
    event_type: str
    client_id: str | None
    display_name: str | None
    domain: str | None
    ip_address: str | None
    inbound_tag: str | None


@dataclass(frozen=True, slots=True)
class WatchlistHitStats:
    watchlist_id: int
    label: str
    hits: int
    percentage: float


@dataclass(frozen=True, slots=True)
class FirstLastSeen:
    first_seen: datetime | None
    last_seen: datetime | None


class AnalyticsService:
    """Computes usage metrics from normalized traffic samples and stored events."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    def usage_percent(self, sample: TrafficSample) -> float | None:
        if not sample.total_bytes:
            return None
        return float(min(sample.used_bytes / sample.total_bytes * 100, 100.0))

    async def active_users(self, *, limit: int = 100, offset: int = 0) -> list[UserTotals]:
        """Return active users with their event totals and first/last seen timestamps."""

        self._require_session()
        result = await self._db.execute(
            select(
                User.id,
                User.client_id,
                User.display_name,
                User.email,
                func.count(Event.id),
                func.count(distinct(Event.domain)),
                func.count(distinct(Event.ip_address)),
                func.min(Event.timestamp),
                func.max(Event.timestamp),
            )
            .select_from(User)
            .outerjoin(Event, Event.user_id == User.id)
            .where(User.is_active.is_(True))
            .group_by(User.id)
            .order_by(User.client_id)
            .limit(limit)
            .offset(offset)
        )
        return [
            UserTotals(
                user_id=row[0],
                client_id=row[1],
                display_name=row[2],
                email=row[3],
                event_count=row[4] or 0,
                unique_domains=row[5] or 0,
                unique_ips=row[6] or 0,
                first_seen=row[7],
                last_seen=row[8],
            )
            for row in result.all()
        ]

    async def per_user_totals(self, *, limit: int = 100, offset: int = 0) -> list[UserTotals]:
        """Return per-user event totals, including inactive and unknown-user traffic."""

        self._require_session()
        known = await self._db.execute(
            select(
                User.id,
                User.client_id,
                User.display_name,
                User.email,
                func.count(Event.id).label("event_count"),
                func.count(distinct(Event.domain)).label("unique_domains"),
                func.count(distinct(Event.ip_address)).label("unique_ips"),
                func.min(Event.timestamp).label("first_seen"),
                func.max(Event.timestamp).label("last_seen"),
            )
            .select_from(User)
            .outerjoin(Event, Event.user_id == User.id)
            .group_by(User.id)
            .order_by(desc("event_count"), User.client_id)
            .limit(limit)
            .offset(offset)
        )
        totals = [
            UserTotals(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4] or 0,
                row[5] or 0,
                row[6] or 0,
                row[7],
                row[8],
            )
            for row in known.all()
        ]
        if offset == 0 and len(totals) < limit:
            unknown = await self._db.execute(
                select(
                    func.count(Event.id),
                    func.count(distinct(Event.domain)),
                    func.count(distinct(Event.ip_address)),
                    func.min(Event.timestamp),
                    func.max(Event.timestamp),
                ).where(Event.user_id.is_(None))
            )
            row = unknown.one()
            if row[0]:
                totals.append(
                    UserTotals(
                        None,
                        None,
                        "Unknown",
                        None,
                        row[0],
                        row[1] or 0,
                        row[2] or 0,
                        row[3],
                        row[4],
                    )
                )
        return totals

    async def user_totals(self, user_name: str) -> UserTotals | None:
        """Return totals for one user selected by client ID, display name, or email."""

        self._require_session()
        result = await self._db.execute(
            select(
                User.id,
                User.client_id,
                User.display_name,
                User.email,
                func.count(Event.id),
                func.count(distinct(Event.domain)),
                func.count(distinct(Event.ip_address)),
                func.min(Event.timestamp),
                func.max(Event.timestamp),
            )
            .select_from(User)
            .outerjoin(Event, Event.user_id == User.id)
            .where(_user_name_filter(user_name))
            .group_by(User.id)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return UserTotals(
            row[0], row[1], row[2], row[3], row[4] or 0, row[5] or 0, row[6] or 0, row[7], row[8]
        )

    async def unique_domains(self, *, user_name: str | None = None) -> int:
        return await self._unique_count(Event.domain, user_name=user_name)

    async def unique_ips(self, *, user_name: str | None = None) -> int:
        return await self._unique_count(Event.ip_address, user_name=user_name)

    async def first_last_seen(self, *, user_name: str | None = None) -> FirstLastSeen:
        self._require_session()
        statement = select(func.min(Event.timestamp), func.max(Event.timestamp)).select_from(Event)
        if user_name:
            statement = statement.join(User, Event.user_id == User.id).where(
                _user_name_filter(user_name)
            )
        result = await self._db.execute(statement)
        row = result.one()
        return FirstLastSeen(row[0], row[1])

    async def recent_activity(self, *, limit: int = 10) -> list[RecentActivity]:
        self._require_session()
        result = await self._db.execute(
            select(
                Event.id,
                Event.timestamp,
                Event.event_type,
                User.client_id,
                User.display_name,
                Event.domain,
                Event.ip_address,
                Event.inbound_tag,
            )
            .select_from(Event)
            .outerjoin(User, Event.user_id == User.id)
            .order_by(Event.timestamp.desc(), Event.id.desc())
            .limit(limit)
        )
        return [RecentActivity(*row) for row in result.all()]

    async def top_domains(self, *, limit: int = 10) -> list[DestinationCount]:
        return await self._top_destinations(Event.domain, limit=limit)

    async def top_ips(self, *, limit: int = 10) -> list[DestinationCount]:
        return await self._top_destinations(Event.ip_address, limit=limit)

    async def hourly_activity_distribution(self) -> list[HourlyActivity]:
        self._require_session()
        hour_expr = cast(func.strftime("%H", Event.timestamp), Float)
        total = await self._event_total()
        result = await self._db.execute(
            select(hour_expr.label("hour"), func.count(Event.id))
            .select_from(Event)
            .group_by("hour")
            .order_by("hour")
        )
        counts = {int(row[0]): row[1] for row in result.all()}
        return [
            HourlyActivity(
                hour=hour,
                count=counts.get(hour, 0),
                percentage=_percentage(counts.get(hour, 0), total),
            )
            for hour in range(24)
        ]

    async def watchlist_hit_counts(self, *, limit: int = 20) -> list[WatchlistHitStats]:
        self._require_session()
        total_hits = await self._watch_hit_total()
        result = await self._db.execute(
            select(Watchlist.id, Watchlist.label, func.count(WatchHit.id).label("hits"))
            .select_from(Watchlist)
            .outerjoin(WatchHit, WatchHit.watchlist_id == Watchlist.id)
            .group_by(Watchlist.id)
            .order_by(desc("hits"), Watchlist.label)
            .limit(limit)
        )
        return [
            WatchlistHitStats(row[0], row[1], row[2] or 0, _percentage(row[2] or 0, total_hits))
            for row in result.all()
        ]

    async def destination_frequency_percentages(self, *, limit: int = 20) -> list[DestinationCount]:
        self._require_session()
        destination = case(
            (Event.domain.is_not(None), Event.domain),
            (Event.ip_address.is_not(None), Event.ip_address),
            else_=literal("unknown"),
        )
        total = await self._event_total()
        result = await self._db.execute(
            select(destination.label("destination"), func.count(Event.id).label("count"))
            .select_from(Event)
            .group_by("destination")
            .order_by(desc("count"), "destination")
            .limit(limit)
        )
        return [
            DestinationCount(row[0], row[1], _percentage(row[1], total)) for row in result.all()
        ]

    async def events_since(self, since: datetime) -> int:
        self._require_session()
        result = await self._db.execute(
            select(func.count(Event.id)).where(Event.timestamp >= since)
        )
        return result.scalar_one() or 0

    async def has_recent_user_activity(self, user_id: int, *, within: timedelta) -> bool:
        self._require_session()
        since = datetime.now(tz=UTC) - within
        result = await self._db.execute(
            select(func.count(Event.id)).where(Event.user_id == user_id, Event.timestamp >= since)
        )
        return bool(result.scalar_one())

    async def _unique_count(self, column: Any, *, user_name: str | None = None) -> int:
        self._require_session()
        statement = (
            select(func.count(distinct(column))).select_from(Event).where(column.is_not(None))
        )
        if user_name:
            statement = statement.join(User, Event.user_id == User.id).where(
                _user_name_filter(user_name)
            )
        result = await self._db.execute(statement)
        return result.scalar_one() or 0

    async def _top_destinations(self, column: Any, *, limit: int) -> list[DestinationCount]:
        self._require_session()
        total = await self._event_total(where_column=column)
        result = await self._db.execute(
            select(column, func.count(Event.id).label("count"))
            .select_from(Event)
            .where(column.is_not(None))
            .group_by(column)
            .order_by(desc("count"), column)
            .limit(limit)
        )
        return [
            DestinationCount(row[0], row[1], _percentage(row[1], total)) for row in result.all()
        ]

    async def _event_total(self, *, where_column: Any | None = None) -> int:
        statement = select(func.count(Event.id)).select_from(Event)
        if where_column is not None:
            statement = statement.where(where_column.is_not(None))
        result = await self._db.execute(statement)
        return result.scalar_one() or 0

    async def _watch_hit_total(self) -> int:
        result = await self._db.execute(select(func.count(WatchHit.id)).select_from(WatchHit))
        return result.scalar_one() or 0

    @property
    def _db(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("AnalyticsService requires an AsyncSession for database metrics")
        return self._session

    def _require_session(self) -> None:
        if self._session is None:
            raise RuntimeError("AnalyticsService requires an AsyncSession for database metrics")


def _percentage(count: int, total: int) -> float:
    if not total:
        return 0.0
    return round(count / total * 100, 2)


def _user_name_filter(user_name: str) -> Any:
    return or_(
        User.client_id == user_name,
        User.display_name == user_name,
        User.email == user_name,
    )
