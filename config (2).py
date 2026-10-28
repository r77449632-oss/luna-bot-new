import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DB_PATH = os.getenv("DB_PATH", "taxi.db")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))       # hisobot va vaqt uchun
OSRM_BASE = os.getenv("OSRM_BASE", "https://router.project-osrm.org")  # o'z serveringiz bo'lsa shu yerga

LIVE_PERIOD = 1800          # jonli lokatsiya necha soniya ko'rinadi
AVG_SPEED_KMH = 25          # marshrut xizmati ishlamasa, taxminiy tezlik
MIN_TRIP_KM = 0.3
ROUTE_TIMEOUT_SEC = 5

OFFER_TIMEOUT_SEC = 40      # haydovchi taklifga javob berish muddati
SEARCH_TIMEOUT_SEC = 300    # haydovchi topish uchun umumiy muddat
DRAFT_TIMEOUT_SEC = 900     # tasdiqlanmagan buyurtma muddati
TICK_SEC = 5                # navbat tekshiruvi oralig'i
MAX_ONLINE_HOURS = 12       # haydovchi shu soatdan keyin avtomatik offline bo'ladi

BACKUP_DIR = "backups"
BACKUP_KEEP = 14            # necha kunlik nusxa saqlansin
LOG_DIR = "logs"
LOCK_PORT = 47831           # bir vaqtda ikki nusxa ishlamasligi uchun

# Ruxsat etilgan hudud. Shu chegaradan tashqaridagi nuqta qabul qilinmaydi.
LAT_RANGE = (37.0, 46.0)
LON_RANGE = (55.9, 73.2)

# Admin /setprice orqali o'zgartira oladigan sozlamalar (bazada saqlanadi)
DEFAULT_SETTINGS = {
    "base_price": 5000,
    "price_per_km": 2000,
    "price_per_min": 100,
    "min_price": 8000,
    "commission_percent": 10,
    "cancel_fee": 5000,          # yo'lovchi haydovchi qabul qilgandan keyin bekor qilsa
    "cancel_penalty": 5000,      # haydovchi qabul qilgandan keyin bekor qilsa, qarziga qo'shiladi
    "wait_free_min": 5,          # haydovchi kutishining bepul daqiqalari
    "wait_price_per_min": 200,   # bepul vaqtdan keyin har daqiqa uchun
    "debt_limit": 100000,        # komissiya qarzi shu summaga yetsa, haydovchi buyurtma ololmaydi
}
