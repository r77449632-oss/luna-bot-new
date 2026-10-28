from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

import db
from config import ADMIN_IDS, WEBAPP_URL


def main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🚕 Taksi chaqirish", web_app=WebAppInfo(url=WEBAPP_URL))],
        [KeyboardButton(text="📜 Buyurtmalarim")],
    ]
    driver = db.get_driver(uid)
    if driver and driver["status"] == "approved":
        rows.append([KeyboardButton(text="🟢 Online"), KeyboardButton(text="⚫ Offline")])
        rows.append([KeyboardButton(text="📊 Statistika")])
    if uid in ADMIN_IDS:
        rows.append([KeyboardButton(text="🛠 Admin")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
