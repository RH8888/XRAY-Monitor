from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.bot.service import TelegramBotService
from app.config import Settings
from app.database.models import Event
from app.database.repositories import (
    EventRepository,
    RawLogRepository,
    UserRepository,
)
from app.parser import parse_line
from app.poller.client import ThreeXUIClient, ThreeXUIClientError
from app.poller.deduplication import RawLogDeduplicator
from app.watchlist.service import WatchlistService
from app.watchlist.types import WatchlistMatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedLogEntry:
    timestamp: datetime
    event_type: str
    client_id: str | None = None
    domain: str | None = None
    ip_address: str | None = None
    inbound_tag: str | None = None
    details: dict[str, str | int | float | None] | None = None


@dataclass(frozen=True)
class PollResult:
    fetched_lines: int = 0
    stored_raw_logs: int = 0
    stored_events: int = 0
    skipped_duplicates: int = 0
    parser_failures: int = 0
    watch_hits: int = 0
    alerts_sent: int = 0


class PollerService:
    """Fetch, deduplicate, persist, parse, match, and alert on Xray log entries."""

    def __init__(
        self,
        settings: Settings,
        client: ThreeXUIClient,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        alert_service: AlertService | None = None,
        bot_service: TelegramBotService | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._session_factory = session_factory
        self._alert_service = alert_service
        self._bot_service = bot_service

    async def poll_once(self) -> PollResult:
        """Run a single poll cycle without allowing one bad line to stop the service."""

        try:
            log_chunk = await self._client.fetch_xray_logs(self._settings.log_count)
        except (ThreeXUIClientError, httpx.HTTPError):
            logger.warning("poll failed while fetching Xray logs", exc_info=True)
            return PollResult()

        lines = log_chunk.splitlines()
        if self._session_factory is None:
            logger.warning("poll fetched logs but no database session factory is configured")
            return PollResult(fetched_lines=len(lines))

        async with self._session_factory() as session:
            try:
                result = await self._process_lines(session, lines)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("poll failed while processing Xray logs")
                return PollResult(fetched_lines=len(lines))

        logger.info(
            "poll completed successfully",
            extra={
                "fetched_lines": result.fetched_lines,
                "stored_raw_logs": result.stored_raw_logs,
                "stored_events": result.stored_events,
                "skipped_duplicates": result.skipped_duplicates,
                "parser_failures": result.parser_failures,
                "watch_hits": result.watch_hits,
                "alerts_sent": result.alerts_sent,
            },
        )
        return result

    async def _process_lines(self, session: AsyncSession, lines: list[str]) -> PollResult:
        raw_logs = RawLogRepository(session)
        events = EventRepository(session)
        users = UserRepository(session)
        deduplicator = RawLogDeduplicator(session)

        normalized_with_hashes = [
            (normalized, deduplicator.hash(normalized))
            for line in lines
            if (normalized := deduplicator.normalize(line))
        ]
        existing_hashes = await deduplicator.existing_hashes(
            line_hash for _, line_hash in normalized_with_hashes
        )

        stored_raw_logs = 0
        stored_events = 0
        skipped_duplicates = 0
        parser_failures = 0
        watch_hits = 0
        alerts_sent = 0
        seen_hashes: set[str] = set()
        observed_at = datetime.now(UTC)

        for normalized_line, line_hash in normalized_with_hashes:
            if line_hash in existing_hashes or line_hash in seen_hashes:
                skipped_duplicates += 1
                logger.info("skipped duplicate raw log", extra={"line_hash": line_hash})
                continue
            seen_hashes.add(line_hash)

            raw_log = await raw_logs.add(
                line=normalized_line,
                line_hash=line_hash,
                observed_at=observed_at,
                source="3x-ui:xraylogs",
            )
            stored_raw_logs += 1

            try:
                parsed = parse_xray_log_line(normalized_line, observed_at=observed_at)
            except Exception:
                parser_failures += 1
                logger.exception("parser failed for raw log", extra={"raw_log_id": raw_log.id})
                continue

            user_id = None
            if parsed.client_id:
                user = await users.get_by_client_id(parsed.client_id)
                user_id = user.id if user else None

            event = await events.add(
                timestamp=parsed.timestamp,
                event_type=parsed.event_type,
                user_id=user_id,
                raw_log_id=raw_log.id,
                domain=parsed.domain,
                ip_address=parsed.ip_address,
                inbound_tag=parsed.inbound_tag,
                details=parsed.details,
            )
            stored_events += 1

            matches = await WatchlistService(session).evaluate_event(event)
            watch_hits += len(matches)
            alerts_sent += await self._send_watchlist_alerts(event, matches)

        if skipped_duplicates:
            logger.info("duplicate raw logs skipped", extra={"count": skipped_duplicates})
        return PollResult(
            fetched_lines=len(lines),
            stored_raw_logs=stored_raw_logs,
            stored_events=stored_events,
            skipped_duplicates=skipped_duplicates,
            parser_failures=parser_failures,
            watch_hits=watch_hits,
            alerts_sent=alerts_sent,
        )

    async def _send_watchlist_alerts(
        self, event: Event, matches: list[WatchlistMatch]
    ) -> int:
        sent_alerts = 0
        for match in matches:
            if await self._send_watchlist_alert(match, event):
                sent_alerts += 1
        return sent_alerts

    async def _send_watchlist_alert(self, match: WatchlistMatch, event: Event) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        alert_key = f"watchlist:{match.watchlist_id}:{event.domain or event.ip_address or event.id}"
        if not self._alert_service.should_alert(alert_key, 100.0):
            return False
        try:
            await self._bot_service.send_admin_message(
                "XRAY Monitor watchlist match\n"
                f"Watchlist: {match.label}\n"
                f"Reason: {match.reason}\n"
                f"Domain: {event.domain or '-'}\n"
                f"IP: {event.ip_address or '-'}"
            )
        except Exception:
            logger.exception("failed to send watchlist alert", extra={"event_id": event.id})
            return False
        return True


def parse_xray_log_line(line: str, *, observed_at: datetime) -> ParsedLogEntry:
    parsed = parse_line(line, observed_at=observed_at, source="3x-ui:xraylogs")
    return ParsedLogEntry(
        timestamp=parsed.timestamp or observed_at,
        event_type=parsed.event_type,
        client_id=parsed.user_identifier,
        domain=parsed.domain,
        ip_address=parsed.ip,
        inbound_tag=parsed.inbound_tag,
        details={
            "line": parsed.raw_line,
            "protocol": parsed.protocol,
            "port": parsed.port,
            "outbound_tag": parsed.outbound_tag,
            "outbound_type": parsed.outbound_type,
            "status": parsed.status,
            "reason": parsed.reason,
            "parser_name": parsed.parser_name,
            "parser_confidence": parsed.confidence,
            "source": parsed.source,
        },
    )
