import asyncio
import logging
from contextlib import suppress

from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
    xui_client = ThreeXUIClient(settings)
    poller = PollerService(settings, xui_client)
    bot = TelegramBotService(settings)
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def poll_job() -> None:
        samples = await poller.poll_once()
        logger.info("poll completed", extra={"sample_count": len(samples)})

    scheduler.add_job(
        poll_job,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="xray_poll",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    if settings.alert_send_startup_message:
        await bot.send_admin_message("XRAY Monitor started.")

    logger.info("XRAY Monitor service started")
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await xui_client.close()
        await bot.close()
        await database.dispose()


def run() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(main())


if __name__ == "__main__":
    run()
