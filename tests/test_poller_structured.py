import asyncio
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import Settings
from app.database.base import Base
from app.database.models import Event, RawLog, User
from app.poller.client import ThreeXUIClient
from app.poller.service import PollerService, parse_structured_xray_log_entry


class StructuredClient:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.payload = payload

    async def fetch_xray_logs(self, count: int) -> list[dict[str, object]]:
        return self.payload


def make_settings() -> Settings:
    return Settings(
        XRAY_3XUI_BASE_URL="https://panel.example.com",
        XRAY_3XUI_USERNAME="admin",
        XRAY_3XUI_PASSWORD="secret",
        telegram_bot_token="123:abc",
    )


def test_parse_structured_xray_log_entry_extracts_normalized_fields() -> None:
    parsed = parse_structured_xray_log_entry(
        {
            "DateTime": "2026-05-16T16:03:56.444907Z",
            "FromAddress": "20.76.219.231:19595",
            "ToAddress": "tcp:www.google.com:443",
            "Inbound": "inbound-80",
            "Outbound": "direct",
            "Email": "Hooman",
            "Event": 0,
        },
        observed_at=datetime(2026, 5, 16, tzinfo=UTC),
    )

    assert parsed.timestamp == datetime(2026, 5, 16, 16, 3, 56, 444907, tzinfo=UTC)
    assert parsed.event_type == "direct"
    assert parsed.client_id == "Hooman"
    assert parsed.domain == "www.google.com"
    assert parsed.ip_address is None
    assert parsed.inbound_tag == "inbound-80"
    assert parsed.details is not None
    assert parsed.details["protocol"] == "tcp"
    assert parsed.details["port"] == 443
    assert parsed.details["source_ip"] == "20.76.219.231"
    assert parsed.details["source_port"] == 19595
    assert parsed.details["parser_name"] == "3xui_structured_event"


def test_poller_persists_structured_xray_log_events() -> None:
    async def run_test() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        payload = [
            {
                "DateTime": "2026-05-16T16:03:56.444907Z",
                "FromAddress": "20.76.219.231:19595",
                "ToAddress": "tcp:www.google.com:443",
                "Inbound": "inbound-80",
                "Outbound": "direct",
                "Email": "Hooman",
                "Event": 0,
            },
            {
                "DateTime": "2026-05-16T16:03:57.285583Z",
                "FromAddress": "20.76.219.231:1852",
                "ToAddress": "tcp:8.8.8.8:443",
                "Inbound": "inbound-80",
                "Outbound": "blackhole",
                "Email": "Allah",
                "Event": 1,
            },
        ]
        poller = PollerService(
            make_settings(), cast(ThreeXUIClient, StructuredClient(payload)), session_factory
        )

        result = await poller.poll_once()

        assert result.fetched_lines == 2
        assert result.stored_raw_logs == 2
        assert result.stored_events == 2
        assert result.parser_failures == 0

        async with session_factory() as session:
            raw_logs = (await session.execute(select(RawLog).order_by(RawLog.id))).scalars().all()
            events = (await session.execute(select(Event).order_by(Event.id))).scalars().all()
            users = (await session.execute(select(User).order_by(User.id))).scalars().all()

        assert len(raw_logs) == 2
        assert '"Email":"Hooman"' in raw_logs[0].line
        assert [user.client_id for user in users] == ["Hooman", "Allah"]
        assert events[0].event_type == "direct"
        assert events[0].domain == "www.google.com"
        assert events[0].details is not None
        assert events[0].details["destination_address"] == "tcp:www.google.com:443"
        assert events[1].event_type == "blocked"
        assert events[1].ip_address == "8.8.8.8"
        await engine.dispose()

    asyncio.run(run_test())
