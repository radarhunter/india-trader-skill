"""End-to-end paper session in a temp folder: config -> plan -> size -> risk -> place -> close -> status."""
import json, os, subprocess, sys, tempfile, unittest
from _load import ROOT

PY = sys.executable


def run(env, *args):
    r = subprocess.run([PY] + list(args), cwd=env["cwd"], env=env["env"], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class PaperFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"cwd": self.tmp, "env": dict(os.environ, PYTHONIOENCODING="utf-8", TRADER_NOW="2026-11-03 10:20")}

    def tool(self, skill, f):
        return os.path.join(ROOT, skill, f)

    def test_buy_then_close(self):
        rc, out = run(self.env, self.tool("nse-budget", "size.py"), "config", "--budget-inr", "100000",
                      "--max-loss-per-trade-inr", "1000", "--max-daily-loss-inr", "3000", "--paper")
        self.assertEqual(rc, 0, out)
        rc, out = run(self.env, self.tool("nse-exit", "exit.py"), "DEMO", "--entry", "1000", "--stop", "1.2%", "--target", "2R")
        self.assertEqual(rc, 0, out)
        rc, out = run(self.env, self.tool("nse-budget", "size.py"), "size")
        self.assertEqual(rc, 0, out)
        rc, out = run(self.env, self.tool("nse-risk", "check.py"), "--no-fetch")
        self.assertEqual(rc, 0, out)
        rc, out = run(self.env, self.tool("nse-broker", "ticket.py"), "place", "--yes", "yes")
        self.assertEqual(rc, 0, out)
        self.assertIn("PAPER fill", out)
        hold = json.load(open(os.path.join(self.tmp, "trader", "holdings.json")))
        self.assertEqual(len(hold["positions"]), 1)
        qty = hold["positions"][0]["qty"]
        # a second buy in the same symbol is refused on paper
        rc, out = run(self.env, self.tool("nse-budget", "size.py"), "size")
        rc, out = run(self.env, self.tool("nse-risk", "check.py"), "--no-fetch")
        self.assertEqual(rc, 1, out)
        self.assertIn("already a live order or position", out)
        # close: write the SELL, risk it, place it
        rc, out = run(self.env, self.tool("nse-broker", "ticket.py"), "close", "DEMO", "--price", "995")
        self.assertEqual(rc, 0, out)
        orders = json.load(open(os.path.join(self.tmp, "trader", "orders.json")))
        self.assertEqual(orders["orders"][0]["side"], "SELL")
        rc, out = run(self.env, self.tool("nse-risk", "check.py"), "--no-fetch")
        self.assertEqual(rc, 0, out)
        rc, out = run(self.env, self.tool("nse-broker", "ticket.py"), "place", "--yes", "yes close it")
        self.assertEqual(rc, 0, out)
        self.assertIn("PAPER closed", out)
        hold = json.load(open(os.path.join(self.tmp, "trader", "holdings.json")))
        self.assertEqual(hold["positions"], [])
        log = json.load(open(os.path.join(self.tmp, "trader", "trades_log.json")))
        buys = [r for r in log["trades"] if r["side"] == "BUY"]
        sells = [r for r in log["trades"] if r["side"] == "SELL"]
        self.assertEqual(buys[0]["status"], "closed")
        self.assertAlmostEqual(buys[0]["pnl"], (995 - buys[0]["fill_price"]) * qty, places=2)
        self.assertEqual(sells[0]["status"], "closed")
        rc, out = run(self.env, self.tool("nse-broker", "ticket.py"), "status")
        self.assertIn("realised P&L", out)
        # the realised P&L is counted exactly once (the SELL row), not on both rows
        self.assertIn("realised P&L -\u20b9196.95", out.replace(",", "")) if abs(buys[0]["pnl"] + 196.95) < 0.01 else None
        self.assertAlmostEqual(sum(float(r.get("pnl") or 0) for r in log["trades"] if r["side"] == "SELL"), buys[0]["pnl"], places=2)

    def test_stale_or_future_pass_is_refused(self):
        run(self.env, self.tool("nse-budget", "size.py"), "config", "--paper")
        run(self.env, self.tool("nse-exit", "exit.py"), "DEMO", "--entry", "1000", "--stop", "1.2%", "--target", "2R")
        run(self.env, self.tool("nse-budget", "size.py"), "size")
        run(self.env, self.tool("nse-risk", "check.py"), "--now", "2026-11-03 11:20", "--no-fetch")  # PASS dated an hour ahead of the clock
        rc, out = run(self.env, self.tool("nse-broker", "ticket.py"), "place", "--yes", "yes")
        self.assertEqual(rc, 1, out)
        self.assertIn("no order with a fresh risk PASS", out)


if __name__ == "__main__":
    unittest.main()
