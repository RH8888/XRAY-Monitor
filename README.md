# XRAY Monitor

XRAY Monitor is an async Python service scaffold for polling a 3x-ui/Xray panel,
normalizing traffic records, tracking watched clients, computing usage metrics, and sending
Telegram alerts.

The project is intentionally modular: polling, parsing, persistence, analytics, alerting, and bot
interfaces live behind separate services so PostgreSQL or an optional FastAPI/API layer can be added
without rewriting the core monitoring flow.

## Project layout

```text
app/
  config/      Environment-driven settings
  database/    Async SQLAlchemy session/engine facade
  poller/      3x-ui HTTP client and polling orchestration
  parser/      Normalized traffic data models and future parsers
  watchlist/   Tracked client service boundary
  analytics/   Usage metric calculations
  alerts/      Alert policy and cooldown logic
  bot/         Telegram bot lifecycle and notifications
  utils/       Shared helpers
```

## Requirements

- Python 3.11+
- SQLite for the default deployment path
- A 3x-ui/Xray panel URL and credentials or token
- A Telegram bot token for alert delivery

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env` with your panel URL, authentication settings, Telegram token, and admin IDs.

## Configuration

All runtime settings use the `XRAY_` prefix and can be supplied in `.env` or the process
environment.

| Setting | Purpose |
| --- | --- |
| `XRAY_3XUI_BASE_URL` | Base URL for the 3x-ui panel. |
| `XRAY_3XUI_USERNAME` / `XRAY_3XUI_PASSWORD` | Panel credentials for future login/session support. |
| `XRAY_3XUI_TOKEN` | Optional bearer/API token for token-enabled deployments. |
| `XRAY_POLL_INTERVAL_SECONDS` | Interval between polling jobs, defaulting to 300 seconds (5 minutes). |
| `XRAY_LOG_COUNT` | Number of recent log records to request. |
| `XRAY_DATABASE_URL` | SQLAlchemy async URL, defaulting to `sqlite+aiosqlite`. |
| `XRAY_TELEGRAM_BOT_TOKEN` | Telegram bot token. |
| `XRAY_TELEGRAM_ADMIN_IDS` | Comma-separated admin Telegram user IDs. |
| `XRAY_ALERTS_ENABLED` | Enables or disables alert evaluation. |
| `XRAY_ALERT_USAGE_THRESHOLD_PERCENT` | Usage percentage that triggers alerts. |
| `XRAY_ALERT_COOLDOWN_SECONDS` | Per-alert cooldown window. |
| `XRAY_ALERT_SEND_STARTUP_MESSAGE` | Sends a startup message to admins when true. |
| `XRAY_LOG_LEVEL` | Python logging level. |

## Running locally

```bash
xray-monitor
```

On startup, the service immediately requests the 3x-ui Xray log API once and then repeats the
request every `XRAY_POLL_INTERVAL_SECONDS` seconds. Request attempts, successes, and failures are
written to the normal `xray-monitor` application logs to make API connectivity troubleshooting easier.

or:

```bash
python -m app.main
```

## Running with Docker Compose

```bash
cp .env.example .env
# edit .env first
docker compose up --build -d
```

The default compose file stores SQLite data in the `xray-monitor-data` named volume at `/app/data`.

## Development checks

```bash
pytest
ruff check .
mypy app tests
```

## Future extension points

- **PostgreSQL:** change `XRAY_DATABASE_URL` to `postgresql+asyncpg://...` and install the
  `postgres` optional dependency.
- **FastAPI/API:** add routers that depend on the service classes in `app/` instead of embedding
  business logic in HTTP handlers.
- **Panel-specific parsers:** implement parser modules that transform 3x-ui response variants into
  `TrafficSample` objects.
