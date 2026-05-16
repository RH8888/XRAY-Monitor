from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.config import Settings
from app.database.models import Event, User
from app.watchlist.types import WatchlistMatch


class TelegramDispatcher(Protocol):
    async def send_admin_message(self, text: str) -> None: ...


class AlertService:
    """Applies alert policy and cooldown tracking before dispatching notifications."""

    def __init__(self, settings: Settings, dispatcher: TelegramDispatcher | None = None) -> None:
        self._settings = settings
        self._dispatcher = dispatcher
        self._last_sent_at: dict[str, datetime] = {}

    def should_alert(self, key: str, usage_percent: float | None = 100.0) -> bool:
        if not self._settings.alerts_enabled or usage_percent is None:
            return False
        if usage_percent < self._settings.alert_usage_threshold_percent:
            return False

        now = datetime.now(UTC)
        last_sent_at = self._last_sent_at.get(key)
        cooldown = timedelta(seconds=self._settings.alert_cooldown_seconds)
        if last_sent_at and now - last_sent_at < cooldown:
            return False

        self._last_sent_at[key] = now
        return True

    async def dispatch_watchlist_hit(
        self,
        *,
        match: WatchlistMatch,
        event: Event,
        dispatcher: TelegramDispatcher | None = None,
    ) -> bool:
        key = f"watchlist:{match.watchlist_id}:{event.domain or event.ip_address or event.id}"
        return await self._dispatch(
            key,
            "XRAY Monitor watchlist match\n"
            f"Watchlist: {match.label}\n"
            f"Reason: {match.reason}\n"
            f"Domain: {event.domain or '-'}\n"
            f"IP: {event.ip_address or '-'}",
            dispatcher=dispatcher,
        )

    async def dispatch_blocked_traffic(
        self,
        *,
        event: Event,
        dispatcher: TelegramDispatcher | None = None,
    ) -> bool:
        key = f"blocked:{event.domain or event.ip_address or event.id}"
        return await self._dispatch(
            key,
            "XRAY Monitor blocked traffic\n"
            f"Domain: {event.domain or '-'}\n"
            f"IP: {event.ip_address or '-'}\n"
            f"Inbound: {event.inbound_tag or '-'}",
            dispatcher=dispatcher,
        )

    async def dispatch_new_user(
        self,
        *,
        user: User,
        dispatcher: TelegramDispatcher | None = None,
    ) -> bool:
        return await self._dispatch(
            f"new-user:{user.client_id}",
            "XRAY Monitor new user observed\n"
            f"Client ID: {user.client_id}\n"
            f"Display name: {user.display_name or '-'}\n"
            f"Email: {user.email or '-'}",
            dispatcher=dispatcher,
        )

    async def dispatch_suspicious_spike(
        self,
        *,
        observed_count: int,
        threshold: int,
        window_seconds: int,
        dispatcher: TelegramDispatcher | None = None,
    ) -> bool:
        return await self._dispatch(
            f"spike:{window_seconds}",
            "XRAY Monitor suspicious activity spike\n"
            f"Events: {observed_count}\n"
            f"Threshold: {threshold}\n"
            f"Window: {window_seconds}s",
            dispatcher=dispatcher,
        )

    async def dispatch_important_destination(
        self,
        *,
        event: Event,
        dispatcher: TelegramDispatcher | None = None,
    ) -> bool:
        destination = event.domain or event.ip_address
        if destination is None:
            return False
        key = f"important:{destination}"
        return await self._dispatch(
            key,
            "XRAY Monitor important destination observed\n"
            f"Destination: {destination}\n"
            f"Event type: {event.event_type}\n"
            f"Inbound: {event.inbound_tag or '-'}",
            dispatcher=dispatcher,
        )

    async def _dispatch(
        self,
        key: str,
        text: str,
        *,
        dispatcher: TelegramDispatcher | None = None,
        usage_percent: float | None = 100.0,
    ) -> bool:
        resolved_dispatcher = dispatcher or self._dispatcher
        if resolved_dispatcher is None or not self.should_alert(key, usage_percent):
            return False
        await resolved_dispatcher.send_admin_message(text)
        return True
