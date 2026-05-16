from functools import lru_cache

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="XRAY_",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    three_xui_base_url: AnyHttpUrl = Field(alias="XRAY_3XUI_BASE_URL")
    three_xui_username: str = Field(alias="XRAY_3XUI_USERNAME")
    three_xui_password: str = Field(alias="XRAY_3XUI_PASSWORD")
    three_xui_token: str | None = Field(default=None, alias="XRAY_3XUI_TOKEN")

    poll_interval_seconds: int = 60
    log_count: int = 200

    database_url: str = "sqlite+aiosqlite:///./data/xray-monitor.db"

    telegram_bot_token: str
    telegram_admin_ids: tuple[int, ...] = ()

    alerts_enabled: bool = True
    alert_usage_threshold_percent: int = 80
    alert_cooldown_seconds: int = 3600
    alert_send_startup_message: bool = False
    alert_spike_threshold_count: int = 100
    alert_spike_window_seconds: int = 300
    alert_important_domains: tuple[str, ...] = ()
    alert_important_ips: tuple[str, ...] = ()

    log_level: str = "INFO"

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: str | list[int] | tuple[int, ...]) -> tuple[int, ...]:
        if isinstance(value, str):
            if not value.strip():
                return ()
            return tuple(int(part.strip()) for part in value.split(",") if part.strip())
        return tuple(value)

    @field_validator("alert_important_domains", "alert_important_ips", mode="before")
    @classmethod
    def parse_csv_tuple(cls, value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(value, str):
            if not value.strip():
                return ()
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
