from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.bot.service import TelegramBotService
from app.config import Settings
from app.database.models import Event, Watchlist
from app.database.repositories import (
    EventRepository,
    RawLogRepository,
    UserRepository,
    WatchHitRepository,
    WatchlistRepository,
)
from app.poller.client import ThreeXUIClient, ThreeXUIClientError
from app.poller.deduplication import RawLogDeduplicator

logger = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(r"^(?P<date>\d{4}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\b")
_EMAIL_RE = re.compile(r"(?:email|user|client)[:=](?P<value>[\w.+@-]+)", re.IGNORECASE)
_INBOUND_RE = re.compile(r"\[(?P<tag>[^\[\]]+)\]")
_HOST_RE = re.compile(r"\b(?:tcp|udp):(?P<host>\[[^\]]+\]|[^\s:]+)(?::\d+)?\b", re.IGNORECASE)
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_IPV6_RE = re.compile(r"^[0-9a-f:]+$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedLogEntry:
    timestamp: datetime
    event_type: str
    client_id: str | None = None
    domain: str | None = None
    ip_address: str | None = None
    inbound_tag: str | None = None
    details: dict[str, str] | None = None


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

            matched_hits, sent_alerts = await self._match_watchlists(session, event)
            watch_hits += matched_hits
            alerts_sent += sent_alerts

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

    async def _match_watchlists(self, session: AsyncSession, event: Event) -> tuple[int, int]:
        watchlists = await WatchlistRepository(session).list_enabled(limit=1000)
        watch_hits = WatchHitRepository(session)
        matched = 0
        alerts_sent = 0

        for watchlist in watchlists:
            reason = _watchlist_match_reason(watchlist, event)
            if reason is None:
                continue
            await watch_hits.add(
                watchlist_id=watchlist.id,
                event_id=event.id,
                hit_at=datetime.now(UTC),
                reason=reason,
            )
            matched += 1
            if await self._send_watchlist_alert(watchlist, event, reason):
                alerts_sent += 1

        return matched, alerts_sent

    async def _send_watchlist_alert(self, watchlist: Watchlist, event: Event, reason: str) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        alert_key = f"watchlist:{watchlist.id}:{event.domain or event.ip_address or event.id}"
        if not self._alert_service.should_alert(alert_key, 100.0):
            return False
        try:
            await self._bot_service.send_admin_message(
                "XRAY Monitor watchlist match\n"
                f"Watchlist: {watchlist.label}\n"
                f"Reason: {reason}\n"
                f"Domain: {event.domain or '-'}\n"
                f"IP: {event.ip_address or '-'}"
            )
        except Exception:
            logger.exception("failed to send watchlist alert", extra={"event_id": event.id})
            return False
        return True


def parse_xray_log_line(line: str, *, observed_at: datetime) -> ParsedLogEntry:
    timestamp = _parse_timestamp(line) or observed_at
    lower_line = line.lower()
    event_type = "xray_log"
    if "blocked" in lower_line or "reject" in lower_line:
        event_type = "blocked"
    elif "direct" in lower_line:
        event_type = "direct"
    elif "proxy" in lower_line or "accepted" in lower_line:
        event_type = "proxy"

    client_id = _first_match_value(_EMAIL_RE, line)
    inbound_tag = _first_match_value(_INBOUND_RE, line)
    host = _extract_destination_host(line)
    domain: str | None = None
    ip_address: str | None = None
    if host:
        if _is_ip_address(host):
            ip_address = host
        else:
            domain = host.lower()

    return ParsedLogEntry(
        timestamp=timestamp,
        event_type=event_type,
        client_id=client_id,
        domain=domain,
        ip_address=ip_address,
        inbound_tag=inbound_tag,
        details={"line": line},
    )


def _parse_timestamp(line: str) -> datetime | None:
    match = _TIMESTAMP_RE.search(line)
    if not match:
        return None
    try:
        parsed = datetime.strptime(
            f"{match.group('date')} {match.group('time')}", "%Y/%m/%d %H:%M:%S"
        )
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def _first_match_value(pattern: re.Pattern[str], line: str) -> str | None:
    match = pattern.search(line)
    if not match:
        return None
    return match.group("value" if "value" in pattern.groupindex else "tag")


def _extract_destination_host(line: str) -> str | None:
    matches = list(_HOST_RE.finditer(line))
    if not matches:
        return None
    host = matches[-1].group("host").strip("[]")
    return host.rstrip(".,;)") or None


def _is_ip_address(host: str) -> bool:
    return bool(_IPV4_RE.match(host) or (":" in host and _IPV6_RE.match(host)))


def _watchlist_match_reason(watchlist: Watchlist, event: Event) -> str | None:
    if watchlist.domain_pattern and event.domain:
        if fnmatchcase(event.domain.lower(), watchlist.domain_pattern.lower()):
            return f"domain matched {watchlist.domain_pattern}"
    if watchlist.ip_pattern and event.ip_address:
        if fnmatchcase(event.ip_address, watchlist.ip_pattern):
            return f"ip matched {watchlist.ip_pattern}"
    return None
