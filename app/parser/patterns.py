from __future__ import annotations

import re
from dataclasses import dataclass

TIMESTAMP_PATTERN = r"(?P<timestamp>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})"
HOST_PATTERN = r"(?P<host>\[[^\]]+\]|[^\s:]+)"
PORT_PATTERN = r"(?P<port>\d{1,5})"
IDENTIFIER_PATTERN = r"(?P<user_identifier>[\w.+@-]+)"
TAG_PATTERN = r"(?P<outbound_tag>[\w.@:+-]+)"


@dataclass(frozen=True, slots=True)
class LogPattern:
    """A named regular expression for one known Xray/3x-ui log format."""

    name: str
    regex: re.Pattern[str]
    confidence: float


KNOWN_LOG_PATTERNS: tuple[LogPattern, ...] = (
    LogPattern(
        name="accepted_destination_user_outbound",
        regex=re.compile(
            rf"^{TIMESTAMP_PATTERN}\s+"
            rf"(?P<protocol>tcp|udp):{HOST_PATTERN}:{PORT_PATTERN}\s+"
            rf"(?P<status>accepted)\s+"
            rf"(?:email|user|client)[:=]{IDENTIFIER_PATTERN}\s+"
            rf"outbound[:=]{TAG_PATTERN}\b",
            re.IGNORECASE,
        ),
        confidence=0.98,
    ),
    LogPattern(
        name="rejected_protocol_inbound_domain_reason",
        regex=re.compile(
            rf"^{TIMESTAMP_PATTERN}\s+"
            rf"(?P<status>rejected)\s+"
            rf"(?P<protocol>[\w.+-]+)\s+"
            rf"(?P<inbound_tag>[\w.@:+-]+)\s+"
            rf"(?P<host>\[[^\]]+\]|[^\s:]+)"
            rf"(?:\s+reason[:=](?P<reason>.+))?$",
            re.IGNORECASE,
        ),
        confidence=0.95,
    ),
    LogPattern(
        name="dispatcher_default_route",
        regex=re.compile(
            rf"^{TIMESTAMP_PATTERN}\s+"
            rf"\[(?P<level>Info|Debug|Warning|Error)\]\s+"
            rf"app/dispatcher:\s+"
            rf"(?P<reason>default route)\s+for\s+"
            rf"(?P<protocol>tcp|udp):{HOST_PATTERN}:{PORT_PATTERN}\b",
            re.IGNORECASE,
        ),
        confidence=0.9,
    ),
)
