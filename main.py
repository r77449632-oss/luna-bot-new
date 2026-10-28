import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import db
import dispatch
from config import BOT_TOKEN, WEBAPP_URL
from handlers import router


async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN .env faylida ko'rsatilmagan.")
    if not WEBAPP_URL.startswith("https://"):
        raise SystemExit("WEBAPP_URL HTTPS bo'lishi kerak (.env faylida).")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    dispatch_task = asyncio.create_task(dispatch.run(bot))  # navbat fonida ishlaydi
    try:
        await dp.start_polling(bot, allowed_updates=["message", "edited_message", "callback_query"])
    finally:
        dispatch_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
