"""Buyurtmalarni haydovchilarga navbat bilan yuborish.

Har buyurtma bitta eng yaqin bo'sh haydovchiga taklif qilinadi. U OFFER_TIMEOUT_SEC ichida
javob bermasa yoki rad etsa, keyingi eng yaqin haydovchiga o'tadi.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import DRAFT_TIMEOUT_SEC, OFFER_TIMEOUT_SEC, SEARCH_TIMEOUT_SEC, TICK_SEC
from pricing import distance_km

log = logging.getLogger("taxi.dispatch")


def offer_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"accept:{order_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject:{order_id}"),
    ]])


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception:
        log.warning("Xabar yuborilmadi: chat_id=%s", chat_id, exc_info=True)
        return False


async def dispatch_next(bot: Bot, order_id: int):
    """Eng yaqin bo'sh haydovchiga bitta taklif yuboradi."""
    o = db.get_order(order_id)
    if not o or o["status"] != "new" or db.active_offer(order_id):
        return

    ranked = []
    for d in db.candidate_drivers(order_id):
        dist = distance_km(o["pickup_lat"], o["pickup_lon"], d["last_lat"], d["last_lon"])
        # Teng masofada bo'lsa, eng uzoq vaqt buyurtma olmagan haydovchi birinchi
        ranked.append((round(dist, 1), d["last_accept_at"] or "", dist, d))
    ranked.sort(key=lambda x: (x[0], x[1]))

    for _, _, dist, d in ranked:
        db.create_offer(order_id, d["tg_id"])
        text = (f"🚕 Yangi buyurtma #{order_id}\n"
                f"Sizdan yo'lovchigacha: {dist:.1f} km\n"
                f"Safar: {o['km']:.1f} km, {o['minutes']} daqiqa\n"
                f"Narx: {o['price']:,} so'm\n"
                f"Siz olasiz: {o['driver_earning']:,} so'm (💵 naqd)\n"
                f"⏳ {OFFER_TIMEOUT_SEC} soniya ichida javob bering.")
        try:
            await bot.send_message(d["tg_id"], text, reply_markup=offer_kb(order_id))
            await bot.send_location(d["tg_id"], o["pickup_lat"], o["pickup_lon"])
            return
        except Exception:
            log.warning("Taklif yuborilmadi: haydovchi=%s", d["tg_id"], exc_info=True)
            db.set_offer_status(order_id, d["tg_id"], "expired")
    # Bo'sh haydovchi yo'q: tick keyinroq qayta urinadi


async def tick(bot: Bot):
    # 1. Javob muddati o'tgan takliflar
    for off in db.expired_offers(db.ago(OFFER_TIMEOUT_SEC)):
        db.set_offer_status(off["order_id"], off["driver_id"], "expired")

    # 2. Tasdiqlanmagan buyurtmalar
    db.expire_drafts(db.ago(DRAFT_TIMEOUT_SEC))

    # 3. Haydovchi topilmagan buyurtmalar
    for o in db.stale_search_orders(db.ago(SEARCH_TIMEOUT_SEC)):
        if db.expire_order(o["id"]):
            await safe_send(bot, o["passenger_id"],
                            f"😔 Buyurtma #{o['id']}: yaqin atrofda bo'sh haydovchi topilmadi. "
                            f"Keyinroq qayta urinib ko'ring.")

    # 4. Yangi buyurtmalarni bo'sh haydovchilarga yuborish
    for o in db.new_orders():
        await dispatch_next(bot, o["id"])


async def run(bot: Bot):
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("Navbat tekshiruvida xato")
        await asyncio.sleep(TICK_SEC)


__all__ = ["run", "dispatch_next", "safe_send", "SEARCH_TIMEOUT_SEC"]
