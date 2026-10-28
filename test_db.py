"""Ma'lumotlar bazasi mantig'i: qarz, jarima, kutish haqi, navbat, shikoyat, FSM, zaxira."""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="luna-test-")
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")

import db  # noqa: E402
from pricing import calc_price, distance_km  # noqa: E402


class DbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        db.create_driver(1, "Ali", "+998900000001", "Nexia", "lf", "tf")
        db.create_driver(2, "Vali", "+998900000002", "Spark", "lf", "tf")
        for t in (1, 2):
            db.set_driver_status(t, "approved")
            db.update_driver_location(t, 39.65, 66.96)
        db.set_online(1, 1)
        db.set_online(2, 1)

    def setUp(self):
        """Har test oldidan holatni tiklaydi: qarz, faol buyurtma, smena."""
        with db.connect() as c:
            c.execute("UPDATE drivers SET debt=0, online=0, online_since=NULL WHERE tg_id IN (1,2)")
            c.execute("UPDATE orders SET status='cancelled' WHERE status IN ('draft','new','accepted')")
            c.execute("UPDATE settings SET value=100000 WHERE key='debt_limit'")
        for t in (1, 2):
            db.update_driver_location(t, 39.65, 66.96)
            db.set_online(t, 1)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def _order(self, pid):
        oid = db.create_order(pid, 39.65, 66.96, 39.66, 66.97, 2.0, 10, 20000, 0, 0)
        db.confirm_order(oid, pid)
        return oid

    def test_phone_unique(self):
        self.assertTrue(db.phone_taken("+998900000001"))

    def test_one_active_order_per_passenger(self):
        oid = db.create_order(500, 39.65, 66.96, 39.66, 66.97, 2.0, 10, 20000, 0, 0)
        self.assertIsNotNone(oid)
        self.assertIsNone(db.create_order(500, 39.65, 66.96, 39.66, 66.97, 2.0, 10, 20000, 0, 0))
        db.cancel_order(oid, 500)

    def test_dispatch_candidates_and_busy_driver(self):
        oid = self._order(100)
        self.assertEqual(sorted(r["tg_id"] for r in db.candidate_drivers(oid)), [1, 2])
        db.create_offer(oid, 1)
        self.assertNotIn(1, [r["tg_id"] for r in db.candidate_drivers(oid)])
        self.assertTrue(db.accept_order(oid, 1))
        self.assertFalse(db.accept_order(oid, 1))
        oid2 = self._order(101)
        self.assertFalse(db.accept_order(oid2, 1))  # haydovchi band
        db.cancel_order(oid2, 101)

    def test_driver_penalty_on_release_then_wait_fee_and_commission(self):
        oid = self._order(102)
        self.assertTrue(db.accept_order(oid, 2))
        self.assertTrue(db.release_order(oid, penalty=5000))
        self.assertEqual(db.get_driver(2)["debt"], 5000)
        self.assertEqual(db.get_order(oid)["status"], "new")
        self.assertTrue(db.accept_order(oid, 1))
        self.assertTrue(db.mark_arrived(oid, 1))
        self.assertFalse(db.mark_arrived(oid, 1))
        with db.connect() as c:
            c.execute("UPDATE orders SET arrived_at=? WHERE id=?",
                      ((datetime.now() - timedelta(minutes=20)).isoformat(timespec="seconds"), oid))
        o = db.finish_order(oid, 1)
        s = db.get_settings()
        self.assertEqual(o["status"], "done")
        self.assertGreaterEqual(o["wait_fee"], 2900)
        self.assertEqual(o["price"], 20000 + o["wait_fee"])
        self.assertEqual(o["commission"], round(o["price"] * s["commission_percent"] / 100))
        self.assertEqual(o["driver_earning"], o["price"] - o["commission"])
        self.assertIsNone(db.finish_order(oid, 1))

    def test_debt_limit_blocks_and_payment_unblocks(self):
        db.set_setting("debt_limit", 1000)
        try:
            with db.connect() as c:
                c.execute("UPDATE drivers SET debt=5000 WHERE tg_id=2")
            oid = self._order(103)
            self.assertFalse(db.accept_order(oid, 2))  # qarz 5000 >= 1000
            self.assertNotIn(2, [r["tg_id"] for r in db.candidate_drivers(oid)])
            self.assertEqual(db.pay_debt(2, 10**9), 0)
            self.assertEqual(db.get_driver(2)["debt"], 0)
            self.assertTrue(db.accept_order(oid, 2))
            db.cancel_order(oid, 103)
        finally:
            db.set_setting("debt_limit", 100000)

    def test_cancel_fee_and_free_cancel(self):
        oid = self._order(104)
        db.accept_order(oid, 1)
        self.assertEqual(db.cancel_order(oid, 104), db.get_settings()["cancel_fee"])
        oid2 = self._order(105)
        self.assertEqual(db.cancel_order(oid2, 105), 0)
        self.assertIsNone(db.cancel_order(oid2, 105))

    def test_rating_once_and_report(self):
        oid = self._order(106)
        db.accept_order(oid, 1)
        db.mark_arrived(oid, 1)
        o = db.finish_order(oid, 1)
        self.assertTrue(db.rate_order(oid, 106, 5))
        self.assertFalse(db.rate_order(oid, 106, 5))
        self.assertTrue(db.rate_passenger(oid, 1, 4))
        cnt, total, comm = db.report(datetime.now() - timedelta(days=1))
        self.assertGreaterEqual(cnt, 1)
        self.assertGreaterEqual(comm, o["commission"])

    def test_complaints(self):
        cid = db.add_complaint(1, 100, 2, "test")
        self.assertTrue(any(r["id"] == cid for r in db.open_complaints()))
        self.assertTrue(db.close_complaint(cid))
        self.assertFalse(db.close_complaint(cid))

    def test_fsm_persistence(self):
        db.fsm_set_state("k", "DriverApply:car")
        db.fsm_set_data("k", {"name": "x"})
        self.assertEqual(db.fsm_get("k"), ("DriverApply:car", {"name": "x"}))
        db.fsm_set_state("k", None)
        self.assertEqual(db.fsm_get("k"), (None, {"name": "x"}))

    def test_auto_offline(self):
        with db.connect() as c:
            c.execute("UPDATE drivers SET online_since=? WHERE tg_id=1", ("2000-01-01T00:00:00",))
        self.assertEqual(db.auto_offline("2020-01-01T00:00:00"), [1])
        self.assertEqual(db.get_driver(1)["online"], 0)

    def test_admin_cancel(self):
        oid = self._order(107)
        self.assertIsNotNone(db.admin_cancel(oid))
        self.assertEqual(db.get_order(oid)["status"], "cancelled")

    def test_backup_once_per_day(self):
        bdir = os.path.join(TMP, "bk")
        path = db.backup_db(bdir, keep=2)
        self.assertTrue(os.path.exists(path))
        self.assertIsNone(db.backup_db(bdir, keep=2))


class PricingTests(unittest.TestCase):
    S = {"base_price": 5000, "price_per_km": 2000, "price_per_min": 100, "min_price": 8000}

    def test_price(self):
        self.assertEqual(calc_price(2.0, 10, self.S), 10000)
        self.assertEqual(calc_price(0.3, 1, self.S), 8000)

    def test_distance_road_factor(self):
        self.assertAlmostEqual(distance_km(39.65, 66.96, 39.66, 66.96), 1.446, places=2)


if __name__ == "__main__":
    unittest.main()
