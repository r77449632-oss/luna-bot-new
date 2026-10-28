import json
import logging
import re
import sqlite3

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from config import (ADMIN_IDS, DEFAULT_SETTINGS, LAT_RANGE, LIVE_PERIOD, LON_RANGE,
                    MIN_TRIP_KM, OFFER_TIMEOUT_SEC)
from dispatch import dispatch_next, safe_send
from keyboards import main_menu
from pricing import calc_price, route

log = logging.getLogger("taxi.handlers")
router = Router()

PAGE = 10
PHONE_RE = re.compile(r"\+?\d{9,13}")
STATUS_TEXT = {
    "pending": "kutilmoqda ⏳",
    "approved": "tasdiqlangan ✅",
    "rejected": "rad etilgan ❌",
    "blocked": "bloklangan 🚫",
}


class DriverApply(StatesGroup):
    name = State()
    car = State()
    phone = State()
    license = State()
    techpass = State()


# ---------- Yordamchilar ----------

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def approved_driver(uid: int):
    d = db.get_driver(uid)
    return d if d and d["status"] == "approved" else None


def valid_point(lat: float, lon: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def rate_kb(prefix: str, order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{i}⭐", callback_data=f"{prefix}:{order_id}:{i}")
        for i in range(1, 6)
    ]])


def passenger_cancel_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel:{order_id}"),
    ]])


def driver_order_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏁 Tugatish", callback_data=f"done:{order_id}"),
        InlineKeyboardButton(text="↩️ Bekor qilish", callback_data=f"dcancel:{order_id}"),
    ]])


async def stop_live(bot: Bot, order):
    if order and order["live_msg_id"]:
        try:
            await bot.stop_message_live_location(chat_id=order["passenger_id"],
                                                 message_id=order["live_msg_id"])
        except Exception:
            log.debug("Jonli lokatsiyani to'xtatib bo'lmadi", exc_info=True)


# ---------- Umumiy ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Assalomu alaykum! 🚕 tugmasi orqali taksi chaqiring.\nHaydovchi bo'lish uchun /driver",
        reply_markup=main_menu(message.from_user.id),
    )


# ---------- Yo'lovchi ----------

@router.message(F.web_app_data)
async def order_from_map(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        pl, po = float(data["pickup"]["lat"]), float(data["pickup"]["lon"])
        dl, dn = float(data["dest"]["lat"]), float(data["dest"]["lon"])
    except (KeyError, TypeError, ValueError):
        await message.answer("Ma'lumot noto'g'ri keldi. Qaytadan urinib ko'ring.")
        return

    if not (valid_point(pl, po) and valid_point(dl, dn)):
        await message.answer("Belgilangan nuqta ruxsat etilgan hududdan tashqarida.")
        return

    km, minutes = await route(pl, po, dl, dn)
    if km < MIN_TRIP_KM:
        await message.answer("Borish joyi juda yaqin. Xaritada boshqa nuqtani belgilang.")
        return

    settings = db.get_settings()
    price = calc_price(km, minutes, settings)
    commission = round(price * settings["commission_percent"] / 100)
    order_id = db.create_order(message.from_user.id, pl, po, dl, dn, km, minutes, price,
                               commission, price - commission)
    await message.answer(
        f"Buyurtma #{order_id}\n"
        f"Masofa: {km:.1f} km, taxminan {minutes} daqiqa\n"
        f"Narx: {price:,} so'm\n"
        f"To'lov: 💵 naqd pul",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"confirm:{order_id}"),
            InlineKeyboardButton(text="❌ Bekor", callback_data=f"cancel:{order_id}"),
        ]]),
    )


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_order(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    if not db.confirm_order(order_id, cb.from_user.id):
        await cb.answer("Bu buyurtma allaqachon yuborilgan yoki muddati tugagan.", show_alert=True)
        return
    await cb.message.edit_text(f"Buyurtma #{order_id} yuborildi. Haydovchi qidirilmoqda... 🔍",
                               reply_markup=passenger_cancel_kb(order_id))
    await cb.answer()
    await dispatch_next(bot, order_id)


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_order(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    o = db.get_order(order_id)
    if not o or not db.cancel_order(order_id, cb.from_user.id):
        await cb.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
        return
    await stop_live(bot, o)
    await cb.message.edit_text(f"Buyurtma #{order_id} bekor qilindi.")
    await cb.answer()
    if o["driver_id"]:
        await safe_send(bot, o["driver_id"], f"⚠️ Yo'lovchi #{order_id} buyurtmani bekor qildi.")


@router.message(F.text == "📜 Buyurtmalarim")
async def history(message: Message):
    rows = db.passenger_orders(message.from_user.id)
    if not rows:
        await message.answer("Hali buyurtmalar yo'q.")
        return
    lines = [f"#{r['id']} | {r['price']:,} so'm | {r['status']}" for r in rows]
    await message.answer("Oxirgi buyurtmalar:\n" + "\n".join(lines))


@router.callback_query(F.data.startswith("rate:"))
async def rate_driver(cb: CallbackQuery):
    _, order_id, stars = cb.data.split(":")
    if db.rate_order(int(order_id), cb.from_user.id, int(stars)):
        await cb.message.edit_text(f"Rahmat! Haydovchiga bahoyingiz: {stars}⭐")
        await cb.answer()
    else:
        await cb.answer("Baholash mumkin emas.", show_alert=True)


# ---------- Haydovchi: ariza ----------

@router.message(Command("driver"))
async def driver_apply(message: Message, state: FSMContext):
    d = db.get_driver(message.from_user.id)
    if d:
        await message.answer(f"Arizangiz holati: {STATUS_TEXT[d['status']]}",
                             reply_markup=main_menu(message.from_user.id))
        return
    await state.set_state(DriverApply.name)
    await message.answer("Haydovchi bo'lish uchun ariza. Ismingizni yozing (2–60 belgi):")


@router.message(DriverApply.name, F.text)
async def apply_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 60:
        await message.answer("Ism 2 dan 60 gacha belgidan iborat bo'lsin. Qaytadan yozing:")
        return
    await state.update_data(name=name)
    await state.set_state(DriverApply.car)
    await message.answer("Mashina modeli va davlat raqami (masalan: Nexia 3, 01 A 123 BC):")


@router.message(DriverApply.car, F.text)
async def apply_car(message: Message, state: FSMContext):
    car = message.text.strip()
    if not 3 <= len(car) <= 60:
        await message.answer("Mashina ma'lumoti 3 dan 60 gacha belgi bo'lsin. Qaytadan yozing:")
        return
    await state.update_data(car=car)
    await state.set_state(DriverApply.phone)
    await message.answer("Telefon raqamingiz (masalan: +998901234567):")


@router.message(DriverApply.phone, F.text)
async def apply_phone(message: Message, state: FSMContext):
    phone = message.text.replace(" ", "").replace("-", "")
    if not PHONE_RE.fullmatch(phone):
        await message.answer("Telefon noto'g'ri. Masalan: +998901234567")
        return
    if db.phone_taken(phone):
        await message.answer("Bu telefon raqami bilan allaqachon ariza berilgan.")
        return
    await state.update_data(phone=phone)
    await state.set_state(DriverApply.license)
    await message.answer("Haydovchilik guvohnomangizning rasmini yuboring:")


@router.message(DriverApply.license, F.photo)
async def apply_license(message: Message, state: FSMContext):
    await state.update_data(license_file=message.photo[-1].file_id)
    await state.set_state(DriverApply.techpass)
    await message.answer("Texpasportning rasmini yuboring:")


@router.message(DriverApply.techpass, F.photo)
async def apply_techpass(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    try:
        db.create_driver(uid, data["name"], data["phone"], data["car"],
                         data["license_file"], message.photo[-1].file_id)
    except sqlite3.IntegrityError:
        await message.answer("Bu telefon raqami bilan allaqachon ariza berilgan.",
                             reply_markup=main_menu(uid))
        return
    d = db.get_driver(uid)
    for admin_id in ADMIN_IDS:
        await send_application(bot, admin_id, d)
    await message.answer("Arizangiz yuborildi ⏳ Tasdiqlangach xabar beramiz.",
                         reply_markup=main_menu(uid))


@router.message(DriverApply.license)
@router.message(DriverApply.techpass)
async def apply_need_photo(message: Message):
    await message.answer("Iltimos, rasm yuboring.")


async def send_application(bot: Bot, chat_id: int, d):
    text = (f"🆕 Haydovchi arizasi\n"
            f"Ism: {d['name']}\nMashina: {d['car']}\nTel: {d['phone']}\nID: {d['tg_id']}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm:approve:{d['tg_id']}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm:reject:{d['tg_id']}"),
    ]])
    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
        await bot.send_photo(chat_id, d["license_file"], caption="Haydovchilik guvohnomasi")
        await bot.send_photo(chat_id, d["techpass_file"], caption="Texpasport")
    except Exception:
        log.warning("Ariza adminga yuborilmadi", exc_info=True)


# ---------- Haydovchi: ish ----------

@router.message(F.text == "🟢 Online")
async def go_online(message: Message):
    d = approved_driver(message.from_user.id)
    if not d:
        await message.answer("Arizangiz hali tasdiqlanmagan.")
        return
    if d["last_lat"] is None:
        await message.answer("Avval lokatsiyangizni yuboring: 📎 > Lokatsiya. "
                             "Shunda sizga yaqin buyurtmalar keladi.")
        return
    db.set_online(message.from_user.id, 1)
    await message.answer("Siz online. Yaqin atrofdagi buyurtmalar sizga yuboriladi.")


@router.message(F.text == "⚫ Offline")
async def go_offline(message: Message):
    db.set_online(message.from_user.id, 0)
    await message.answer("Siz offline.")


@router.message(F.text == "📊 Statistika")
async def driver_stats(message: Message):
    d = approved_driver(message.from_user.id)
    if not d:
        await message.answer("Siz tasdiqlangan haydovchi emassiz.")
        return
    s = db.driver_stats(message.from_user.id)
    await message.answer(
        f"Reyting: {d['rating']:.1f} ⭐ ({d['rating_count']} ta baho)\n"
        f"Bajarilgan buyurtmalar: {s['cnt']}\n"
        f"Sof daromad: {s['total']:,} so'm (komissiyadan keyin)"
    )


@router.message(F.location)
async def driver_location(message: Message, bot: Bot):
    db.update_driver_location(message.from_user.id,
                              message.location.latitude, message.location.longitude)
    if message.location.live_period:
        await forward_live(message, bot)


@router.edited_message(F.location)
async def driver_location_update(message: Message, bot: Bot):
    db.update_driver_location(message.from_user.id,
                              message.location.latitude, message.location.longitude)
    await forward_live(message, bot)


async def forward_live(message: Message, bot: Bot):
    """Haydovchining jonli lokatsiyasini yo'lovchiga uzatadi."""
    o = db.active_order_for_driver(message.from_user.id)
    if not o:
        return
    lat, lon = message.location.latitude, message.location.longitude
    if o["live_msg_id"] is not None:
        try:
            await bot.edit_message_live_location(lat, lon, chat_id=o["passenger_id"],
                                                 message_id=o["live_msg_id"])
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
        except Exception:
            log.debug("Jonli lokatsiya yangilanmadi, yangisi yuboriladi", exc_info=True)
    sent = await bot.send_location(o["passenger_id"], lat, lon, live_period=LIVE_PERIOD)
    db.set_live_msg(o["id"], sent.message_id)


@router.callback_query(F.data.startswith("accept:"))
async def accept_order(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    d = approved_driver(uid)
    if not d or not d["online"]:
        await cb.answer("Avval 🟢 Online bo'ling.", show_alert=True)
        return

    offer = db.get_offer(order_id, uid)
    if not offer or offer["status"] != "sent" or offer["sent_at"] < db.ago(OFFER_TIMEOUT_SEC):
        await cb.answer("Bu taklif muddati o'tgan yoki boshqa haydovchiga berilgan.",
                        show_alert=True)
        return

    if not db.accept_order(order_id, uid):
        await cb.answer("Buyurtma allaqachon olingan yoki bekor qilingan.", show_alert=True)
        return

    db.set_offer_status(order_id, uid, "accepted")
    db.mark_accept(uid)
    o = db.get_order(order_id)
    await cb.message.edit_text(
        f"✅ #{order_id} buyurtma sizda.\n"
        f"Yo'lovchiga boring va jonli lokatsiyani ulang: 📎 > Lokatsiya > Jonli lokatsiya ulashish",
        reply_markup=driver_order_kb(order_id),
    )
    await cb.answer()
    await safe_send(
        bot, o["passenger_id"],
        f"Haydovchi topildi!\nIsm: {d['name']}\nMashina: {d['car']}\n"
        f"Reyting: {d['rating']:.1f} ⭐\nNarx: {o['price']:,} so'm (💵 naqd)",
        reply_markup=passenger_cancel_kb(order_id),
    )
    await bot.send_location(uid, o["dest_lat"], o["dest_lon"])


@router.callback_query(F.data.startswith("reject:"))
async def reject_offer(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    offer = db.get_offer(order_id, uid)
    if offer and offer["status"] == "sent":
        db.set_offer_status(order_id, uid, "rejected")
    await cb.message.edit_text(f"❌ #{order_id} buyurtmani rad etdingiz.")
    await cb.answer()
    await dispatch_next(bot, order_id)


@router.callback_query(F.data.startswith("dcancel:"))
async def driver_cancel(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    o = db.get_order(order_id)
    if not o or o["driver_id"] != uid or not db.release_order(order_id):
        await cb.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
        return
    await stop_live(bot, o)
    await cb.message.edit_text(f"⚠️ #{order_id} buyurtmani bekor qildingiz.")
    await cb.answer()
    await safe_send(bot, o["passenger_id"],
                    f"⚠️ Haydovchi buyurtma #{order_id} ni bekor qildi. "
                    f"Yangi haydovchi qidirilmoqda...",
                    reply_markup=passenger_cancel_kb(order_id))
    await dispatch_next(bot, order_id)


@router.callback_query(F.data.startswith("done:"))
async def finish(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    if not db.finish_order(order_id, cb.from_user.id):
        await cb.answer("Buyurtma topilmadi.", show_alert=True)
        return
    o = db.get_order(order_id)
    await stop_live(bot, o)
    await cb.message.edit_text(
        f"🏁 #{order_id} yakunlandi. {o['price']:,} so'mni yo'lovchidan naqd oling.\n"
        f"Yo'lovchini baholang:",
        reply_markup=rate_kb("prate", order_id),
    )
    await cb.answer()
    await safe_send(bot, o["passenger_id"],
                    f"Safar yakunlandi. Narx: {o['price']:,} so'm (💵 naqd).\n"
                    f"Haydovchini baholang:",
                    reply_markup=rate_kb("rate", order_id))


@router.callback_query(F.data.startswith("prate:"))
async def rate_passenger(cb: CallbackQuery):
    _, order_id, stars = cb.data.split(":")
    if db.rate_passenger(int(order_id), cb.from_user.id, int(stars)):
        await cb.message.edit_text(f"Rahmat! Yo'lovchiga bahoyingiz: {stars}⭐")
        await cb.answer()
    else:
        await cb.answer("Baholash mumkin emas.", show_alert=True)


# ---------- Admin ----------

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Arizalar", callback_data="adm:pending:0")],
        [InlineKeyboardButton(text="🚖 Haydovchilar", callback_data="adm:drivers:0")],
        [InlineKeyboardButton(text="📊 Hisobot", callback_data="adm:stats")],
        [InlineKeyboardButton(text="💲 Narxlar", callback_data="adm:prices")],
    ])


@router.message(F.text == "🛠 Admin")
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🛠 Admin panel", reply_markup=admin_kb())


@router.message(Command("setprice"))
async def set_price(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or parts[1] not in DEFAULT_SETTINGS or not parts[2].isdigit():
        await message.answer("Foydalanish: /setprice <kalit> <qiymat>\n"
                             f"Kalitlar: {', '.join(DEFAULT_SETTINGS)}")
        return
    key, value = parts[1], int(parts[2])
    if key == "commission_percent" and value > 100:
        await message.answer("Komissiya 0 dan 100 gacha bo'lishi kerak.")
        return
    db.set_setting(key, value)
    await message.answer(f"{key} = {value} saqlandi ✅")


def report_text() -> str:
    from datetime import datetime, timedelta
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lines = ["📊 Hisobot (tugagan safarlar):"]
    for title, since in [("Bugun", midnight),
                         ("7 kun", now - timedelta(days=7)),
                         ("30 kun", now - timedelta(days=30))]:
        cnt, total, comm = db.report(since)
        lines.append(f"{title}: {cnt} ta, {total:,} so'm (komissiya: {comm:,})")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("adm:"))
async def admin_action(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q.", show_alert=True)
        return

    parts = cb.data.split(":")
    action = parts[1]
    admin_chat = cb.from_user.id

    if action in ("approve", "reject", "block", "unblock"):
        target = int(parts[2])
        if not db.get_driver(target):
            await cb.answer("Haydovchi topilmadi.", show_alert=True)
            return
        new_status = {"approve": "approved", "reject": "rejected",
                      "block": "blocked", "unblock": "approved"}[action]
        released = db.set_driver_status(target, new_status)
        # Bloklangan/rad etilgan haydovchining faol buyurtmalari qayta navbatga qo'yiladi
        for oid in released:
            if db.release_order(oid):
                o = db.get_order(oid)
                await safe_send(bot, o["passenger_id"],
                                f"⚠️ Buyurtma #{oid} uchun haydovchi o'zgardi. "
                                f"Yangi haydovchi qidirilmoqda...")
                await dispatch_next(bot, oid)
        notice = {
            "approved": "✅ Ariza tasdiqlandi! 🟢 Online ni bosib ishni boshlang.",
            "rejected": "❌ Arizangiz rad etildi.",
            "blocked": "🚫 Hisobingiz bloklandi.",
        }[new_status]
        await safe_send(bot, target, notice, reply_markup=main_menu(target))
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await cb.answer(f"Holat: {STATUS_TEXT[new_status]}")
        return

    if action in ("pending", "drivers"):
        offset = int(parts[2]) if len(parts) > 2 else 0
        statuses = ("pending",) if action == "pending" else ("approved", "blocked")
        rows = db.list_drivers(statuses, offset, PAGE)
        total = db.count_drivers(statuses)
        if not rows:
            await safe_send(bot, admin_chat, "Ro'yxat bo'sh.")
        for d in rows:
            if action == "pending":
                await send_application(bot, admin_chat, d)
            else:
                if d["status"] == "approved":
                    btn = InlineKeyboardButton(text="🚫 Bloklash",
                                               callback_data=f"adm:block:{d['tg_id']}")
                else:
                    btn = InlineKeyboardButton(text="♻️ Blokdan chiqarish",
                                               callback_data=f"adm:unblock:{d['tg_id']}")
                await safe_send(
                    bot, admin_chat,
                    f"{d['name']} | {d['car']} | ⭐ {d['rating']:.1f} ({d['rating_count']})\n"
                    f"Holat: {STATUS_TEXT[d['status']]}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]),
                )
        if offset + PAGE < total:
            await safe_send(bot, admin_chat, f"Yana {total - offset - PAGE} ta bor.",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="Keyingi sahifa ➡️",
                                                     callback_data=f"adm:{action}:{offset + PAGE}"),
                            ]]))
        await cb.answer()
        return

    if action == "stats":
        await safe_send(bot, admin_chat, report_text())
        await cb.answer()
        return

    if action == "prices":
        s = db.get_settings()
        text = "💲 Joriy sozlamalar:\n" + "\n".join(f"{k}: {v}" for k, v in s.items())
        text += ("\n\nO'zgartirish: /setprice <kalit> <qiymat>\n"
                 "Masalan: /setprice base_price 6000")
        await safe_send(bot, admin_chat, text)
        await cb.answer()
        return

    await cb.answer()


# ---------- Qolgan xabarlar ----------

@router.message(F.text == "🚕 Taksi chaqirish")
async def webapp_hint(message: Message):
    await message.answer("Xarita ochilishi uchun 🚕 tugmasini pastdagi menyudan bosing.")
