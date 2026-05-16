from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Event


class EventRepository:
    """Async persistence operations for normalized events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        timestamp: datetime,
        event_type: str,
        user_id: int | None = None,
        raw_log_id: int | None = None,
        domain: str | None = None,
        ip_address: str | None = None,
        inbound_tag: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(
            timestamp=timestamp,
            event_type=event_type,
            user_id=user_id,
            raw_log_id=raw_log_id,
            domain=domain,
            ip_address=ip_address,
            inbound_tag=inbound_tag,
            details=details,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def get(self, event_id: int) -> Event | None:
        return await self._session.get(Event, event_id)

    async def list_between(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Event]:
        result = await self._session.execute(
            select(Event)
            .where(Event.timestamp >= start_at, Event.timestamp < end_at)
            .order_by(Event.timestamp.desc(), Event.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_for_user(
        self,
        *,
        user_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Event]:
        result = await self._session.execute(
            select(Event)
            .where(Event.user_id == user_id)
            .order_by(Event.timestamp.desc(), Event.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_by_domain(
        self,
        *,
        domain: str,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Event]:
        result = await self._session.execute(
            select(Event)
            .where(Event.domain == domain)
            .order_by(Event.timestamp.desc(), Event.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_by_ip(
        self,
        *,
        ip_address: str,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Event]:
        result = await self._session.execute(
            select(Event)
            .where(Event.ip_address == ip_address)
            .order_by(Event.timestamp.desc(), Event.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()
