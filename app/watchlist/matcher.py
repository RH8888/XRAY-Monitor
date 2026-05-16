from __future__ import annotations

from ipaddress import ip_address, ip_network


def normalize_domain(value: str | None) -> str | None:
    """Normalize and validate a domain name for watchlist comparisons."""

    if value is None:
        return None

    domain = value.strip().lower().rstrip(".")
    if not domain or len(domain) > 253:
        return None
    if "://" in domain or "/" in domain or any(char.isspace() for char in domain):
        return None

    labels = domain.split(".")
    if any(not _is_valid_domain_label(label) for label in labels):
        return None
    return domain


def matches_exact_domain(candidate: str | None, target: str) -> bool:
    """Return true when a candidate domain equals a normalized target domain."""

    normalized_candidate = normalize_domain(candidate)
    normalized_target = normalize_domain(target)
    return normalized_candidate is not None and normalized_candidate == normalized_target


def matches_wildcard_domain(candidate: str | None, wildcard_target: str) -> bool:
    """Return true when a candidate domain is under a wildcard suffix like *.example.com."""

    normalized_candidate = normalize_domain(candidate)
    wildcard = wildcard_target.strip().lower().rstrip(".")
    if not wildcard.startswith("*."):
        return False

    suffix = normalize_domain(wildcard[2:])
    if normalized_candidate is None or suffix is None:
        return False

    return normalized_candidate.endswith(f".{suffix}")


def matches_exact_ip(candidate: str | None, target: str) -> bool:
    """Return true when a candidate IP address exactly equals a target IP address."""

    try:
        return ip_address(_strip_optional_brackets(candidate)) == ip_address(target.strip())
    except ValueError:
        return False


def matches_cidr(candidate: str | None, cidr_target: str) -> bool:
    """Return true when a candidate IP address belongs to a CIDR target network."""

    try:
        return ip_address(_strip_optional_brackets(candidate)) in ip_network(cidr_target.strip())
    except ValueError:
        return False


def _is_valid_domain_label(label: str) -> bool:
    if not label or len(label) > 63:
        return False
    if label.startswith("-") or label.endswith("-"):
        return False
    return all(char.isalnum() or char == "-" for char in label)


def _strip_optional_brackets(value: str | None) -> str:
    if value is None:
        raise ValueError("missing IP address")
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1]
    return stripped
