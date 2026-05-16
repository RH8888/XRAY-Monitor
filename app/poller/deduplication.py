from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import RawLogRepository

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_log_line(line: str) -> str:
    """Return the canonical representation used for duplicate detection.

    Normalization intentionally does not remove or reinterpret timestamps; duplicate detection must
    be based on the normalized full line hash, never timestamp ordering or timestamp windows.
    """

    return _WHITESPACE_RE.sub(" ", line.replace("\x00", "").strip())


def hash_normalized_line(normalized_line: str) -> str:
    """Generate the primary SHA256 duplicate key for a normalized log line."""

    return hashlib.sha256(normalized_line.encode("utf-8")).hexdigest()


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
