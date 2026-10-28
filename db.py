import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH, DEFAULT_SETTINGS

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
    status TEXT NOT NULL DEFAULT 'new',       -- new, accepted, done, cancelled
    live_msg_id INTEGER,
    rated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def migrate(c):
    """Eski bazaga yangi ustunlarni qo'shadi (mavjud ma'lumot saqlanadi)."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(orders)")}
    if "commission" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN commission INTEGER NOT NULL DEFAULT 0")
    if "driver_earning" not in cols:
        c.execute("ALTER TABLE orders ADD COLUMN driver_earning INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE orders SET driver_earning = price")


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        migrate(c)


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


def create_driver(tg_id, name, phone, car, license_file, techpass_file):
    with connect() as c:
        c.execute(
            """INSERT INTO drivers (tg_id, name, phone, car, license_file, techpass_file,
                                    status, online, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)""",
            (tg_id, name, phone, car, license_file, techpass_file, now()),
        )


def set_driver_status(tg_id: int, status: str):
    with connect() as c:
        c.execute("UPDATE drivers SET status=?, online=0 WHERE tg_id=?", (status, tg_id))


def set_online(tg_id: int, value: int):
    with connect() as c:
        c.execute("UPDATE drivers SET online=? WHERE tg_id=? AND status='approved'", (value, tg_id))


def online_driver_ids() -> list[int]:
    with connect() as c:
        return [r["tg_id"] for r in c.execute(
            "SELECT tg_id FROM drivers WHERE online=1 AND status='approved'")]


def list_drivers(statuses: tuple):
    placeholders = ",".join("?" * len(statuses))
    with connect() as c:
        return c.execute(
            f"SELECT * FROM drivers WHERE status IN ({placeholders}) ORDER BY created_at",
            statuses,
        ).fetchall()


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
    with connect() as c:
        cur = c.execute(
            """INSERT INTO orders (passenger_id, pickup_lat, pickup_lon, dest_lat, dest_lon,
                                   km, minutes, price, commission, driver_earning, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (passenger_id, p_lat, p_lon, d_lat, d_lon, km, minutes, price,
             commission, driver_earning, now()),
        )
        return cur.lastrowid


def get_order(order_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def accept_order(order_id: int, driver_id: int) -> bool:
    """Atomik: faqat hali olinmagan buyurtma bitta haydovchiga beriladi."""
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET driver_id=?, status='accepted' "
            "WHERE id=? AND status='new' AND driver_id IS NULL",
            (driver_id, order_id),
        )
        return cur.rowcount == 1


def finish_order(order_id: int, driver_id: int) -> bool:
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET status='done' WHERE id=? AND driver_id=? AND status='accepted'",
            (order_id, driver_id),
        )
        return cur.rowcount == 1


def cancel_order(order_id: int, passenger_id: int) -> bool:
    with connect() as c:
        cur = c.execute(
            "UPDATE orders SET status='cancelled' WHERE id=? AND passenger_id=? "
            "AND status IN ('new', 'accepted')",
            (order_id, passenger_id),
        )
        return cur.rowcount == 1


def set_live_msg(order_id: int, msg_id: int):
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
            "SELECT id, price, status, created_at FROM orders WHERE passenger_id=? "
            "ORDER BY id DESC LIMIT ?",
            (passenger_id, limit),
        ).fetchall()


def rate_order(order_id: int, passenger_id: int, stars: int) -> bool:
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
        new_rating = (d["rating"] * d["rating_count"] + stars) / n
        c.execute("UPDATE drivers SET rating=?, rating_count=? WHERE tg_id=?",
                  (new_rating, n, o["driver_id"]))
        c.execute("UPDATE orders SET rated=1 WHERE id=?", (order_id,))
        return True


def report(since: datetime):
    """Qaytaradi: (buyurtmalar soni, umumiy narx, komissiya jami)."""
    with connect() as c:
        r = c.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(price), 0) AS total, "
            "COALESCE(SUM(commission), 0) AS comm FROM orders "
            "WHERE status='done' AND created_at>=?",
            (since.isoformat(timespec="seconds"),),
        ).fetchone()
        return r["cnt"], r["total"], r["comm"]
