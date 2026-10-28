import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import db
from config import BOT_TOKEN, WEBAPP_URL
from handlers import router


async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN .env faylida ko'rsatilmagan.")
    if not WEBAPP_URL.startswith("https://"):
        raise SystemExit("WEBAPP_URL HTTPS bo'lishi kerak (.env faylida).")

    logging.basicConfig(level=logging.INFO)
    db.init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
