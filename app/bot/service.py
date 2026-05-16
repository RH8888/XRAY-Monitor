from aiogram import Bot, Dispatcher

from app.config import Settings


class TelegramBotService:
    """Owns Telegram bot lifecycle while handlers remain pluggable."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dispatcher = Dispatcher()

    async def send_admin_message(self, text: str) -> None:
        for admin_id in self._settings.telegram_admin_ids:
            await self.bot.send_message(admin_id, text)

    async def close(self) -> None:
        await self.bot.session.close()
