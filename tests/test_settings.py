from app.config.settings import Settings


def test_settings_parse_admin_ids_from_csv() -> None:
    settings = Settings(
        XRAY_3XUI_BASE_URL="https://panel.example.com",
        XRAY_3XUI_USERNAME="admin",
        XRAY_3XUI_PASSWORD="secret",
        telegram_bot_token="123:abc",
        telegram_admin_ids="1, 2,3",
    )

    assert settings.telegram_admin_ids == (1, 2, 3)


def test_settings_parse_single_admin_id_from_int() -> None:
    settings = Settings(
        XRAY_3XUI_BASE_URL="https://panel.example.com",
        XRAY_3XUI_USERNAME="admin",
        XRAY_3XUI_PASSWORD="secret",
        telegram_bot_token="123:abc",
        telegram_admin_ids=123456789,
    )

    assert settings.telegram_admin_ids == (123456789,)


def test_settings_poll_interval_defaults_to_five_minutes() -> None:
    settings = Settings(
        XRAY_3XUI_BASE_URL="https://panel.example.com",
        XRAY_3XUI_USERNAME="admin",
        XRAY_3XUI_PASSWORD="secret",
        telegram_bot_token="123:abc",
    )

    assert settings.poll_interval_seconds == 300
