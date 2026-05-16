from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from ipaddress import ip_address

from app.parser.normalization import normalize_log_line
from app.parser.patterns import KNOWN_LOG_PATTERNS, LogPattern
from app.parser.types import ParsedEvent

Parser = Callable[[str], ParsedEvent | None]
_TIMESTAMP_FORMAT = "%Y/%m/%d %H:%M:%S"
_OUTBOUND_TYPE_ALIASES = {
    "direct": "direct",
    "freedom": "direct",
    "proxy": "proxy",
    "blocked": "blocked",
    "block": "blocked",
    "blackhole": "blocked",
}
_GENERIC_IDENTIFIER_RE = re.compile(
    r"\b(?:email|user|client)[:=](?P<value>[\w.+@-]+)", re.IGNORECASE
)
_GENERIC_OUTBOUND_RE = re.compile(r"\boutbound[:=](?P<value>[\w.@:+-]+)\b", re.IGNORECASE)
_GENERIC_INBOUND_RE = re.compile(r"\binbound[:=](?P<value>[\w.@:+-]+)\b", re.IGNORECASE)


def parse_line(
    line: str,
    *,
    observed_at: datetime | None = None,
    source: str | None = None,
    parsers: Sequence[Parser] | None = None,
) -> ParsedEvent:
    """Parse one raw log line without raising on malformed input.

    Registered parsers are tried in order. If no parser recognizes the line, a fallback event is
    returned so the raw line can still be stored and linked to a mostly-null structured event.
    """

    normalized_line = normalize_log_line(line)
    selected_parsers = parsers or REGISTERED_PARSERS
    for parser in selected_parsers:
        try:
            parsed = parser(normalized_line)
        except Exception:
            parsed = None
        if parsed is not None:
            return _with_source(parsed, source)
    return fallback_parse(normalized_line, observed_at=observed_at, source=source)


def parse_known_patterns(line: str) -> ParsedEvent | None:
    """Parse the line using the named regex patterns in ``patterns.py``."""

    normalized_line = normalize_log_line(line)
    for pattern in KNOWN_LOG_PATTERNS:
        parsed = _parse_pattern(normalized_line, pattern)
        if parsed is not None:
            return parsed
    return None


def fallback_parse(
    line: str,
    *,
    observed_at: datetime | None = None,
    source: str | None = None,
) -> ParsedEvent:
    """Return a safe parser result for unrecognized or malformed log formats."""

    normalized_line = normalize_log_line(line)
    return ParsedEvent(
        timestamp=observed_at,
        protocol=None,
        domain=None,
        ip=None,
        port=None,
        user_identifier=_first_group(_GENERIC_IDENTIFIER_RE, normalized_line),
        inbound_tag=_first_group(_GENERIC_INBOUND_RE, normalized_line),
        outbound_tag=_first_group(_GENERIC_OUTBOUND_RE, normalized_line),
        outbound_type=None,
        status=None,
        reason=None,
        raw_line=normalized_line,
        parser_name="fallback_unknown",
        confidence=0.0,
        source=source,
    )


def _parse_pattern(line: str, pattern: LogPattern) -> ParsedEvent | None:
    match = pattern.regex.search(line)
    if not match:
        return None

    values = match.groupdict()
    status = _lower_or_none(values.get("status"))
    if pattern.name == "dispatcher_default_route":
        status = "routed"

    host = _strip_host(values.get("host"))
    domain, ip = _classify_host(host)
    outbound_tag = values.get("outbound_tag")

    return ParsedEvent(
        timestamp=_parse_timestamp(values.get("timestamp")),
        protocol=_lower_or_none(values.get("protocol")),
        domain=domain,
        ip=ip,
        port=_parse_port(values.get("port")),
        user_identifier=values.get("user_identifier"),
        inbound_tag=values.get("inbound_tag"),
        outbound_tag=outbound_tag,
        outbound_type=_outbound_type(outbound_tag),
        status=status,
        reason=_clean_reason(values.get("reason")),
        raw_line=line,
        parser_name=pattern.name,
        confidence=pattern.confidence,
    )


def _with_source(parsed: ParsedEvent, source: str | None) -> ParsedEvent:
    if parsed.source == source:
        return parsed
    return ParsedEvent(
        timestamp=parsed.timestamp,
        protocol=parsed.protocol,
        domain=parsed.domain,
        ip=parsed.ip,
        port=parsed.port,
        user_identifier=parsed.user_identifier,
        inbound_tag=parsed.inbound_tag,
        outbound_tag=parsed.outbound_tag,
        outbound_type=parsed.outbound_type,
        status=parsed.status,
        reason=parsed.reason,
        raw_line=parsed.raw_line,
        parser_name=parsed.parser_name,
        confidence=parsed.confidence,
        source=source,
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


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


def _strip_host(host: str | None) -> str | None:
    if host is None:
        return None
    cleaned = host.strip().strip("[]").rstrip(".,;)")
    return cleaned or None


def _outbound_type(outbound_tag: str | None) -> str | None:
    if outbound_tag is None:
        return None
    return _OUTBOUND_TYPE_ALIASES.get(outbound_tag.lower())


def _clean_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    cleaned = reason.strip()
    return cleaned or None


def _lower_or_none(value: str | None) -> str | None:
    return value.lower() if value else None


def _first_group(pattern: re.Pattern[str], line: str) -> str | None:
    match = pattern.search(line)
    if not match:
        return None
    return match.group("value")


REGISTERED_PARSERS: tuple[Parser, ...] = (parse_known_patterns,)
