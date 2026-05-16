from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RawLog


class RawLogRepository:
    """Async persistence operations for raw log lines."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        line: str,
        line_hash: str,
        observed_at: datetime,
        source: str | None = None,
    ) -> RawLog:
        raw_log = RawLog(
            line=line,
            line_hash=line_hash,
            observed_at=observed_at,
            source=source,
        )
        self._session.add(raw_log)
        await self._session.flush()
        return raw_log

    async def get(self, raw_log_id: int) -> RawLog | None:
        return await self._session.get(RawLog, raw_log_id)

    async def get_by_hash(self, line_hash: str) -> RawLog | None:
        result = await self._session.execute(select(RawLog).where(RawLog.line_hash == line_hash))
        return result.scalar_one_or_none()

    async def exists_by_hash(self, line_hash: str) -> bool:
        return await self.get_by_hash(line_hash) is not None

    async def existing_hashes(self, line_hashes: Iterable[str]) -> set[str]:
        hashes = set(line_hashes)
        if not hashes:
            return set()
        result = await self._session.execute(
            select(RawLog.line_hash).where(RawLog.line_hash.in_(hashes))
        )
        return set(result.scalars().all())

    async def list_recent(self, *, limit: int = 100, offset: int = 0) -> Sequence[RawLog]:
        result = await self._session.execute(
            select(RawLog)
            .order_by(RawLog.observed_at.desc(), RawLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()
