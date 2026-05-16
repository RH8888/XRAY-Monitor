from datetime import UTC, datetime, timedelta

from app.config import Settings


class AlertService:
    """Applies alert policy and cooldown tracking before dispatching notifications."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._last_sent_at: dict[str, datetime] = {}

    def should_alert(self, key: str, usage_percent: float | None) -> bool:
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
