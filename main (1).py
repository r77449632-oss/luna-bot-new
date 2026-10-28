import asyncio
import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher

import db
from config import BOT_TOKEN, LOCK_PORT, LOG_DIR, WEBAPP_URL
from dispatch import backup_loop, run
from handlers import router
from storage import DbStorage


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(os.path.join(LOG_DIR, "bot.log"),
                                       maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream])


def acquire_lock() -> socket.socket:
    """Bir vaqtda ikkinchi nusxa ishga tushmasligi uchun lokal port band qilinadi."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        raise SystemExit("Bot allaqachon ishlayapti (boshqa nusxani to'xtating).")
    return s


async def main():
    setup_logging()
    log = logging.getLogger("taxi.main")
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        raise SystemExit("BOT_TOKEN .env faylida noto'g'ri yoki yo'q.")
    if not WEBAPP_URL.startswith("https://"):
        raise SystemExit("WEBAPP_URL https:// bilan boshlanishi kerak.")

    _lock = acquire_lock()  # noqa: F841  (jarayon tugaguncha ushlab turiladi)
    db.init_db()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=DbStorage())
    dp.include_router(router)

    tasks = [asyncio.create_task(run(bot)), asyncio.create_task(backup_loop())]
    log.info("Bot ishga tushdi")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "edited_message", "callback_query"])
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
