import json
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from config import ADMIN_IDS, DEFAULT_SETTINGS, LIVE_PERIOD, MIN_TRIP_KM
from keyboards import main_menu
from pricing import calc_price, distance_km

router = Router()

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


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def approved_driver(uid: int):
    d = db.get_driver(uid)
    return d if d and d["status"] == "approved" else None


def rate_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{i}⭐", callback_data=f"rate:{order_id}:{i}") for i in range(1, 6)
    ]])


async def stop_live(bot: Bot, order):
    if order and order["live_msg_id"]:
        try:
            await bot.stop_message_live_location(chat_id=order["passenger_id"],
                                                 message_id=order["live_msg_id"])
        except Exception:
            pass


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
        dl, do = float(data["dest"]["lat"]), float(data["dest"]["lon"])
    except (KeyError, TypeError, ValueError):
        await message.answer("Ma'lumot noto'g'ri keldi. Qaytadan urinib ko'ring.")
        return

    km = distance_km(pl, po, dl, do)
    if km < MIN_TRIP_KM:
        await message.answer("Borish joyi juda yaqin. Xaritada boshqa nuqtani belgilang.")
        return

    settings = db.get_settings()
    price, minutes = calc_price(km, settings)
    commission = round(price * settings["commission_percent"] / 100)
    order_id = db.create_order(message.from_user.id, pl, po, dl, do, km, minutes, price,
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
    o = db.get_order(order_id)
    if not o or o["passenger_id"] != cb.from_user.id or o["status"] != "new":
        await cb.answer("Buyurtma topilmadi.", show_alert=True)
        return

    await cb.message.edit_text(f"Buyurtma #{order_id} yuborildi. Haydovchi qidirilmoqda... 🔍")
    await cb.answer()

    text = (f"🚕 Yangi buyurtma #{order_id}\n"
            f"Masofa: {o['km']:.1f} km, {o['minutes']} daqiqa\n"
            f"Narx: {o['price']:,} so'm\nSiz olasiz: {o['driver_earning']:,} so'm (💵 naqd)")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"accept:{order_id}"),
    ]])
    for driver_id in db.online_driver_ids():
        try:
            await bot.send_message(driver_id, text, reply_markup=kb)
            await bot.send_location(driver_id, o["pickup_lat"], o["pickup_lon"])
        except Exception:
            pass  # haydovchi botni bloklagan bo'lishi mumkin


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
        await bot.send_message(o["driver_id"], f"⚠️ Yo'lovchi #{order_id} buyurtmani bekor qildi.")


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
        await cb.message.edit_text(f"Rahmat! Bahoyingiz: {stars}⭐")
        await cb.answer()
    else:
        await cb.answer("Baholash mumkin emas.", show_alert=True)


# ---------- Haydovchi: ariza ----------

@router.message(Command("driver"))
async def driver_apply(message: Message, state: FSMContext):
    d = db.get_driver(message.from_user.id)
    if d:
        await message.answer(
            f"Arizangiz holati: {STATUS_TEXT[d['status']]}",
            reply_markup=main_menu(message.from_user.id),
        )
        return
    await state.set_state(DriverApply.name)
    await message.answer("Haydovchi bo'lish uchun ariza. Ismingizni yozing:")


@router.message(DriverApply.name, F.text)
async def apply_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(DriverApply.car)
    await message.answer("Mashina modeli va davlat raqami (masalan: Nexia 3, 01 A 123 BC):")


@router.message(DriverApply.car, F.text)
async def apply_car(message: Message, state: FSMContext):
    await state.update_data(car=message.text.strip())
    await state.set_state(DriverApply.phone)
    await message.answer("Telefon raqamingiz (masalan: +998901234567):")


@router.message(DriverApply.phone, F.text)
async def apply_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
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
    db.create_driver(message.from_user.id, data["name"], data["phone"], data["car"],
                     data["license_file"], message.photo[-1].file_id)
    d = db.get_driver(message.from_user.id)
    for admin_id in ADMIN_IDS:
        await send_application(bot, admin_id, d)
    await message.answer("Arizangiz yuborildi ⏳ Tasdiqlangach xabar beramiz.",
                         reply_markup=main_menu(message.from_user.id))


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
        pass


# ---------- Haydovchi: ish ----------

@router.message(F.text == "🟢 Online")
async def go_online(message: Message):
    if not approved_driver(message.from_user.id):
        await message.answer("Arizangiz hali tasdiqlanmagan.")
        return
    db.set_online(message.from_user.id, 1)
    await message.answer("Siz online. Yangi buyurtmalar keladi.")


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
        f"Jami daromad: {s['total']:,} so'm (naqd)"
    )


@router.callback_query(F.data.startswith("accept:"))
async def accept_order(cb: CallbackQuery, bot: Bot):
    d = approved_driver(cb.from_user.id)
    if not d:
        await cb.answer("Avval tasdiqlangan haydovchi bo'ling.", show_alert=True)
        return
    order_id = int(cb.data.split(":")[1])
    if not db.accept_order(order_id, cb.from_user.id):
        await cb.answer("Bu buyurtma allaqachon olingan yoki bekor qilingan.", show_alert=True)
        return

    o = db.get_order(order_id)
    done_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏁 Tugatish", callback_data=f"done:{order_id}"),
    ]])
    await cb.message.edit_text(
        f"✅ #{order_id} buyurtma sizda.\n"
        f"Yo'lovchiga borish uchun jonli lokatsiyani ulang:\n"
        f"📎 > Lokatsiya > Jonli lokatsiya ulashish",
        reply_markup=done_kb,
    )
    await cb.answer()

    await bot.send_message(
        o["passenger_id"],
        f"Haydovchi topildi!\nIsm: {d['name']}\nMashina: {d['car']}\n"
        f"Reyting: {d['rating']:.1f} ⭐\nNarx: {o['price']:,} so'm (💵 naqd)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel:{order_id}"),
        ]]),
    )
    await bot.send_location(cb.from_user.id, o["dest_lat"], o["dest_lon"])


@router.callback_query(F.data.startswith("done:"))
async def finish(cb: CallbackQuery, bot: Bot):
    order_id = int(cb.data.split(":")[1])
    if not db.finish_order(order_id, cb.from_user.id):
        await cb.answer("Buyurtma topilmadi.", show_alert=True)
        return
    o = db.get_order(order_id)
    await stop_live(bot, o)
    await cb.message.edit_text(f"🏁 #{order_id} yakunlandi. {o['price']:,} so'mni naqd oling.")
    await cb.answer()
    await bot.send_message(o["passenger_id"],
                           f"Safar yakunlandi. Narx: {o['price']:,} so'm (💵 naqd).\n"
                           f"Haydovchini baholang:",
                           reply_markup=rate_kb(order_id))


# ---------- Jonli kuzatuv ----------

async def forward_live(message: Message, bot: Bot):
    """Haydovchining jonli lokatsiyasini yo'lovchiga uzatadi."""
    o = db.active_order_for_driver(message.from_user.id)
    if not o:
        return
    lat, lon = message.location.latitude, message.location.longitude
    if o["live_msg_id"] is None:
        sent = await bot.send_location(o["passenger_id"], lat, lon, live_period=LIVE_PERIOD)
        db.set_live_msg(o["id"], sent.message_id)
    else:
        try:
            await bot.edit_message_live_location(
                lat, lon, chat_id=o["passenger_id"], message_id=o["live_msg_id"])
        except Exception:
            pass  # xabar muddati tugagan bo'lishi mumkin


@router.message(F.location)
async def driver_location(message: Message, bot: Bot):
    if message.location.live_period:
        await forward_live(message, bot)


@router.edited_message(F.location)
async def driver_location_update(message: Message, bot: Bot):
    # Jonli lokatsiya yangilanishlari edited_message sifatida keladi
    await forward_live(message, bot)


# ---------- Admin ----------

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Arizalar", callback_data="adm:pending")],
        [InlineKeyboardButton(text="🚖 Haydovchilar", callback_data="adm:drivers")],
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
    db.set_setting(parts[1], int(parts[2]))
    await message.answer(f"{parts[1]} = {parts[2]} saqlandi ✅")


def report_text() -> str:
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lines = ["📊 Hisobot (faqat bajarilgan buyurtmalar):"]
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

    if action in ("approve", "reject", "block", "unblock"):
        target = int(parts[2])
        if not db.get_driver(target):
            await cb.answer("Haydovchi topilmadi.", show_alert=True)
            return
        new_status = {"approve": "approved", "reject": "rejected",
                      "block": "blocked", "unblock": "approved"}[action]
        db.set_driver_status(target, new_status)
        notice = {
            "approved": "✅ Ariza tasdiqlandi! 🟢 Online ni bosib ishni boshlang.",
            "rejected": "❌ Arizangiz rad etildi.",
            "blocked": "🚫 Hisobingiz bloklandi.",
        }[new_status]
        try:
            await bot.send_message(target, notice, reply_markup=main_menu(target))
        except Exception:
            pass
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.answer(f"Holat: {STATUS_TEXT[new_status]}")
        return

    if action == "pending":
        rows = db.list_drivers(("pending",))
        if not rows:
            await bot.send_message(cb.from_user.id, "Yangi arizalar yo'q.")
        for d in rows:
            await send_application(bot, cb.from_user.id, d)
        await cb.answer()
        return

    if action == "drivers":
        rows = db.list_drivers(("approved", "blocked"))
        if not rows:
            await bot.send_message(cb.from_user.id, "Haydovchilar yo'q.")
        for d in rows:
            if d["status"] == "approved":
                btn = InlineKeyboardButton(text="🚫 Bloklash", callback_data=f"adm:block:{d['tg_id']}")
            else:
                btn = InlineKeyboardButton(text="♻️ Blokdan chiqarish",
                                           callback_data=f"adm:unblock:{d['tg_id']}")
            await bot.send_message(
                cb.from_user.id,
                f"{d['name']} | {d['car']} | ⭐ {d['rating']:.1f} ({d['rating_count']})\n"
                f"Holat: {STATUS_TEXT[d['status']]}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]),
            )
        await cb.answer()
        return

    if action == "stats":
        await bot.send_message(cb.from_user.id, report_text())
        await cb.answer()
        return

    if action == "prices":
        s = db.get_settings()
        text = "💲 Joriy narxlar:\n" + "\n".join(f"{k}: {v}" for k, v in s.items())
        text += "\n\nO'zgartirish: /setprice <kalit> <qiymat>\nMasalan: /setprice base_price 6000"
        await bot.send_message(cb.from_user.id, text)
        await cb.answer()
        return

    await cb.answer()


# ---------- Qolgan xabarlar ----------

@router.message(F.text == "🚕 Taksi chaqirish")
async def webapp_hint(message: Message):
    await message.answer("Xarita ochilishi uchun 🚕 tugmasini pastdagi menyudan bosing.")
