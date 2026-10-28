import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DB_PATH = os.getenv("DB_PATH", "taxi.db")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

LIVE_PERIOD = 1800          # jonli lokatsiya necha soniya ko'rinadi
AVG_SPEED_KMH = 25          # marshrut xizmati ishlamasa, taxminiy tezlik
MIN_TRIP_KM = 0.3

OFFER_TIMEOUT_SEC = 40      # haydovchi taklifga javob berish muddati
SEARCH_TIMEOUT_SEC = 300    # haydovchi topish uchun umumiy muddat
DRAFT_TIMEOUT_SEC = 900     # tasdiqlanmagan buyurtma muddati
TICK_SEC = 5                # navbat tekshiruvi oralig'i
ROUTE_TIMEOUT_SEC = 5       # marshrut so'rovi uchun kutish vaqti

# Ruxsat etilgan hudud. Shu chegaradan tashqaridagi nuqta qabul qilinmaydi.
LAT_RANGE = (37.0, 46.0)
LON_RANGE = (55.9, 73.2)

DEFAULT_SETTINGS = {
    "base_price": 5000,
    "price_per_km": 2000,
    "price_per_min": 100,
    "min_price": 8000,
    "commission_percent": 10,
}
