import asyncio
import logging
from contextlib import suppress

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

from app.alerts.service import AlertService
from app.bot import TelegramBotService
from app.config import get_settings
from app.database import Database
from app.poller import PollerService, ThreeXUIClient
from app.utils import configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database(settings.database_url)
    await database.create_all()

    xui_client = ThreeXUIClient(settings)
    bot = TelegramBotService(settings, database.session_factory)
    alert_service = AlertService(settings, dispatcher=bot)
    poller = PollerService(
        settings,
        xui_client,
        database.session_factory,
        alert_service=alert_service,
        bot_service=bot,
    )
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def poll_job() -> None:
        result = await poller.poll_once()
        logger.info("poll completed", extra={"stored_events": result.stored_events})

    scheduler.add_job(
        poll_job,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="xray_poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    bot_task = asyncio.create_task(bot.start_polling())

    if settings.alert_send_startup_message:
        await bot.send_admin_message("XRAY Monitor started.")

    logger.info("XRAY Monitor service started")
    try:
        await asyncio.Event().wait()
    finally:
        bot_task.cancel()
        with suppress(asyncio.CancelledError):
            await bot_task
        scheduler.shutdown(wait=False)
        await xui_client.close()
        await bot.close()
        await database.dispose()


def run() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(main())


if __name__ == "__main__":
    run()
