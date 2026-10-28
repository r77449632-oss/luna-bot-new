import logging
from math import asin, cos, radians, sin, sqrt

import aiohttp

from config import AVG_SPEED_KMH, ROUTE_TIMEOUT_SEC

log = logging.getLogger("taxi.pricing")

ROAD_FACTOR = 1.3
OSRM_URL = ("https://router.project-osrm.org/route/v1/driving/"
            "{lon1},{lat1};{lon2},{lat2}?overview=false")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """To'g'ri chiziq masofasi (Haversine), yo'l uchun ROAD_FACTOR ga ko'paytiriladi."""
    r = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a)) * ROAD_FACTOR


async def route(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, int]:
    """Haqiqiy yo'l bo'yicha (km, daqiqa). Xizmat javob bermasa, taxminiy hisob qaytadi."""
    url = OSRM_URL.format(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2)
    try:
        timeout = aiohttp.ClientTimeout(total=ROUTE_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json(content_type=None)
        if data.get("code") == "Ok":
            leg = data["routes"][0]
            return leg["distance"] / 1000, max(5, round(leg["duration"] / 60))
    except Exception:
        log.warning("Marshrut xizmati javob bermadi, taxminiy hisob ishlatilyapti", exc_info=True)
    km = distance_km(lat1, lon1, lat2, lon2)
    return km, max(5, round(km / AVG_SPEED_KMH * 60))


def calc_price(km: float, minutes: int, s: dict) -> int:
    price = s["base_price"] + km * s["price_per_km"] + minutes * s["price_per_min"]
    return int(max(s["min_price"], round(price, -2)))
