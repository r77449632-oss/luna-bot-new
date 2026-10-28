import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DB_PATH = os.getenv("DB_PATH", "taxi.db")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

LIVE_PERIOD = 1800        # jonli lokatsiya necha soniya ko'rinadi
AVG_SPEED_KMH = 25        # shahar ichida o'rtacha tezlik
MIN_TRIP_KM = 0.3         # bundan qisqa safar qabul qilinmaydi

# Boshlang'ich narxlar. Keyinchalik admin /setprice orqali o'zgartiradi (bazada saqlanadi).
DEFAULT_SETTINGS = {
    "base_price": 5000,
    "price_per_km": 2000,
    "price_per_min": 100,
    "min_price": 8000,
    "commission_percent": 10,   # platforma komissiyasi, foizda
}
