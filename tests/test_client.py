import logging

import httpx
import pytest

from app.config.settings import Settings
from app.poller.client import ThreeXUIClient, ThreeXUIClientError


def make_settings() -> Settings:
    return Settings(
        XRAY_3XUI_BASE_URL="https://panel.example.com",
        XRAY_3XUI_USERNAME="admin",
        XRAY_3XUI_PASSWORD="secret",
        telegram_bot_token="123:abc",
    )


@pytest.mark.anyio
async def test_fetch_xray_logs_logs_request_and_success(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/panel/api/server/xraylogs/2"
        return httpx.Response(200, json={"success": True, "obj": "first\nsecond"})

    http_client = httpx.AsyncClient(
        base_url="https://panel.example.com",
        transport=httpx.MockTransport(handler),
    )
    client = ThreeXUIClient(make_settings(), http_client=http_client)

    with caplog.at_level(logging.INFO, logger="app.poller.client"):
        logs = await client.fetch_xray_logs(2)

    await http_client.aclose()

    assert logs == "first\nsecond"
    assert "requesting Xray logs from 3x-ui API" in caplog.text
    assert "xray log fetch succeeded" in caplog.text


@pytest.mark.anyio
async def test_fetch_xray_logs_logs_unsuccessful_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    http_client = httpx.AsyncClient(
        base_url="https://panel.example.com",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": False, "msg": "not ready"})
        ),
    )
    client = ThreeXUIClient(make_settings(), http_client=http_client)

    with caplog.at_level(logging.WARNING, logger="app.poller.client"):
        with pytest.raises(ThreeXUIClientError):
            await client.fetch_xray_logs(2)

    await http_client.aclose()

    assert "xray log fetch returned unsuccessful response" in caplog.text


@pytest.mark.anyio
async def test_fetch_xray_logs_accepts_structured_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events = [
        {
            "DateTime": "2026-05-16T16:03:56.444907Z",
            "FromAddress": "20.76.219.231:19595",
            "ToAddress": "tcp:www.google.com:443",
            "Inbound": "inbound-80",
            "Outbound": "direct",
            "Email": "Hooman",
            "Event": 0,
        }
    ]
    http_client = httpx.AsyncClient(
        base_url="https://panel.example.com",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": True, "msg": "", "obj": events})
        ),
    )
    client = ThreeXUIClient(make_settings(), http_client=http_client)

    with caplog.at_level(logging.INFO, logger="app.poller.client"):
        logs = await client.fetch_xray_logs(2)

    await http_client.aclose()

    assert logs == events
    assert "payload_format=structured_json" in caplog.text


@pytest.mark.anyio
async def test_fetch_raw_logs_returns_structured_events_unchanged() -> None:
    events = [{"Email": "Hooman", "ToAddress": "tcp:www.google.com:443"}]
    http_client = httpx.AsyncClient(
        base_url="https://panel.example.com",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": True, "obj": events})
        ),
    )
    client = ThreeXUIClient(make_settings(), http_client=http_client)

    raw_logs = await client.fetch_raw_logs(2)

    await http_client.aclose()

    assert raw_logs == events
