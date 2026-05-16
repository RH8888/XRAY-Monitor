from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any, TypedDict

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import AlertService
from app.analytics import AnalyticsService
from app.bot.service import TelegramBotService
from app.config import Settings
from app.database.models import Event, User
from app.database.repositories import (
    EventRepository,
    RawLogRepository,
    UserRepository,
)
from app.parser import parse_line
from app.poller.client import ThreeXUIClient, ThreeXUIClientError, XrayLogsPayload
from app.poller.deduplication import RawLogDeduplicator
from app.watchlist.service import WatchlistService
from app.watchlist.types import WatchlistMatch

logger = logging.getLogger(__name__)


class ParsedAddress(TypedDict):
    protocol: str | None
    domain: str | None
    ip_address: str | None
    port: int | None


@dataclass(frozen=True)
class ParsedLogEntry:
    timestamp: datetime
    event_type: str
    client_id: str | None = None
    domain: str | None = None
    ip_address: str | None = None
    inbound_tag: str | None = None
    details: dict[str, Any] | None = None


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

        entries = _entries_from_xray_logs_payload(log_chunk)
        if self._session_factory is None:
            logger.warning("poll fetched logs but no database session factory is configured")
            return PollResult(fetched_lines=len(entries))

        async with self._session_factory() as session:
            try:
                result = await self._process_entries(session, entries)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("poll failed while processing Xray logs")
                return PollResult(fetched_lines=len(entries))

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
        return await self._process_entries(session, lines)

    async def _process_entries(
        self, session: AsyncSession, entries: Sequence[str | dict[str, Any]]
    ) -> PollResult:
        raw_logs = RawLogRepository(session)
        events = EventRepository(session)
        users = UserRepository(session)
        deduplicator = RawLogDeduplicator(session)

        normalized_with_hashes = [
            (entry, normalized, deduplicator.hash(normalized))
            for entry in entries
            if (normalized := _normalize_log_entry(entry, deduplicator))
        ]
        existing_hashes = await deduplicator.existing_hashes(
            line_hash for _, _, line_hash in normalized_with_hashes
        )

        stored_raw_logs = 0
        stored_events = 0
        skipped_duplicates = 0
        parser_failures = 0
        watch_hits = 0
        alerts_sent = 0
        seen_hashes: set[str] = set()
        observed_at = datetime.now(UTC)

        for entry, normalized_line, line_hash in normalized_with_hashes:
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
                parsed = parse_xray_log_entry(entry, observed_at=observed_at)
            except Exception:
                parser_failures += 1
                logger.exception("parser failed for raw log", extra={"raw_log_id": raw_log.id})
                continue

            user_id = None
            user = None
            if parsed.client_id:
                user = await users.get_by_client_id(parsed.client_id)
                if user is None:
                    user = await users.add(client_id=parsed.client_id)
                    if await self._send_new_user_alert(user):
                        alerts_sent += 1
                user_id = user.id

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
            if event.event_type == "blocked" and await self._send_blocked_alert(event):
                alerts_sent += 1
            if self._is_important_destination(
                event
            ) and await self._send_important_destination_alert(event):
                alerts_sent += 1

        if await self._send_spike_alert(session):
            alerts_sent += 1

        if skipped_duplicates:
            logger.info("duplicate raw logs skipped", extra={"count": skipped_duplicates})
        return PollResult(
            fetched_lines=len(entries),
            stored_raw_logs=stored_raw_logs,
            stored_events=stored_events,
            skipped_duplicates=skipped_duplicates,
            parser_failures=parser_failures,
            watch_hits=watch_hits,
            alerts_sent=alerts_sent,
        )

    async def _send_watchlist_alerts(self, event: Event, matches: list[WatchlistMatch]) -> int:
        sent_alerts = 0
        for match in matches:
            if await self._send_watchlist_alert(match, event):
                sent_alerts += 1
        return sent_alerts

    async def _send_watchlist_alert(self, match: WatchlistMatch, event: Event) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        try:
            return await self._alert_service.dispatch_watchlist_hit(
                match=match, event=event, dispatcher=self._bot_service
            )
        except Exception:
            logger.exception("failed to send watchlist alert", extra={"event_id": event.id})
            return False

    async def _send_blocked_alert(self, event: Event) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        try:
            return await self._alert_service.dispatch_blocked_traffic(
                event=event, dispatcher=self._bot_service
            )
        except Exception:
            logger.exception("failed to send blocked traffic alert", extra={"event_id": event.id})
            return False

    async def _send_new_user_alert(self, user: User) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        try:
            return await self._alert_service.dispatch_new_user(
                user=user, dispatcher=self._bot_service
            )
        except Exception:
            logger.exception("failed to send new user alert", extra={"user_id": user.id})
            return False

    async def _send_spike_alert(self, session: AsyncSession) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        threshold = self._settings.alert_spike_threshold_count
        window_seconds = self._settings.alert_spike_window_seconds
        if threshold <= 0 or window_seconds <= 0:
            return False
        since = datetime.now(UTC) - timedelta(seconds=window_seconds)
        observed_count = await AnalyticsService(session).events_since(since)
        if observed_count < threshold:
            return False
        try:
            return await self._alert_service.dispatch_suspicious_spike(
                observed_count=observed_count,
                threshold=threshold,
                window_seconds=window_seconds,
                dispatcher=self._bot_service,
            )
        except Exception:
            logger.exception("failed to send suspicious spike alert")
            return False

    async def _send_important_destination_alert(self, event: Event) -> bool:
        if self._alert_service is None or self._bot_service is None:
            return False
        try:
            return await self._alert_service.dispatch_important_destination(
                event=event, dispatcher=self._bot_service
            )
        except Exception:
            logger.exception(
                "failed to send important destination alert", extra={"event_id": event.id}
            )
            return False

    def _is_important_destination(self, event: Event) -> bool:
        domain = (event.domain or "").lower().rstrip(".")
        ip_address = event.ip_address or ""
        important_domains = {
            value.lower().rstrip(".") for value in self._settings.alert_important_domains
        }
        important_ips = set(self._settings.alert_important_ips)
        return bool(
            (domain and domain in important_domains) or (ip_address and ip_address in important_ips)
        )


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


def parse_xray_log_entry(entry: str | dict[str, Any], *, observed_at: datetime) -> ParsedLogEntry:
    if isinstance(entry, str):
        return parse_xray_log_line(entry, observed_at=observed_at)
    return parse_structured_xray_log_entry(entry, observed_at=observed_at)


def parse_structured_xray_log_entry(
    entry: dict[str, Any], *, observed_at: datetime
) -> ParsedLogEntry:
    timestamp = _parse_structured_timestamp(entry.get("DateTime")) or observed_at
    destination = _parse_address(entry.get("ToAddress"))
    source = _parse_address(entry.get("FromAddress"))
    outbound_tag = _clean_string(entry.get("Outbound"))
    outbound_type = _outbound_type(outbound_tag)
    event_code = entry.get("Event")
    status = _structured_status(event_code)

    return ParsedLogEntry(
        timestamp=timestamp,
        event_type=_structured_event_type(outbound_type=outbound_type, status=status),
        client_id=_clean_string(entry.get("Email")),
        domain=destination["domain"],
        ip_address=destination["ip_address"],
        inbound_tag=_clean_string(entry.get("Inbound")),
        details={
            "line": _serialize_structured_log_entry(entry),
            "protocol": destination["protocol"],
            "port": destination["port"],
            "outbound_tag": outbound_tag,
            "outbound_type": outbound_type,
            "status": status,
            "reason": None,
            "parser_name": "3xui_structured_event",
            "parser_confidence": 1.0,
            "source": "3x-ui:xraylogs",
            "event_code": event_code,
            "source_address": entry.get("FromAddress"),
            "source_ip": source["ip_address"],
            "source_port": source["port"],
            "destination_address": entry.get("ToAddress"),
        },
    )


def _entries_from_xray_logs_payload(payload: XrayLogsPayload) -> list[str | dict[str, Any]]:
    if isinstance(payload, str):
        return list(payload.splitlines())
    return list(payload)


def _normalize_log_entry(entry: str | dict[str, Any], deduplicator: RawLogDeduplicator) -> str:
    if isinstance(entry, str):
        return deduplicator.normalize(entry)
    return deduplicator.normalize(_serialize_structured_log_entry(entry))


def _serialize_structured_log_entry(entry: dict[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_structured_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_address(value: Any) -> ParsedAddress:
    result: ParsedAddress = {
        "protocol": None,
        "domain": None,
        "ip_address": None,
        "port": None,
    }
    if not isinstance(value, str) or not value.strip():
        return result

    address = value.strip()
    protocol = None
    remainder = address
    if ":" in address:
        candidate_protocol, candidate_remainder = address.split(":", 1)
        if candidate_protocol.lower() in {"tcp", "udp"}:
            protocol = candidate_protocol.lower()
            remainder = candidate_remainder

    host, port = _split_host_port(remainder)
    domain, ip = _classify_host(host)
    result.update({"protocol": protocol, "domain": domain, "ip_address": ip, "port": port})
    return result


def _split_host_port(value: str) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    if value.startswith("[") and "]:" in value:
        host, port_text = value[1:].split("]:", 1)
        return _clean_host(host), _parse_port(port_text)
    if ":" not in value:
        return _clean_host(value), None
    host, port_text = value.rsplit(":", 1)
    return _clean_host(host), _parse_port(port_text)


def _clean_host(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip("[]").rstrip(".,;)")
    return cleaned or None


def _parse_port(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        port = int(value)
    except ValueError:
        return None
    if 0 < port <= 65535:
        return port
    return None


def _classify_host(host: str | None) -> tuple[str | None, str | None]:
    if not host:
        return None, None
    try:
        return None, str(ip_address(host))
    except ValueError:
        return host.lower(), None


def _outbound_type(outbound_tag: str | None) -> str | None:
    if outbound_tag is None:
        return None
    return {
        "direct": "direct",
        "freedom": "direct",
        "proxy": "proxy",
        "blocked": "blocked",
        "block": "blocked",
        "blackhole": "blocked",
    }.get(outbound_tag.lower())


def _structured_status(event_code: Any) -> str | None:
    if event_code == 0:
        return "accepted"
    if event_code == 1:
        return "rejected"
    return None


def _structured_event_type(*, outbound_type: str | None, status: str | None) -> str:
    if status == "rejected":
        return "blocked"
    if outbound_type:
        return outbound_type
    if status == "accepted":
        return "proxy"
    return "xray_log"


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
