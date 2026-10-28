import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from config import DB_PATH, DEFAULT_SETTINGS

log = logging.getLogger("taxi.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS drivers (
    tg_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    car TEXT,
    license_file TEXT,
    techpass_file TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending, approved, rejected, blocked
    online INTEGER NOT NULL DEFAULT 0,
    rating REAL NOT NULL DEFAULT 5.0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger_id INTEGER NOT NULL,
    driver_id INTEGER,
    pickup_lat REAL,
    pickup_lon REAL,
    dest_lat REAL,
    dest_lon REAL,
    km REAL,
    minutes INTEGER,
    price INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',     -- draft, new, accepted, done, cancelled, expired
    live_msg_id INTEGER,
    rated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    driver_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent'      -- sent, rejected, expired, accepted
);
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    rating REAL NOT NULL DEFAULT 5.0,
    rating_count INTEGER NOT NULL DEFAULT 0
);
"""

# Eski bazaga qo'shiladigan yangi ustunlar
NEW_COLUMNS = {
    "orders": {
        "commission": "INTEGER NOT NULL DEFAULT 0",
        "driver_earning": "INTEGER NOT NULL DEFAULT 0",
        "driver_rated": "INTEGER NOT NULL DEFAULT 0",
        "search_started_at": "TEXT",
        "done_at": "TEXT",
    },
    "drivers": {
        "last_lat": "REAL",
        "last_lon": "REAL",
        "last_accept_at": "TEXT",
    },
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ago(seconds: int) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(c):
    for table, cols in NEW_COLUMNS.items():
        existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # Eski ma'lumotni to'g'rilash
    c.execute("UPDATE orders SET driver_earning = price "
              "WHERE status='done' AND driver_earning=0 AND commission=0")
    c.execute("UPDATE orders SET done_at = created_at WHERE status='done' AND done_at IS NULL")
    # Eski "new" buyurtmalar qidiruv vaqtisiz qolgan: ularni muddati tugagan deb belgilaymiz
    c.execute("UPDATE orders SET status='expired' WHERE status='new' AND search_started_at IS NULL")
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_drivers_phone ON drivers(phone)")
    except sqlite3.IntegrityError:
        log.warning("Bazada takroriy telefon raqamlar bor, unique index qo'yilmadi")


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        _ensure_columns(c)


# ---------- Sozlamalar ----------

def get_settings() -> dict:
    with connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}


def set_setting(key: str, value: int):
    with connect() as c:
        c.execute("UPDATE settings SET value=? WHERE key=?", (value, key))


# ---------- Haydovchilar ----------

def get_driver(tg_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM drivers WHERE tg_id=?", (tg_id,)).fetchone()


def phone_taken(phone: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM drivers WHERE phone=?", (phone,)).fetchone() is not None


def create_driver(tg_id, name, phone, car, license_file, techpass_file):
    with connect() as c:
        c.execute(
            """INSERT INTO drivers (tg_id, name, phone, car, license_file, techpass_file,
                                    status, online, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)""",
            (tg_id, name, phone, car, license_file, techpass_file, now()),
        )


def set_driver_status(tg_id: int, status: str) -> list[int]:
    """Holatni o'zgartiradi. Bloklansa yoki rad etilsa, uning faol buyurtma ID lari qaytadi."""
    with connect() as c:
        order_ids = []
        if status in ("blocked", "rejected"):
            order_ids = [r["id"] for r in c.execute(
                "SELECT id FROM orders WHERE driver_id=? AND status='accepted'", (tg_id,))]
        c.execute("UPDATE drivers SET status=?, online=0 WHERE tg_id=?", (status, tg_id))
        return order_ids


def set_online(tg_id: int, value: int) -> bool:
    with connect() as c:
        cur = c.execute("UPDATE drivers SET online=? WHERE tg_id=? AND status='approved'",
                        (value, tg_id))
        return cur.rowcount == 1


def update_driver_location(tg_id: int, lat: float, lon: float):
    with connect() as c:
        c.execute("UPDATE drivers SET last_lat=?, last_lon=? WHERE tg_id=? AND status='approved'",
                  (lat, lon, tg_id))


def mark_accept(tg_id: int):
    with connect() as c:
        c.execute("UPDATE drivers SET last_accept_at=? WHERE tg_id=?", (now(), tg_id))


def candidate_drivers(order_id: int):
    """Navbatga kira oladigan haydovchilar: online, bo'sh, joyi ma'lum, bu buyurtmaga hali taklif olmagan."""
    with connect() as c:
        return c.execute(
            """SELECT d.* FROM drivers d
               WHERE d.status='approved' AND d.online=1 AND d.last_lat IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM orders o
                                 WHERE o.driver_id=d.tg_id AND o.status='accepted')
                 AND NOT EXISTS (SELECT 1 FROM offers f
                                 WHERE f.order_id=? AND f.driver_id=d.tg_id)""",
            (order_id,),
        ).fetchall()


def list_drivers(statuses: tuple, offset: int, limit: int):
    placeholders = ",".join("?" * len(statuses))
    with connect() as c:
        return c.execute(
            f"SELECT * FROM drivers WHERE status IN ({placeholders}) "
            f"ORDER BY created_at LIMIT ? OFFSET ?",
            (*statuses, limit, offset),
        ).fetchall()


def count_drivers(statuses: tuple) -> int:
    placeholders = ",".join("?" * len(statuses))
    with connect() as c:
        return c.execute(f"SELECT COUNT(*) FROM drivers WHERE status IN ({placeholders})",
                         statuses).fetchone()[0]


def driver_stats(driver_id: int):
    """Haydovchining sof daromadi (komissiyadan keyin)."""
    with connect() as c:
        return c.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(driver_earning), 0) AS total FROM orders "
            "WHERE driver_id=? AND status='done'",
            (driver_id,),
        ).fetchone()


# ---------- Buyurtmalar ----------

def create_order(passenger_id, p_lat, p_lon, d_lat, d_lon, km, minutes, price,
                 commission, driver_earning) -> int:
    """Buyurtma 'draft' holatida yaratiladi, yo'lovchi tasdiqlagach 'new' bo'ladi."""
    with connect() as c:
        cur = c.execute(
            """INSERT INTO orders (passenger_id, pickup_lat, pickup_lon, dest_lat, dest_lon,
                                   km, minutes, price, commission, driver_earning,
                                   status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
            (passenger_id, p_lat, p_lon, d_lat, d_lon, km, minutes, price,
             commission, driver_earning, now()),
        )
        return cur.lastrowid


def confirm_order(order_id: int, passenger_id: int) -> bool:
    """Atomik: buyurtma faqat bir marta yuboriladi."""
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET status='new', search_started_at=? "
            "WHERE id=? AND passenger_id=? AND status='draft'",
            (now(), order_id, passenger_id),
        )
        return cur.rowcount == 1


def get_order(order_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def accept_order(order_id: int, driver_id: int) -> bool:
    """Haydovchi bo'sh bo'lsa va buyurtma hali 'new' bo'lsa, qabul qiladi."""
    with connect() as c:
        busy = c.execute("SELECT 1 FROM orders WHERE driver_id=? AND status='accepted'",
                         (driver_id,)).fetchone()
        if busy:
            return False
        cur = c.execute(
            "UPDATE orders SET driver_id=?, status='accepted' WHERE id=? AND status='new'",
            (driver_id, order_id),
        )
        return cur.rowcount == 1


def release_order(order_id: int) -> bool:
    """Haydovchi bekor qilganda buyurtma qayta 'new' holatiga qaytadi."""
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET driver_id=NULL, status='new', search_started_at=? "
            "WHERE id=? AND status='accepted'",
            (now(), order_id),
        )
        return cur.rowcount == 1


def finish_order(order_id: int, driver_id: int) -> bool:
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET status='done', done_at=? "
            "WHERE id=? AND driver_id=? AND status='accepted'",
            (now(), order_id, driver_id),
        )
        return cur.rowcount == 1


def cancel_order(order_id: int, passenger_id: int) -> bool:
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET status='cancelled' WHERE id=? AND passenger_id=? "
            "AND status IN ('draft', 'new', 'accepted')",
            (order_id, passenger_id),
        )
        return cur.rowcount == 1


def expire_order(order_id: int) -> bool:
    with connect() as c:
        cur = c.execute("UPDATE orders SET status='expired' WHERE id=? AND status='new'",
                        (order_id,))
        return cur.rowcount == 1


def expire_drafts(cutoff: str):
    with connect() as c:
        c.execute("UPDATE orders SET status='expired' WHERE status='draft' AND created_at<?",
                  (cutoff,))


def stale_search_orders(cutoff: str):
    with connect() as c:
        return c.execute(
            "SELECT id, passenger_id FROM orders WHERE status='new' "
            "AND search_started_at IS NOT NULL AND search_started_at<?",
            (cutoff,),
        ).fetchall()


def new_orders():
    with connect() as c:
        return c.execute("SELECT id FROM orders WHERE status='new'").fetchall()


def set_live_msg(order_id: int, msg_id):
    with connect() as c:
        c.execute("UPDATE orders SET live_msg_id=? WHERE id=?", (msg_id, order_id))


def active_order_for_driver(driver_id: int):
    with connect() as c:
        return c.execute(
            "SELECT * FROM orders WHERE driver_id=? AND status='accepted' ORDER BY id DESC LIMIT 1",
            (driver_id,),
        ).fetchone()


def passenger_orders(passenger_id: int, limit: int = 10):
    with connect() as c:
        return c.execute(
            "SELECT id, price, status, created_at FROM orders "
            "WHERE passenger_id=? AND status != 'draft' ORDER BY id DESC LIMIT ?",
            (passenger_id, limit),
        ).fetchall()


def rate_order(order_id: int, passenger_id: int, stars: int) -> bool:
    """Yo'lovchi haydovchini baholaydi."""
    with connect() as c:
        o = c.execute(
            "SELECT * FROM orders WHERE id=? AND passenger_id=? AND status='done' AND rated=0",
            (order_id, passenger_id),
        ).fetchone()
        if not o:
            return False
        d = c.execute("SELECT rating, rating_count FROM drivers WHERE tg_id=?",
                      (o["driver_id"],)).fetchone()
        n = d["rating_count"] + 1
        c.execute("UPDATE drivers SET rating=?, rating_count=? WHERE tg_id=?",
                  ((d["rating"] * d["rating_count"] + stars) / n, n, o["driver_id"]))
        c.execute("UPDATE orders SET rated=1 WHERE id=?", (order_id,))
        return True


def rate_passenger(order_id: int, driver_id: int, stars: int) -> bool:
    """Haydovchi yo'lovchini baholaydi."""
    with connect() as c:
        o = c.execute(
            "SELECT passenger_id FROM orders WHERE id=? AND driver_id=? "
            "AND status='done' AND driver_rated=0",
            (order_id, driver_id),
        ).fetchone()
        if not o:
            return False
        c.execute("UPDATE orders SET driver_rated=1 WHERE id=?", (order_id,))
        c.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (o["passenger_id"],))
        u = c.execute("SELECT rating, rating_count FROM users WHERE tg_id=?",
                      (o["passenger_id"],)).fetchone()
        n = u["rating_count"] + 1
        c.execute("UPDATE users SET rating=?, rating_count=? WHERE tg_id=?",
                  ((u["rating"] * u["rating_count"] + stars) / n, n, o["passenger_id"]))
        return True


def report(since: datetime):
    """(buyurtmalar soni, umumiy narx, komissiya jami) — tugagan safarlar bo'yicha."""
    with connect() as c:
        r = c.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(price), 0) AS total, "
            "COALESCE(SUM(commission), 0) AS comm FROM orders "
            "WHERE status='done' AND done_at>=?",
            (since.isoformat(timespec="seconds"),),
        ).fetchone()
        return r["cnt"], r["total"], r["comm"]


# ---------- Takliflar (navbat) ----------

def create_offer(order_id: int, driver_id: int):
    with connect() as c:
        c.execute("INSERT INTO offers (order_id, driver_id, sent_at, status) VALUES (?, ?, ?, 'sent')",
                  (order_id, driver_id, now()))


def active_offer(order_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM offers WHERE order_id=? AND status='sent'",
                         (order_id,)).fetchone()


def get_offer(order_id: int, driver_id: int):
    with connect() as c:
        return c.execute(
            "SELECT * FROM offers WHERE order_id=? AND driver_id=? ORDER BY id DESC LIMIT 1",
            (order_id, driver_id),
        ).fetchone()


def set_offer_status(order_id: int, driver_id: int, status: str):
    with connect() as c:
        c.execute("UPDATE offers SET status=? WHERE order_id=? AND driver_id=? AND status='sent'",
                  (status, order_id, driver_id))


def expired_offers(cutoff: str):
    with connect() as c:
        return c.execute("SELECT * FROM offers WHERE status='sent' AND sent_at<?",
                         (cutoff,)).fetchall()
