from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import RawLogRepository
from app.parser.normalization import hash_normalized_line, normalize_log_line


class RawLogDeduplicator:
    """Database-backed duplicate detector using ``raw_logs.line_hash``."""

    def __init__(self, session: AsyncSession) -> None:
        self._raw_logs = RawLogRepository(session)

    def normalize(self, line: str) -> str:
        return normalize_log_line(line)

    def hash(self, normalized_line: str) -> str:
        return hash_normalized_line(normalized_line)

    async def existing_hashes(self, line_hashes: Iterable[str]) -> set[str]:
        return await self._raw_logs.existing_hashes(line_hashes)

    async def is_duplicate(self, line_hash: str) -> bool:
        return await self._raw_logs.exists_by_hash(line_hash)
