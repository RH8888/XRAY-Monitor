from __future__ import annotations

import hashlib
import re

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_log_line(line: str) -> str:
    """Return a stable canonical form for raw-log deduplication.

    The normalizer removes non-printing control characters, trims leading/trailing whitespace, and
    collapses whitespace runs without changing case or removing timestamps. Keeping timestamps in
    place makes duplicate detection deterministic and based on the complete normalized raw line.
    """

    without_controls = _CONTROL_CHARS_RE.sub("", line.replace("\ufeff", ""))
    return _WHITESPACE_RE.sub(" ", without_controls.strip())


def hash_normalized_line(normalized_line: str) -> str:
    """Return the SHA-256 duplicate key for a normalized raw log line."""

    return hashlib.sha256(normalized_line.encode("utf-8")).hexdigest()
