import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import Event, WatchHit
from app.database.repositories.events import EventRepository
from app.database.repositories.users import UserRepository
from app.database.repositories.watchlists import WatchlistRepository
from app.watchlist.matcher import (
    matches_cidr,
    matches_exact_domain,
    matches_exact_ip,
    matches_wildcard_domain,
)
from app.watchlist.service import WatchlistService
from app.watchlist.types import WatchTargetType


def test_domain_matchers_normalize_and_match_expected_targets() -> None:
    assert matches_exact_domain("Example.COM.", "example.com")
    assert not matches_exact_domain("www.example.com", "example.com")
    assert matches_wildcard_domain("video.googlevideo.com", "*.googlevideo.com")
    assert matches_wildcard_domain("a.b.googlevideo.com", "*.googlevideo.com")
    assert not matches_wildcard_domain("googlevideo.com", "*.googlevideo.com")
    assert not matches_wildcard_domain("example com", "*.example.com")


def test_ip_matchers_handle_exact_cidr_and_invalid_values() -> None:
    assert matches_exact_ip("8.8.8.8", "8.8.8.8")
    assert matches_exact_ip("[2001:4860:4860::8888]", "2001:4860:4860::8888")
    assert not matches_exact_ip("8.8.4.4", "8.8.8.8")
    assert matches_cidr("192.0.2.5", "192.0.2.0/24")
    assert not matches_cidr("192.0.3.5", "192.0.2.0/24")
    assert not matches_cidr("192.0.2.5", "bad-cidr")


def test_watchlist_service_creates_hits_for_matching_enabled_targets() -> None:
    async def run_test() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            user = await UserRepository(session).add(client_id="client-1")
            watchlists = WatchlistRepository(session)
            await watchlists.add(
                user_id=user.id,
                label="googlevideo",
                domain_pattern="*.googlevideo.com",
            )
            await watchlists.add(
                user_id=user.id,
                label="dns",
                ip_pattern="8.8.8.8",
            )
            await watchlists.add(
                user_id=user.id,
                label="disabled",
                domain_pattern="video.googlevideo.com",
                is_enabled=False,
            )
            await watchlists.add(
                user_id=user.id,
                label="bad cidr",
                ip_pattern="10.0.0.0/99",
            )
            event = await EventRepository(session).add(
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                event_type="routed",
                domain="VIDEO.GoogleVideo.com",
                ip_address="8.8.8.8",
            )

            matches = await WatchlistService(session).evaluate_event(event)
            await session.commit()

        assert [match.label for match in matches] == ["dns", "googlevideo"]
        assert {match.target_type for match in matches} == {
            WatchTargetType.EXACT_IP,
            WatchTargetType.WILDCARD_DOMAIN,
        }

        async with session_factory() as session:
            hits = (await session.execute(WatchHit.__table__.select())).all()

        assert len(hits) == 2
        await engine.dispose()

    asyncio.run(run_test())


def test_watchlist_service_ignores_malformed_values_without_crashing() -> None:
    async def run_test() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            user = await UserRepository(session).add(client_id="client-1")
            await WatchlistRepository(session).add(
                user_id=user.id,
                label="malformed",
                domain_pattern="*.bad domain",
                ip_pattern="not an ip",
            )
            event = Event(
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
                event_type="routed",
                domain="safe.example",
                ip_address="203.0.113.10",
            )

            matches = await WatchlistService(session).evaluate_event(event)

        assert matches == []
        await engine.dispose()

    asyncio.run(run_test())
