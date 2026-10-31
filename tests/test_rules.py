import datetime as dt, json, os, unittest
from _load import ROOT, load
import nse_common as nc

ex = load("nse-exit", "exit.py")
bud = load("nse-budget", "size.py")
tm = load("nse-timemachine", "timemachine.py")
sc = load("nse-scanner", "scan.py")
en = load("nse-entry", "entry.py")
FIX = os.path.join(ROOT, "tests", "fixtures")


def _load_fix():
    with open(os.path.join(FIX, "DEMO-60d5m.json"), encoding="utf-8") as fh:
        return json.load(fh)


class Clock(unittest.TestCase):
    def test_holidays_and_weekends(self):
        self.assertFalse(nc.is_trading_day(dt.date(2026, 1, 26)))
        self.assertFalse(nc.is_trading_day(dt.date(2026, 11, 1)))  # Sunday
        self.assertTrue(nc.is_trading_day(dt.date(2026, 11, 3)))

    def test_market_phase(self):
        self.assertEqual(nc.market_status(nc.now_ist("2026-11-03 10:00"))["phase"], "open")
        self.assertEqual(nc.market_status(nc.now_ist("2026-11-03 09:05"))["phase"], "pre-open")
        self.assertTrue(nc.market_status(nc.now_ist("2026-11-01 10:00"))["phase"].startswith("closed-weekend"))
        self.assertEqual(nc.market_status(nc.now_ist("2026-11-03 16:30"))["next_open_ist"], "2026-11-04 09:15 IST")

    def test_inr_grouping(self):
        self.assertEqual(nc.inr(1234567.891), "₹12,34,567.89")
        self.assertEqual(nc.inr(999, 0), "₹999")
        self.assertEqual(nc.inr(100000, 0), "₹1,00,000")

    def test_tick_round(self):
        # NSE tick by price band: <=250 0.01, <=1000 0.05, <=5000 0.10, <=10000 0.50, <=20000 1.00, above 5.00
        self.assertEqual(nc.tick_for(100), 0.01)
        self.assertEqual(nc.tick_for(600), 0.05)
        self.assertEqual(nc.tick_for(1164.9), 0.10)
        self.assertEqual(nc.tick_for(25000), 5.00)
        self.assertEqual(nc.tick_round(600.07, down=True), 600.05)
        self.assertEqual(nc.tick_round(600.07, down=False), 600.10)
        self.assertEqual(nc.tick_round(1202.33, down=True), 1202.30)
        self.assertEqual(nc.tick_round(1202.33, down=False), 1202.40)
        self.assertEqual(nc.tick_round(100.073, down=True), 100.07)


class ExitRules(unittest.TestCase):
    def test_default_plan(self):
        p = ex.build_plan(1202.29, "or-mid", "2R", 1201.21, 1194.41)
        self.assertEqual(p["stop"], 1197.80)
        self.assertEqual(p["target"], 1211.20)  # 1211.27 floored to the Rs 0.10 tick above Rs 1,000
        self.assertAlmostEqual(p["reward_risk"], 2.0, places=1)

    def test_refusals(self):
        with self.assertRaises(nc.DataError):
            ex.build_plan(100, "101", "2R")
        with self.assertRaises(nc.DataError):
            ex.build_plan(100, "99.9", "2R")          # too tight
        with self.assertRaises(nc.DataError):
            ex.build_plan(100, "95", "99")            # target below entry
        with self.assertRaises(nc.DataError):
            ex.build_plan(100, "95", "0.5R", min_rr=1.0)

    def test_percent_and_atr(self):
        p = ex.build_plan(1000, "1.5%", "3%")
        self.assertEqual(p["stop"], 985.0)
        self.assertEqual(p["target"], 1030.0)
        p = ex.build_plan(1000, "1.5atr", "2R", atr_val=10)
        self.assertEqual(p["stop"], 985.0)


class Budget(unittest.TestCase):
    def cfg(self, **kw):
        with open(os.path.join(ROOT, "nse-budget", "config.example.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        c.update(kw)
        return c

    def test_charges_intraday_vs_delivery(self):
        c = self.cfg()
        mis = bud.round_trip_charges(100, 1000, 1010, "MIS", c)
        cnc = bud.round_trip_charges(100, 1000, 1010, "CNC", c)
        self.assertAlmostEqual(mis["stt"], 101000 * 0.025 / 100, places=2)
        self.assertAlmostEqual(cnc["stt"], (100000 + 101000) * 0.1 / 100, places=2)
        self.assertGreater(cnc["total"], mis["total"])

    def test_size_decided_by_max_loss(self):
        plan = {"symbol": "DEMO", "entry": 1000, "stop": 990, "target": 1020, "tick": 0.05}
        o = bud.size_order(plan, self.cfg(max_loss_per_trade_inr=500, budget_inr=1000000, max_order_inr=1000000, max_position_pct=100))
        self.assertEqual(o["decided_by"], "max_loss_per_trade")
        self.assertLessEqual(o["loss_at_stop_inr"], 500)
        self.assertEqual(o["price"], 1000.05)  # Rs 1,000 is still in the <=1000 band: Rs 0.05 tick
        self.assertEqual(o["bracket"]["stop_limit"], 986.0)  # max(3 ticks, 0.4%) under the trigger
        self.assertEqual(o["notional_inr"], o["qty"] * o["price"])  # measured from the limit price

    def test_refuses_far_stop(self):
        plan = {"symbol": "DEMO", "entry": 1000, "stop": 900, "target": 1200}
        with self.assertRaises(nc.DataError):
            bud.size_order(plan, self.cfg(max_loss_per_trade_inr=50))

    def test_tight_limits_pass_risk(self):
        """What budget sizes, risk must pass: both measure from the limit price."""
        import tempfile, os
        risk = load("nse-risk", "check.py")
        plan = {"symbol": "DEMO", "entry": 1000, "stop": 990, "target": 1020}
        cfg = self.cfg(max_loss_per_trade_inr=1000, budget_inr=100000, max_order_inr=40000, max_position_pct=40)
        o = bud.size_order(plan, cfg)
        ctx = {"now": nc.now_ist("2026-11-03 10:20"), "quotes": {}, "universe_tags": {}, "day_realised_loss": 0.0, "day_open_risk": 0.0,
               "day_trades": 0, "day_orders": 0, "pending_open_risk": 0.0, "pending_trades": 0, "pending_orders": 0,
               "live_symbols": set(), "results_today": set(), "allow_closed": False, "held": {}}
        reasons, _ = risk.check_order(o, cfg, ctx)
        self.assertEqual(reasons, [])

    def test_daily_limit_aggregates_within_a_run(self):
        risk = load("nse-risk", "check.py")
        cfg = self.cfg(max_loss_per_trade_inr=2000, max_daily_loss_inr=3000, budget_inr=1000000, max_order_inr=1000000, max_position_pct=100)
        ctx = {"now": nc.now_ist("2026-11-03 10:20"), "quotes": {}, "universe_tags": {}, "day_realised_loss": 0.0, "day_open_risk": 0.0,
               "day_trades": 0, "day_orders": 0, "pending_open_risk": 0.0, "pending_trades": 0, "pending_orders": 0,
               "live_symbols": set(), "results_today": set(), "allow_closed": False, "held": {}}
        o1 = bud.size_order({"symbol": "AAA", "entry": 1000, "stop": 990, "target": 1020}, dict(cfg, max_loss_per_trade_inr=1700))
        o2 = dict(o1, symbol="BBB")
        r1, _ = risk.check_order(o1, cfg, ctx)
        r2, _ = risk.check_order(o2, cfg, ctx)
        self.assertEqual(r1, [])
        self.assertTrue(any("daily limit" in r for r in r2), r2)

    def test_sell_needs_a_position(self):
        risk = load("nse-risk", "check.py")
        cfg = self.cfg()
        base = {"now": nc.now_ist("2026-11-03 10:20"), "quotes": {}, "universe_tags": {}, "day_realised_loss": 0.0, "day_open_risk": 0.0,
                "day_trades": 0, "day_orders": 0, "pending_open_risk": 0.0, "pending_trades": 0, "pending_orders": 0,
                "live_symbols": set(), "results_today": set(), "allow_closed": False}
        sell = {"symbol": "DEMO", "side": "SELL", "qty": 10, "price": 1000, "product": "MIS", "bracket": {}}
        r, _ = risk.check_order(sell, cfg, dict(base, held={}))
        self.assertTrue(any("short" in x for x in r), r)
        r, _ = risk.check_order(sell, cfg, dict(base, held={"DEMO": 5}))
        self.assertTrue(any("only 5 held" in x for x in r), r)
        r, _ = risk.check_order(sell, cfg, dict(base, held={"DEMO": 10}))
        self.assertEqual(r, [])


class TimeMachine(unittest.TestCase):
    def test_simulation_is_seeded(self):
        shapes = [(0.002, -0.002, 0.0005), (0.001, -0.003, -0.001), (0.004, -0.001, 0.002)]
        a = tm.simulate(100, 99, 102, 20, shapes, 500, 19)
        b = tm.simulate(100, 99, 102, 20, shapes, 500, 19)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["target"] + a["stop"] + a["open"], 100, delta=0.2)

    def test_history_on_fixture(self):
        doc = json.load(open(os.path.join(FIX, "DEMO-60d5m.json"), encoding="utf-8")) if False else _load_fix()
        bars = nc.clean_bars(nc.parse_chart(doc))
        rows = tm.replay_history(bars, 15, "or-mid", 2.0, dt.time(15, 10), dt.time(14, 45))
        self.assertEqual(len(rows), 60)
        self.assertTrue(any(r.get("fired") for r in rows))


class ScannerAndEntry(unittest.TestCase):
    def test_daily_setups(self):
        closes = [100 + i * 0.5 for i in range(60)]
        found = dict(sc.daily_setups(closes))
        self.assertIn("breakout20", found)
        self.assertIn("trend", found)
        closes = [100 - i for i in range(30)]
        self.assertIn("oversold", dict(sc.daily_setups(closes)))

    def test_orb_and_entry_on_fixture(self):
        doc = json.load(open(os.path.join(FIX, "DEMO-60d5m.json"), encoding="utf-8")) if False else _load_fix()
        bars = nc.session_bars(nc.clean_bars(nc.parse_chart(doc)), "2026-11-03")
        now = int(nc.now_ist("2026-11-03 10:15").timestamp())
        h = sc.orb_hit(bars, 15, "2026-11-03", cutoff_ts=now)
        self.assertIsNotNone(h)
        self.assertEqual(h["trigger_time"], "09:45")
        r = en.evaluate(bars, "2026-11-03", now, 15)
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["entry"], h["trigger_close"])
        early = en.evaluate(bars, "2026-11-03", int(nc.now_ist("2026-11-03 09:31").timestamp()), 15)
        self.assertEqual(early["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
