from datetime import UTC, datetime

from app.parser import hash_normalized_line, normalize_log_line, parse_line


def test_normalize_log_line_is_stable_for_deduplication() -> None:
    normalized = normalize_log_line(
        "\ufeff  2025/01/01\t12:00:00\x00 tcp:example.com:443   accepted  "
    )

    assert normalized == "2025/01/01 12:00:00 tcp:example.com:443 accepted"
    assert hash_normalized_line(normalized) == hash_normalized_line(normalized)


def test_parse_accepted_tcp_domain_with_email_and_outbound() -> None:
    parsed = parse_line(
        "2025/01/01 12:00:00 tcp:example.com:443 accepted email:user1 outbound:proxy"
    )

    assert parsed.timestamp == datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert parsed.protocol == "tcp"
    assert parsed.domain == "example.com"
    assert parsed.ip is None
    assert parsed.port == 443
    assert parsed.user_identifier == "user1"
    assert parsed.outbound_tag == "proxy"
    assert parsed.outbound_type == "proxy"
    assert parsed.status == "accepted"
    assert parsed.parser_name == "accepted_destination_user_outbound"


def test_parse_accepted_udp_ip_with_email_and_outbound() -> None:
    parsed = parse_line("2025/01/01 12:00:01 udp:8.8.8.8:53 accepted email:user2 outbound:direct")

    assert parsed.timestamp == datetime(2025, 1, 1, 12, 0, 1, tzinfo=UTC)
    assert parsed.protocol == "udp"
    assert parsed.domain is None
    assert parsed.ip == "8.8.8.8"
    assert parsed.port == 53
    assert parsed.user_identifier == "user2"
    assert parsed.outbound_tag == "direct"
    assert parsed.outbound_type == "direct"
    assert parsed.status == "accepted"


def test_parse_rejected_vless_domain_with_reason() -> None:
    parsed = parse_line("2025/01/01 12:00:02 rejected vless proxy youtube.com reason:no valid user")

    assert parsed.timestamp == datetime(2025, 1, 1, 12, 0, 2, tzinfo=UTC)
    assert parsed.protocol == "vless"
    assert parsed.domain == "youtube.com"
    assert parsed.inbound_tag == "proxy"
    assert parsed.status == "rejected"
    assert parsed.reason == "no valid user"
    assert parsed.event_type == "blocked"


def test_parse_dispatcher_default_route() -> None:
    parsed = parse_line(
        "2025/01/01 12:00:03 [Info] app/dispatcher: default route for tcp:google.com:443"
    )

    assert parsed.timestamp == datetime(2025, 1, 1, 12, 0, 3, tzinfo=UTC)
    assert parsed.protocol == "tcp"
    assert parsed.domain == "google.com"
    assert parsed.port == 443
    assert parsed.status == "routed"
    assert parsed.reason == "default route"
    assert parsed.parser_name == "dispatcher_default_route"


def test_unknown_format_returns_fallback_without_raising() -> None:
    observed_at = datetime(2025, 1, 1, tzinfo=UTC)
    parsed = parse_line("not a known format email:user3 outbound:mystery", observed_at=observed_at)

    assert parsed.timestamp == observed_at
    assert parsed.protocol is None
    assert parsed.domain is None
    assert parsed.ip is None
    assert parsed.port is None
    assert parsed.user_identifier == "user3"
    assert parsed.outbound_tag == "mystery"
    assert parsed.status is None
    assert parsed.raw_line == "not a known format email:user3 outbound:mystery"
    assert parsed.parser_name == "fallback_unknown"
