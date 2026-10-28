"""Barcha buyruq va tugmalar: yo'lovchi, haydovchi, admin."""
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove,
)

import db
from config import (
    ADMIN_IDS, DEFAULT_SETTINGS, LAT_RANGE, LIVE_PERIOD, LON_RANGE, MAX_ONLINE_HOURS,
    MIN_TRIP_KM, OFFER_TIMEOUT_SEC, TZ,
)
from dispatch import dispatch_next, safe_send
from keyboards import main_menu
from pricing import calc_price, route

log = logging.getLogger("taxi.handlers")
router = Router()
PAGE = 10
PHONE_RE = re.compile(r"\+?\d{9,13}")
STATUS_TEXT = {
    "pending": "⏳ ariza ko'rib chiqilmoqda",
    "approved": "✅ tasdiqlangan",
    "rejected": "❌ rad etilgan",
    "blocked": "🚫 bloklangan",
}


class DriverApply(StatesGroup):
    name = State()
    car = State()
    phone = State()
    license = State()
    techpass = State()


class Complaint(StatesGroup):
    text = State()


# ---------- Yordamchi funksiyalar ----------

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def valid_point(lat: float, lon: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def local_now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def rating_row(prefix: str, oid: int):
    return [btn(f"{n}⭐", f"{prefix}:{oid}:{n}") for n in range(1, 6)]


def passenger_cancel_kb(oid: int):
    return kb([btn("❌ Bekor qilish", f"cancel:{oid}")])


def driver_order_kb(oid: int):
    return kb([btn("🚗 Yetib keldim", f"arrive:{oid}")],
              [btn("🏁 Safarni yakunlash", f"done:{oid}")],
              [btn("❌ Bekor qilish", f"dcancel:{oid}")])


def done_kb_passenger(oid: int):
    return kb(rating_row("rate", oid), [btn("⚠️ Shikoyat", f"complain:{oid}")])


def done_kb_driver(oid: int):
    return kb(rating_row("prate", oid), [btn("⚠️ Shikoyat", f"complain:{oid}")])


async def stop_live(bot: Bot, order):
    """Yo'lovchiga yuborilgan jonli lokatsiyani to'xtatadi."""
    if order and order["live_msg_id"]:
        try:
            await bot.stop_message_live_location(chat_id=order["passenger_id"],
                                                 message_id=order["live_msg_id"])
        except TelegramAPIError:
            pass
        db.set_live_msg(order["id"], None)


async def forward_live(bot: Bot, order, lat: float, lon: float):
    """Haydovchining joriy joyini yo'lovchi chatida jonli ko'rsatadi."""
    try:
        if order["live_msg_id"]:
            try:
                await bot.edit_message_live_location(
                    latitude=lat, longitude=lon,
                    chat_id=order["passenger_id"], message_id=order["live_msg_id"])
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e).lower():
                    return
                db.set_live_msg(order["id"], None)  # eskirgan xabar: yangisini yuboramiz
        msg = await bot.send_location(order["passenger_id"], lat, lon, live_period=LIVE_PERIOD)
        db.set_live_msg(order["id"], msg.message_id)
    except TelegramAPIError:
        log.warning("Jonli lokatsiya yuborilmadi, buyurtma #%s", order["id"], exc_info=True)


async def send_application(bot: Bot, chat_id: int, d):
    text = (f"🆕 Yangi haydovchi arizasi\nID: {d['tg_id']}\nIsm: {d['name']}\n"
            f"Mashina: {d['car']}\nTel: {d['phone']}")
    markup = kb([btn("✅ Tasdiqlash", f"adm:approve:{d['tg_id']}"),
                 btn("❌ Rad etish", f"adm:reject:{d['tg_id']}")])
    await bot.send_photo(chat_id, d["license_file"], caption=text + "\n📄 Guvohnoma")
    await bot.send_photo(chat_id, d["techpass_file"], caption="📄 Texpasport", reply_markup=markup)


# ---------- Umumiy ----------

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Amal bekor qilindi.", reply_markup=main_menu(message.from_user.id))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Assalomu alaykum! Luna taksi botiga xush kelibsiz.\n"
        "🚕 Taksi chaqirish uchun tugmani bosing va xaritada manzilni belgilang.",
        reply_markup=main_menu(message.from_user.id),
    )


@router.message(F.text == "🚕 Taksi chaqirish")
async def webapp_hint(message: Message):
    await message.answer("Xarita tugmasi ishlamasa, Telegram ilovasini yangilang.")


# ---------- Yo'lovchi ----------

@router.message(F.web_app_data)
async def order_from_map(message: Message, bot: Bot):
    uid = message.from_user.id
    try:
        data = json.loads(message.web_app_data.data)
        p_lat, p_lon = float(data["pickup"]["lat"]), float(data["pickup"]["lon"])
        d_lat, d_lon = float(data["dest"]["lat"]), float(data["dest"]["lon"])
    except (ValueError, KeyError, TypeError):
        await message.answer("Ma'lumot noto'g'ri. Xaritadan qaytadan tanlang.")
        return
    if not (valid_point(p_lat, p_lon) and valid_point(d_lat, d_lon)):
        await message.answer("Manzil xizmat hududidan tashqarida.")
        return

    km, minutes = await route(p_lat, p_lon, d_lat, d_lon)
    if km < MIN_TRIP_KM:
        await message.answer("Masofa juda qisqa. Boshqa manzil tanlang.")
        return

    settings = db.get_settings()
    price = calc_price(km, minutes, settings)
    oid = db.create_order(uid, p_lat, p_lon, d_lat, d_lon, round(km, 2), minutes, price, 0, 0)
    if oid is None:
        await message.answer("Sizda tasdiqlanmagan yoki faol buyurtma bor. Avval uni yakunlang yoki bekor qiling.")
        return
    await message.answer(
        f"🧾 Buyurtma #{oid}\n📏 Masofa: {km:.1f} km\n⏱ Taxminiy vaqt: {minutes} daqiqa\n"
        f"💰 Narx: {price:,} so'm (naqd)",
        reply_markup=kb([btn("✅ Buyurtma berish", f"confirm:{oid}"),
                         btn("❌ Bekor qilish", f"cancel:{oid}")]),
    )


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_order(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    if not db.confirm_order(oid, cb.from_user.id):
        await cb.answer("Buyurtma topilmadi yoki muddati tugagan.", show_alert=True)
        return
    await cb.message.edit_text(f"🔎 Buyurtma #{oid} qabul qilindi. Haydovchi qidirilmoqda...",
                               reply_markup=passenger_cancel_kb(oid))
    await cb.answer()
    await dispatch_next(bot, oid)


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_order(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    order = db.get_order(oid)
    if not order or order["passenger_id"] != cb.from_user.id:
        await cb.answer("Buyurtma topilmadi.", show_alert=True)
        return
    fee = db.cancel_order(oid, cb.from_user.id)
    if fee is None:
        await cb.answer("Bu buyurtmani endi bekor qilib bo'lmaydi.", show_alert=True)
        return
    db.cancel_offers(oid)
    await stop_live(bot, order)
    text = f"❌ Buyurtma #{oid} bekor qilindi."
    if fee:
        text += f"\nHaydovchi allaqachon yo'lga chiqqan: bekor qilish jarimasi {fee:,} so'm."
    await cb.message.edit_text(text)
    await cb.answer()
    if order["driver_id"]:
        await safe_send(bot, order["driver_id"], f"ℹ️ Yo'lovchi #{oid} buyurtmani bekor qildi.")


@router.message(F.text == "📜 Buyurtmalarim")
async def history(message: Message):
    rows = db.passenger_orders(message.from_user.id)
    if not rows:
        await message.answer("Sizda hali buyurtmalar yo'q.")
        return
    lines = [f"#{r['id']} · {r['price']:,} so'm · {r['status']} · {r['created_at'][:16]}" for r in rows]
    await message.answer("📜 Oxirgi buyurtmalar:\n" + "\n".join(lines))


@router.callback_query(F.data.startswith("rate:"))
async def rate_driver(cb: CallbackQuery):
    _, oid, stars = cb.data.split(":")
    ok = db.rate_order(int(oid), cb.from_user.id, int(stars))
    await cb.answer("Rahmat!" if ok else "Allaqachon baholangan.", show_alert=not ok)
    await cb.message.edit_reply_markup(reply_markup=kb([btn("⚠️ Shikoyat", f"complain:{oid}")]))


# ---------- Shikoyat ----------

@router.callback_query(F.data.startswith("complain:"))
async def complain_start(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":")[1])
    order = db.get_order(oid)
    uid = cb.from_user.id
    if not order or order["status"] != "done" or uid not in (order["passenger_id"], order["driver_id"]):
        await cb.answer("Bu safar bo'yicha shikoyat berib bo'lmaydi.", show_alert=True)
        return
    await state.set_state(Complaint.text)
    await state.update_data(order_id=oid)
    await cb.message.answer("Shikoyatingizni bitta xabarda yozing (bekor qilish: /cancel):")
    await cb.answer()


@router.message(Complaint.text, F.text)
async def complain_text(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    oid = data.get("order_id")
    order = db.get_order(oid) if oid else None
    await state.clear()
    if not order:
        await message.answer("Buyurtma topilmadi.")
        return
    uid = message.from_user.id
    against = order["driver_id"] if uid == order["passenger_id"] else order["passenger_id"]
    text = message.text[:1000]
    cid = db.add_complaint(oid, uid, against, text)
    await message.answer("Shikoyatingiz qabul qilindi. Admin ko'rib chiqadi.")
    for admin in ADMIN_IDS:
        await safe_send(bot, admin, f"⚠️ Shikoyat #{cid} (buyurtma #{oid})\n"
                                    f"Kimdan: {uid}\nKimga: {against}\n\n{text}")


# ---------- Haydovchi: ariza ----------

@router.message(Command("driver"))
async def driver_apply(message: Message, state: FSMContext):
    d = db.get_driver(message.from_user.id)
    if d:
        await message.answer(f"Sizning arizangiz: {STATUS_TEXT.get(d['status'], d['status'])}.")
        return
    await state.clear()
    await message.answer("Haydovchi sifatida ro'yxatdan o'tish.\nIsmingizni yozing:")
    await state.set_state(DriverApply.name)


@router.message(DriverApply.name, F.text)
async def apply_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 60:
        await message.answer("Ism 2 dan 60 gacha belgi bo'lishi kerak.")
        return
    await state.update_data(name=name)
    await message.answer("Mashina modeli va raqami (masalan: Nexia 3, 01 A 123 BC):")
    await state.set_state(DriverApply.car)


@router.message(DriverApply.car, F.text)
async def apply_car(message: Message, state: FSMContext):
    car = message.text.strip()
    if not 3 <= len(car) <= 60:
        await message.answer("Mashina ma'lumoti 3 dan 60 gacha belgi bo'lishi kerak.")
        return
    await state.update_data(car=car)
    await message.answer("Telefon raqamingiz (masalan: +998901234567):")
    await state.set_state(DriverApply.phone)


@router.message(DriverApply.phone, F.text)
async def apply_phone(message: Message, state: FSMContext):
    phone = re.sub(r"[\s\-()]", "", message.text)
    if not PHONE_RE.fullmatch(phone):
        await message.answer("Telefon noto'g'ri. Masalan: +998901234567")
        return
    if db.phone_taken(phone):
        await message.answer("Bu telefon bilan allaqachon ariza berilgan. Boshqa raqam yozing.")
        return
    await state.update_data(phone=phone)
    await message.answer("Haydovchilik guvohnomangizning rasmini yuboring 📷")
    await state.set_state(DriverApply.license)


@router.message(DriverApply.license, F.photo)
async def apply_license(message: Message, state: FSMContext):
    await state.update_data(license_file=message.photo[-1].file_id)
    await message.answer("Texpasport rasmini yuboring 📷")
    await state.set_state(DriverApply.techpass)


@router.message(DriverApply.techpass, F.photo)
async def apply_techpass(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    uid = message.from_user.id
    try:
        db.create_driver(uid, data["name"], data["phone"], data["car"],
                         data["license_file"], message.photo[-1].file_id)
    except sqlite3.IntegrityError:
        await message.answer("Bu telefon bilan ariza allaqachon mavjud.")
        await state.clear()
        return
    await state.clear()
    await message.answer("✅ Arizangiz adminga yuborildi. Tasdiqlanishini kuting.")
    d = db.get_driver(uid)
    for admin in ADMIN_IDS:
        try:
            await send_application(bot, admin, d)
        except TelegramAPIError:
            log.warning("Admin %s ga ariza yuborilmadi", admin, exc_info=True)


@router.message(DriverApply.license)
@router.message(DriverApply.techpass)
async def apply_need_photo(message: Message):
    await message.answer("Iltimos, rasm yuboring 📷")


# ---------- Haydovchi: ish ----------

def approved_driver(uid: int):
    d = db.get_driver(uid)
    return d if d and d["status"] == "approved" else None


@router.message(F.text == "🟢 Online")
async def go_online(message: Message):
    uid = message.from_user.id
    d = approved_driver(uid)
    if not d:
        await message.answer("Siz hali tasdiqlanmagansiz.")
        return
    limit = db.get_settings()["debt_limit"]
    if d["debt"] >= limit:
        await message.answer(f"Komissiya qarzingiz {d['debt']:,} so'm (chegara {limit:,}). "
                             "Qarzni to'lamaguncha buyurtma ololmaysiz.")
        return
    if d["last_lat"] is None:
        await message.answer("Avval 📍 lokatsiyangizni yuboring (📎 → Location).")
        return
    db.set_online(uid, 1)
    await message.answer(f"🟢 Siz onlaynsiz. Smena {MAX_ONLINE_HOURS} soatdan keyin avtomatik tugaydi.")


@router.message(F.text == "⚫ Offline")
async def go_offline(message: Message):
    uid = message.from_user.id
    if db.active_order_for_driver(uid):
        await message.answer("Faol buyurtma bor. Avval uni yakunlang.")
        return
    db.set_online(uid, 0)
    await message.answer("⚫ Siz offlinesiz.")


@router.message(F.text == "📊 Statistika")
async def driver_stats(message: Message):
    uid = message.from_user.id
    if not approved_driver(uid):
        await message.answer("Statistika faqat tasdiqlangan haydovchilar uchun.")
        return
    s = db.driver_stats(uid)
    d = db.get_driver(uid)
    await message.answer(
        f"📊 Statistika\n⭐ Reyting: {d['rating']:.2f} ({d['rating_count']} baho)\n"
        f"🚕 Bajarilgan safarlar: {s['cnt']}\n💵 Sof daromad: {s['total']:,} so'm\n"
        f"🧾 Komissiya qarzi: {s['debt']:,} so'm"
    )


@router.message(F.location)
@router.edited_message(F.location)
async def driver_location(message: Message, bot: Bot):
    uid = message.from_user.id
    if not approved_driver(uid):
        await message.answer("🚕 Taksi chaqirish uchun xarita tugmasini bosing.")
        return
    lat, lon = message.location.latitude, message.location.longitude
    if not valid_point(lat, lon):
        return
    db.update_driver_location(uid, lat, lon)
    order = db.active_order_for_driver(uid)
    if order:
        await forward_live(bot, order, lat, lon)


@router.callback_query(F.data.startswith("accept:"))
async def accept_order(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    if not approved_driver(uid):
        await cb.answer("Avval tasdiqlangan haydovchi bo'lishingiz kerak.", show_alert=True)
        return
    off = db.get_offer(oid, uid)
    if not off or off["status"] != "sent":
        await cb.answer("Bu taklif allaqachon yopilgan.", show_alert=True)
        return
    elapsed = (local_now() - datetime.fromisoformat(off["sent_at"])).total_seconds()
    if elapsed > OFFER_TIMEOUT_SEC + 5:
        db.set_offer_status(oid, uid, "expired")
        await cb.answer("Taklif muddati tugagan.", show_alert=True)
        await cb.message.edit_text(f"⌛ #{oid} taklifi muddati tugadi.")
        await dispatch_next(bot, oid)
        return
    if not db.accept_order(oid, uid):
        db.set_offer_status(oid, uid, "rejected")
        await cb.answer("Buyurtma allaqachon olingan yoki sizda boshqa faol buyurtma bor.", show_alert=True)
        return
    db.set_offer_status(oid, uid, "accepted")
    db.cancel_offers(oid)
    db.mark_accept(uid)
    order = db.get_order(oid)
    d = db.get_driver(uid)
    await cb.message.edit_text(f"✅ #{oid} buyurtma sizniki. Yo'lovchiga boring.\n"
                               "Telefon yoki 📍 jonli lokatsiya orqali bog'laning.")
    await cb.message.answer("Buyurtma boshqaruvi:", reply_markup=driver_order_kb(oid))
    await cb.answer()
    await safe_send(
        bot, order["passenger_id"],
        f"🚕 Haydovchi topildi!\nIsm: {d['name']}\nMashina: {d['car']}\nTel: {d['phone']}\n"
        f"💰 Narx: {order['price']:,} so'm (naqd)\n"
        "Haydovchi yo'lda. Shu chatga yozgan xabaringiz haydovchiga yetkaziladi.",
    )


@router.callback_query(F.data.startswith("reject:"))
async def reject_offer(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    off = db.get_offer(oid, uid)
    if off and off["status"] == "sent":
        db.set_offer_status(oid, uid, "rejected")
        await dispatch_next(bot, oid)
    await cb.message.edit_text(f"❌ #{oid} taklifini rad etdingiz.")
    await cb.answer()


@router.callback_query(F.data.startswith("dcancel:"))
async def driver_cancel(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    order = db.get_order(oid)
    if not order or order["driver_id"] != uid or order["status"] != "accepted":
        await cb.answer("Bu buyurtma sizda emas.", show_alert=True)
        return
    penalty = db.get_settings()["cancel_penalty"]
    await stop_live(bot, order)
    if not db.release_order(oid, penalty=penalty):
        await cb.answer("Buyurtmani bekor qilib bo'lmadi.", show_alert=True)
        return
    await cb.message.edit_text(f"❌ #{oid} bekor qildingiz. Jarima {penalty:,} so'm qarzingizga yozildi.")
    await cb.answer()
    await safe_send(bot, order["passenger_id"],
                    f"⚠️ Haydovchi buyurtma #{oid} ni bekor qildi. Yangi haydovchi qidirilmoqda...")
    await dispatch_next(bot, oid)


@router.callback_query(F.data.startswith("arrive:"))
async def arrived(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    if not db.mark_arrived(oid, uid):
        await cb.answer("Allaqachon belgilangan yoki bu buyurtma sizniki emas.", show_alert=True)
        return
    order = db.get_order(oid)
    free = db.get_settings()["wait_free_min"]
    await cb.answer("Yo'lovchiga xabar yuborildi.")
    await safe_send(bot, order["passenger_id"],
                    f"🚗 Haydovchi yetib keldi! Chiqishingiz mumkin. "
                    f"Kutish {free} daqiqagacha bepul, keyingi vaqt uchun qo'shimcha to'lanadi.")


@router.callback_query(F.data.startswith("done:"))
async def finish(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    order = db.finish_order(oid, uid)
    if not order:
        await cb.answer("Bu buyurtmani yakunlab bo'lmaydi.", show_alert=True)
        return
    await stop_live(bot, order)
    wait = f"\n⏳ Kutish haqi: {order['wait_fee']:,} so'm" if order["wait_fee"] else ""
    await cb.message.edit_text(
        f"🏁 Safar #{oid} yakunlandi\n💰 Jami: {order['price']:,} so'm{wait}\n"
        f"Sizning daromadingiz: {order['driver_earning']:,} so'm\n"
        f"Komissiya ({order['commission']:,} so'm) qarzingizga yozildi.")
    await cb.answer()
    await cb.message.answer("Yo'lovchini baholang:", reply_markup=kb(rating_row("prate", oid),
                                                                 [btn("⚠️ Shikoyat", f"complain:{oid}")]))
    await safe_send(bot, order["passenger_id"],
                    f"🏁 Safar yakunlandi #{oid}\n💰 To'lov: {order['price']:,} so'm (naqd){wait}\n"
                    "Haydovchini baholang:", reply_markup=done_kb_passenger(oid))


@router.callback_query(F.data.startswith("prate:"))
async def rate_passenger(cb: CallbackQuery):
    _, oid, stars = cb.data.split(":")
    ok = db.rate_passenger(int(oid), cb.from_user.id, int(stars))
    await cb.answer("Rahmat!" if ok else "Allaqachon baholangan.", show_alert=not ok)
    await cb.message.edit_reply_markup(reply_markup=kb([btn("⚠️ Shikoyat", f"complain:{oid}")]))


# ---------- Admin ----------

def admin_kb():
    return kb(
        [btn("📥 Arizalar", "adm:pending:0"), btn("👥 Haydovchilar", "adm:drivers:0")],
        [btn("📋 Faol buyurtmalar", "adm:orders"), btn("⚠️ Shikoyatlar", "adm:complaints")],
        [btn("💳 Qarzlar", "adm:debts"), btn("⚙️ Narxlar", "adm:prices")],
        [btn("📊 Bugun", "adm:report:1"), btn("📊 7 kun", "adm:report:7"), btn("📊 30 kun", "adm:report:30")],
    )


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
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in DEFAULT_SETTINGS:
        await message.answer("Format: /setprice <kalit> <qiymat>\nKalitlar: " + ", ".join(DEFAULT_SETTINGS))
        return
    key = parts[1]
    try:
        value = int(parts[2])
    except ValueError:
        await message.answer("Qiymat butun son bo'lishi kerak.")
        return
    if key == "commission_percent" and not 0 <= value <= 100:
        await message.answer("Komissiya 0 dan 100 gacha bo'lishi kerak.")
        return
    if value < 0:
        await message.answer("Qiymat manfiy bo'lmasligi kerak.")
        return
    db.set_setting(key, value)
    await message.answer(f"✅ {key} = {value}")


@router.message(Command("debt"))
async def pay_debt_cmd(message: Message, bot: Bot):
    """/debt <haydovchi_id> <summa> — haydovchi komissiyani to'laganda."""
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Format: /debt <haydovchi_id> <summa>")
        return
    left = db.pay_debt(int(parts[1]), int(parts[2]))
    if left is None:
        await message.answer("Haydovchi topilmadi.")
        return
    await message.answer(f"✅ To'lov qabul qilindi. Qolgan qarz: {left:,} so'm")
    await safe_send(bot, int(parts[1]), f"💳 {int(parts[2]):,} so'm qabul qilindi. Qolgan qarz: {left:,} so'm")


def _since(days: int) -> datetime:
    today = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1)


def _driver_list_text(rows, title):
    if not rows:
        return f"{title}: bo'sh"
    return title + "\n" + "\n".join(f"{r['tg_id']} · {r['name']} · {r['car']} · {r['phone']}" for r in rows)


async def _release_driver_orders(bot: Bot, order_ids: list[int]):
    for oid in order_ids:
        order = db.get_order(oid)
        await stop_live(bot, order)
        if db.release_order(oid):
            await safe_send(bot, order["passenger_id"],
                            f"⚠️ Haydovchi bilan bog'liq muammo. Buyurtma #{oid} uchun yangi haydovchi qidirilmoqda.")
            await dispatch_next(bot, oid)


@router.callback_query(F.data.startswith("adm:"))
async def admin_action(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q.", show_alert=True)
        return
    parts = cb.data.split(":")
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    await cb.answer()

    if action in ("approve", "reject", "block", "unblock"):
        tg = int(arg)
        if not db.get_driver(tg):
            await cb.message.answer("Haydovchi topilmadi.")
            return
        status = {"approve": "approved", "reject": "rejected",
                  "block": "blocked", "unblock": "approved"}[action]
        released = db.set_driver_status(tg, status)
        await _release_driver_orders(bot, released)
        notify = {
            "approved": "✅ Arizangiz tasdiqlandi! /start bosing, so'ng 🟢 Online ni yoqing.",
            "rejected": "❌ Arizangiz rad etildi.",
            "blocked": "🚫 Hisobingiz bloklandi.",
        }[status]
        await safe_send(bot, tg, notify)
        await cb.message.answer(f"Haydovchi {tg}: {STATUS_TEXT[status]}")
        return

    if action == "pending":
        off = int(arg)
        rows = db.list_drivers(("pending",), off, PAGE)
        total = db.count_drivers(("pending",))
        if not rows:
            await cb.message.answer("Yangi arizalar yo'q.")
            return
        rows_kb = [[btn(f"✅ {r['name']}", f"adm:approve:{r['tg_id']}"),
                    btn("❌", f"adm:reject:{r['tg_id']}")] for r in rows]
        nav = []
        if off > 0:
            nav.append(btn("⬅️", f"adm:pending:{max(0, off - PAGE)}"))
        if off + PAGE < total:
            nav.append(btn("➡️", f"adm:pending:{off + PAGE}"))
        if nav:
            rows_kb.append(nav)
        await cb.message.answer(f"📥 Arizalar ({total}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb))
        return

    if action == "drivers":
        off = int(arg)
        statuses = ("approved", "blocked")
        rows = db.list_drivers(statuses, off, PAGE)
        total = db.count_drivers(statuses)
        rows_kb = []
        for r in rows:
            if r["status"] == "approved":
                rows_kb.append([btn(f"🚫 {r['name']}", f"adm:block:{r['tg_id']}")])
            else:
                rows_kb.append([btn(f"♻️ {r['name']}", f"adm:unblock:{r['tg_id']}")])
        nav = []
        if off > 0:
            nav.append(btn("⬅️", f"adm:drivers:{max(0, off - PAGE)}"))
        if off + PAGE < total:
            nav.append(btn("➡️", f"adm:drivers:{off + PAGE}"))
        if nav:
            rows_kb.append(nav)
        await cb.message.answer(_driver_list_text(rows, f"👥 Haydovchilar ({total})"),
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb) if rows_kb else None)
        return

    if action == "orders":
        rows = db.active_orders()
        if not rows:
            await cb.message.answer("Faol buyurtmalar yo'q.")
            return
        for r in rows:
            text = (f"#{r['id']} · {r['status']} · {r['price']:,} so'm\n"
                    f"Yo'lovchi: {r['passenger_id']} · Haydovchi: {r['driver_id'] or '-'}")
            await cb.message.answer(text, reply_markup=kb([btn("❌ Bekor qilish", f"adm:cancelorder:{r['id']}")]))
        return

    if action == "cancelorder":
        oid = int(arg)
        old = db.admin_cancel(oid)
        if not old:
            await cb.message.answer("Buyurtma topilmadi yoki tugagan.")
            return
        db.cancel_offers(oid)
        await stop_live(bot, old)
        await cb.message.answer(f"✅ #{oid} admin tomonidan bekor qilindi.")
        await safe_send(bot, old["passenger_id"], f"ℹ️ Buyurtma #{oid} administrator tomonidan bekor qilindi.")
        if old["driver_id"]:
            await safe_send(bot, old["driver_id"], f"ℹ️ Buyurtma #{oid} administrator tomonidan bekor qilindi.")
        return

    if action == "complaints":
        rows = db.open_complaints()
        if not rows:
            await cb.message.answer("Ochiq shikoyatlar yo'q.")
            return
        for r in rows:
            await cb.message.answer(
                f"⚠️ #{r['id']} (buyurtma #{r['order_id']})\nKimdan: {r['from_id']} · Kimga: {r['against_id']}\n\n{r['text']}",
                reply_markup=kb([btn("✅ Yopish", f"adm:closec:{r['id']}")]))
        return

    if action == "closec":
        ok = db.close_complaint(int(arg))
        await cb.message.answer("Shikoyat yopildi." if ok else "Topilmadi yoki allaqachon yopilgan.")
        return

    if action == "debts":
        rows = db.debtors()
        if not rows:
            await cb.message.answer("Qarzdor haydovchilar yo'q.")
            return
        lines = [f"{r['tg_id']} · {r['name']} · {r['debt']:,} so'm" for r in rows]
        await cb.message.answer("💳 Qarzlar:\n" + "\n".join(lines) +
                                "\n\nTo'lovni qayd etish: /debt <haydovchi_id> <summa>")
        return

    if action == "prices":
        s = db.get_settings()
        lines = [f"{k} = {v}" for k, v in s.items()]
        await cb.message.answer("⚙️ Sozlamalar:\n" + "\n".join(lines) +
                                "\n\nO'zgartirish: /setprice <kalit> <qiymat>")
        return

    if action == "report":
        days = int(arg)
        cnt, total, comm = db.report(_since(days))
        await cb.message.answer(
            f"📊 So'nggi {days} kun\nSafarlar: {cnt}\nUmumiy summa: {total:,} so'm\n"
            f"Komissiya: {comm:,} so'm")
        return


# ---------- Xabar almashish (yo'lovchi <-> haydovchi) ----------

@router.message(F.text)
async def relay_or_menu(message: Message, bot: Bot):
    uid = message.from_user.id
    o = db.active_order_for_passenger(uid)
    if o and o["driver_id"]:
        await safe_send(bot, o["driver_id"], f"💬 Yo'lovchi (#{o['id']}):\n{message.text}")
        await message.answer("✉️ Haydovchiga yuborildi.")
        return
    o = db.active_order_for_driver(uid)
    if o:
        await safe_send(bot, o["passenger_id"], f"💬 Haydovchi (#{o['id']}):\n{message.text}")
        return
    await message.answer("Menyudan tanlang.", reply_markup=main_menu(uid))
