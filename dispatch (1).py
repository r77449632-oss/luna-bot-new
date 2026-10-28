"""Buyurtmalarni haydovchilarga navbat bilan yuborish va vaqt bo'yicha holatlarni tekshirish."""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import (
    DRAFT_TIMEOUT_SEC, MAX_ONLINE_HOURS, OFFER_TIMEOUT_SEC, SEARCH_TIMEOUT_SEC, TICK_SEC, TZ,
)
from pricing import distance_km

log = logging.getLogger("taxi.dispatch")


def offer_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"accept:{order_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject:{order_id}"),
    ]])


async def safe_send(bot: Bot, chat_id: int, text: str, **kw) -> bool:
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except TelegramAPIError:
        log.info("Xabar yuborilmadi chat=%s", chat_id, exc_info=True)
        return False


def _rank(order, drivers):
    """Eng yaqin haydovchi birinchi; teng masofada eng uzoq kutgan haydovchi birinchi."""
    def key(d):
        km = distance_km(order["pickup_lat"], order["pickup_lon"], d["last_lat"], d["last_lon"])
        return (round(km, 1), d["last_accept_at"] or "")
    return sorted(drivers, key=key)


async def dispatch_next(bot: Bot, order_id: int) -> bool:
    """Buyurtma uchun bitta haydovchiga taklif yuboradi. Taklif yuborilsa True."""
    order = db.get_order(order_id)
    if not order or order["status"] != "new" or db.active_offer(order_id):
        return False
    candidates = _rank(order, db.candidate_drivers(order_id))
    for d in candidates:
        db.create_offer(order_id, d["tg_id"])
        try:
            await bot.send_location(d["tg_id"], order["pickup_lat"], order["pickup_lon"])
            await bot.send_message(
                d["tg_id"],
                f"🚕 Yangi buyurtma #{order_id}\n"
                f"📏 Masofa: {order['km']:.1f} km, ⏱ ~{order['minutes']} daqiqa\n"
                f"💰 Narx: {order['price']:,} so'm\n"
                f"Sizga {OFFER_TIMEOUT_SEC} soniya vaqt bor.",
                reply_markup=offer_kb(order_id),
            )
            return True
        except TelegramAPIError:
            # Haydovchi botni bloklagan: taklifni yopamiz va keyingisiga o'tamiz
            db.set_offer_status(order_id, d["tg_id"], "failed")
            log.info("Haydovchiga taklif yuborilmadi: %s", d["tg_id"])
    return False


async def _notify_passenger(bot: Bot, passenger_id: int, text: str):
    await safe_send(bot, passenger_id, text)


async def tick(bot: Bot):
    now = datetime.now(TZ).replace(tzinfo=None)
    offer_cut = (now - timedelta(seconds=OFFER_TIMEOUT_SEC)).isoformat(timespec="seconds")

    # 1. Javobsiz takliflar: keyingi haydovchiga o'tkaziladi
    for off in db.expired_offers(offer_cut):
        db.set_offer_status(off["order_id"], off["driver_id"], "expired")
        await dispatch_next(bot, off["order_id"])

    # 2. Tasdiqlanmagan draft buyurtmalar
    db.expire_drafts((now - timedelta(seconds=DRAFT_TIMEOUT_SEC)).isoformat(timespec="seconds"))

    # 3. Haydovchi topilmagan buyurtmalar
    search_cut = (now - timedelta(seconds=SEARCH_TIMEOUT_SEC)).isoformat(timespec="seconds")
    for o in db.stale_search_orders(search_cut):
        if db.expire_order(o["id"]):
            await _notify_passenger(
                bot, o["passenger_id"],
                f"😔 Buyurtma #{o['id']} uchun haydovchi topilmadi. Qaytadan urinib ko'ring.")

    # 4. Smena muddati tugagan haydovchilar
    cut = (now - timedelta(hours=MAX_ONLINE_HOURS)).isoformat(timespec="seconds")
    for tg_id in db.auto_offline(cut):
        await safe_send(bot, tg_id, f"⏰ {MAX_ONLINE_HOURS} soat ishladingiz, avtomatik offline bo'ldingiz. "
                                    "Qayta ishlash uchun 🟢 Online ni bosing.")

    # 5. Yangi buyurtmalar uchun taklif (navbat)
    for o in db.new_orders():
        await dispatch_next(bot, o["id"])


async def run(bot: Bot):
    """Fon jarayoni: har TICK_SEC soniyada tick() ni chaqiradi. Xatolik bo'lsa ham to'xtamaydi."""
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception("Navbat tekshiruvida xato")
        await asyncio.sleep(TICK_SEC)


async def backup_loop():
    """Kuniga bir marta bazadan zaxira nusxa oladi."""
    while True:
        try:
            path = await asyncio.to_thread(db.backup_db)
            if path:
                log.info("Zaxira nusxa yaratildi: %s", path)
        except Exception:
            log.exception("Zaxira nusxa yaratishda xato")
        await asyncio.sleep(3600)
