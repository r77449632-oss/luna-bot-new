from math import asin, cos, radians, sin, sqrt

from config import AVG_SPEED_KMH

ROAD_FACTOR = 1.3  # to'g'ri chiziq masofasini taxminiy yo'l masofasiga aylantirish


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine formulasi bo'yicha taxminiy yo'l masofasi (km).

    Haqiqiy yo'l uchun OSRM yoki Google Directions API ulash mumkin.
    """
    r = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a)) * ROAD_FACTOR


def calc_price(km: float, s: dict) -> tuple[int, int]:
    """Narx (so'm) va taxminiy vaqt (daqiqa) ni qaytaradi."""
    minutes = max(5, round(km / AVG_SPEED_KMH * 60))
    price = s["base_price"] + km * s["price_per_km"] + minutes * s["price_per_min"]
    return max(s["min_price"], round(price, -2)), minutes
