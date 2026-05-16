from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics import AnalyticsService
from app.config import Settings
from app.watchlist.service import WatchlistService

logger = logging.getLogger(__name__)
T = TypeVar("T")


class AdminOnlyMiddleware(BaseMiddleware):
    """Allow Telegram bot commands only from configured admin user IDs."""

    def __init__(self, admin_ids: tuple[int, ...]) -> None:
        self._admin_ids = set(admin_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not self._admin_ids or user is None or user.id not in self._admin_ids:
            if isinstance(event, Message):
                await event.answer("Unauthorized.")
            return None
        return await handler(event, data)


class TelegramBotService:
    """Owns Telegram bot lifecycle and admin-only analytics/watchlist handlers."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dispatcher = Dispatcher()
        self._register_handlers()

    async def send_admin_message(self, text: str) -> None:
        for admin_id in self._settings.telegram_admin_ids:
            await self.bot.send_message(admin_id, text)

    async def start_polling(self) -> None:
        await self.dispatcher.start_polling(self.bot)

    async def close(self) -> None:
        await self.bot.session.close()

    def _register_handlers(self) -> None:
        router = Router(name="admin")
        router.message.middleware(AdminOnlyMiddleware(self._settings.telegram_admin_ids))

        router.message.register(self._users, Command("users"))
        router.message.register(self._user, Command("user"))
        router.message.register(self._top_domains, Command("topdomains"))
        router.message.register(self._top_ips, Command("topips"))
        router.message.register(self._recent, Command("recent"))
        router.message.register(self._watchlist, Command("watchlist"))
        router.message.register(self._watch_hits, Command("watchhits"))
        router.message.register(self._add_watch, Command("addwatch"))
        router.message.register(self._remove_watch, Command("removewatch"))
        router.message.register(self._list_watch, Command("listwatch"))
        router.message.register(self._stats, Command("stats"))

        self.dispatcher.include_router(router)

    async def _users(self, message: Message) -> None:
        async with self._analytics() as analytics:
            users = await analytics.active_users(limit=25)
        if not users:
            await message.answer("No active users found.")
            return
        await message.answer(
            "Active users\n" + "\n".join(_format_user_total(user) for user in users)
        )

    async def _user(self, message: Message) -> None:
        name = _command_args(message)
        if not name:
            await message.answer("Usage: /user <client_id|display_name|email>")
            return
        async with self._analytics() as analytics:
            user = await analytics.user_totals(name)
            recent = await analytics.recent_activity(limit=5)
        if user is None:
            await message.answer(f"No user found for {name!r}.")
            return
        await message.answer(
            _format_user_total(user, heading="User totals")
            + "\n\nRecent global activity\n"
            + _format_recent(recent)
        )

    async def _top_domains(self, message: Message) -> None:
        async with self._analytics() as analytics:
            rows = await analytics.top_domains(limit=10)
        await message.answer(_format_destinations("Top domains", rows))

    async def _top_ips(self, message: Message) -> None:
        async with self._analytics() as analytics:
            rows = await analytics.top_ips(limit=10)
        await message.answer(_format_destinations("Top IPs", rows))

    async def _recent(self, message: Message) -> None:
        async with self._analytics() as analytics:
            rows = await analytics.recent_activity(limit=10)
        await message.answer("Recent activity\n" + _format_recent(rows))

    async def _watchlist(self, message: Message) -> None:
        await self._list_watch(message)

    async def _watch_hits(self, message: Message) -> None:
        async with self._analytics() as analytics:
            rows = await analytics.watchlist_hit_counts(limit=20)
        if not rows:
            await message.answer("No watchlist hit data found.")
            return
        await message.answer(
            "Watchlist hits\n"
            + "\n".join(f"{row.label}: {row.hits} ({row.percentage:.2f}%)" for row in rows)
        )

    async def _add_watch(self, message: Message) -> None:
        args = _command_args(message).split()
        if len(args) < 3:
            await message.answer(
                "Usage: /addwatch <client_id> <label> <domain|ip|cidr|bytes:N>\n"
                "Examples: /addwatch alice social *.facebook.com | /addwatch alice host 8.8.8.8"
            )
            return
        client_id, label, target = args[0], args[1], args[2]
        domain_pattern, ip_pattern, limit_bytes = _parse_watch_target(target)
        try:
            async with self._watchlists(commit=True) as watchlists:
                entry = await watchlists.add_entry(
                    client_id=client_id,
                    label=label,
                    domain_pattern=domain_pattern,
                    ip_pattern=ip_pattern,
                    limit_bytes=limit_bytes,
                )
        except IntegrityError:
            await message.answer("A watchlist entry with that label already exists for this user.")
            return
        await message.answer(
            f"Added watchlist entry #{entry.id} for {client_id}: {label} -> {target}"
        )

    async def _remove_watch(self, message: Message) -> None:
        args = _command_args(message).split()
        if len(args) != 2:
            await message.answer("Usage: /removewatch <client_id> <label>")
            return
        async with self._watchlists(commit=True) as watchlists:
            removed = await watchlists.remove_entry(client_id=args[0], label=args[1])
        await message.answer(
            "Removed watchlist entry." if removed else "No matching watchlist entry found."
        )

    async def _list_watch(self, message: Message) -> None:
        async with self._watchlists() as watchlists:
            entries = await watchlists.list_entries(limit=50)
        if not entries:
            await message.answer("No watchlist entries found.")
            return
        lines = []
        for entry in entries:
            target = entry.domain_pattern or entry.ip_pattern or f"bytes:{entry.limit_bytes}"
            status = "enabled" if entry.is_enabled else "disabled"
            lines.append(f"#{entry.id} user={entry.user_id} {entry.label}: {target} ({status})")
        await message.answer("Watchlist\n" + "\n".join(lines))

    async def _stats(self, message: Message) -> None:
        async with self._analytics() as analytics:
            users = await analytics.per_user_totals(limit=5)
            domains = await analytics.unique_domains()
            ips = await analytics.unique_ips()
            seen = await analytics.first_last_seen()
            destinations = await analytics.destination_frequency_percentages(limit=5)
            hourly = await analytics.hourly_activity_distribution()
        peak = max(hourly, key=lambda row: row.count, default=None)
        lines = [
            "XRAY Monitor stats",
            f"Unique domains: {domains}",
            f"Unique IPs: {ips}",
            f"First seen: {_format_dt(seen.first_seen)}",
            f"Last seen: {_format_dt(seen.last_seen)}",
        ]
        if peak:
            lines.append(f"Peak hour: {peak.hour:02d}:00 ({peak.count}, {peak.percentage:.2f}%)")
        lines.append("Top users:")
        lines.extend(_format_user_total(user) for user in users)
        lines.append("Destinations:")
        lines.extend(
            f"{row.destination}: {row.count} ({row.percentage:.2f}%)" for row in destinations
        )
        await message.answer("\n".join(lines))

    def _ensure_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("TelegramBotService requires a database session factory")
        return self._session_factory

    def _analytics(self) -> _ServiceContext[AnalyticsService]:
        return _ServiceContext(self._ensure_session_factory(), AnalyticsService)

    def _watchlists(self, *, commit: bool = False) -> _ServiceContext[WatchlistService]:
        return _ServiceContext(self._ensure_session_factory(), WatchlistService, commit=commit)


class _ServiceContext(Generic[T]):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service_factory: Callable[[AsyncSession], T],
        *,
        commit: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._service_factory = service_factory
        self._commit = commit
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> T:
        self._session = self._session_factory()
        return self._service_factory(self._session)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is None:
            return
        if exc_type is None and self._commit:
            await self._session.commit()
        elif exc_type is not None:
            await self._session.rollback()
        await self._session.close()


def _command_args(message: Message) -> str:
    if not message.text:
        return ""
    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _parse_watch_target(target: str) -> tuple[str | None, str | None, int | None]:
    if target.startswith("bytes:"):
        return None, None, int(target.removeprefix("bytes:"))
    if any(char.isalpha() for char in target) or "*" in target:
        return target, None, None
    return None, target, None


def _format_user_total(user: Any, *, heading: str | None = None) -> str:
    label = user.display_name or user.client_id or "Unknown"
    line = (
        f"{label}: events={user.event_count}, domains={user.unique_domains}, "
        f"ips={user.unique_ips}, first={_format_dt(user.first_seen)}, "
        f"last={_format_dt(user.last_seen)}"
    )
    return f"{heading}\n{line}" if heading else line


def _format_destinations(title: str, rows: list[Any]) -> str:
    if not rows:
        return f"{title}\nNo data found."
    return (
        title
        + "\n"
        + "\n".join(f"{row.destination}: {row.count} ({row.percentage:.2f}%)" for row in rows)
    )


def _format_recent(rows: list[Any]) -> str:
    if not rows:
        return "No recent activity found."
    return "\n".join(
        f"{_format_dt(row.timestamp)} {row.client_id or row.display_name or 'unknown'} "
        f"{row.event_type} domain={row.domain or '-'} ip={row.ip_address or '-'}"
        for row in rows
    )


def _format_dt(value: Any) -> str:
    return value.isoformat(timespec="seconds") if value else "-"
