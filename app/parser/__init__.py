from typing import Any

from app.parser.engine import fallback_parse, parse_known_patterns, parse_line
from app.parser.normalization import hash_normalized_line, normalize_log_line
from app.parser.types import ParsedEvent

__all__ = [
    "ParsedEvent",
    "TrafficSample",
    "fallback_parse",
    "hash_normalized_line",
    "normalize_log_line",
    "parse_known_patterns",
    "parse_line",
]


def __getattr__(name: str) -> Any:
    if name == "TrafficSample":
        from app.parser.models import TrafficSample

        return TrafficSample
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
