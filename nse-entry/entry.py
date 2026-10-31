#!/usr/bin/env python3
"""nse-entry: the Entry Timer for an NSE / BSE long.

Watches one symbol bar by bar on free Yahoo 1-minute or 5-minute data,
builds the opening range (09:15 + --or-minutes, default 15) and calls the
entry on the first COMPLETED bar that closes above the range high, or above
a level you give it (--level, e.g. the 20-day high from the scanner). Says
`waiting` (and how far), `triggered` (time and price), `expired` (past the
entry window) or `not open yet`, and writes trader/entry.json for nse-exit.

Long side only. It never places an order. Exit 0 = triggered, 1 = anything else.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402


def load_bars(a, sym, day):
    if a.offline:
        doc = json.loads(Path(a.offline).read_text(encoding="utf-8"))
        ch = nc.parse_chart(doc, sym)
    else:
        ch = nc.yahoo_chart(sym, "1d", a.interval)
    return nc.session_bars(nc.clean_bars(ch), day), ch["meta"]


def evaluate(bars, day, now_ts, or_minutes, level=None, vol_mult=0.0, interval_sec=300, no_before=None, no_after=None):
    start = dt.datetime.combine(dt.date.fromisoformat(day), nc.OPEN, nc.IST)
    or_end = start + dt.timedelta(minutes=or_minutes)
    inside = [b for b in bars if start.timestamp() <= b[0] < or_end.timestamp()]
    res = {"status": "waiting", "or_high": None, "or_low": None, "level": level}
    if now_ts < start.timestamp():
        res["status"] = "not-open-yet"
        return res
    if not inside:
        res["status"] = "waiting"
        res["why"] = "no bars yet for the opening range"
        return res
    or_high, or_low = max(b[2] for b in inside), min(b[3] for b in inside)
    res.update({"or_high": round(or_high, 2), "or_low": round(or_low, 2),
                "or_end": or_end.strftime("%H:%M")})
    trigger_level = level if level else or_high
    res["trigger_level"] = round(trigger_level, 2)
    if now_ts < or_end.timestamp() + interval_sec:
        res["status"] = "waiting"
        res["why"] = "opening range forms until %s; first bar after it closes at %s" % (
            or_end.strftime("%H:%M"), (or_end + dt.timedelta(seconds=interval_sec)).strftime("%H:%M"))
        return res
    avg_vol = sum(b[5] for b in inside) / max(1, len(inside))
    last_closed = None
    for b in bars:
        if b[0] < or_end.timestamp():
            continue
        if b[0] + interval_sec > now_ts:
            break
        last_closed = b
        t = dt.datetime.fromtimestamp(b[0], nc.IST).time()
        if b[4] > trigger_level and (vol_mult <= 0 or b[5] >= avg_vol * vol_mult):
            res.update({"status": "triggered", "trigger_ts": b[0], "trigger_time": nc.fmt_time_ist(b[0]),
                        "entry": round(b[4], 2), "bar_volume": int(b[5]),
                        "bar_high": round(b[2], 2), "bar_low": round(b[3], 2),
                        "in_window": (no_before is None or t >= no_before) and (no_after is None or t <= no_after)})
            return res
    if no_after and dt.datetime.fromtimestamp(now_ts, nc.IST).time() > no_after:
        res["status"] = "expired"
        res["why"] = "past the entry window (%s)" % no_after.strftime("%H:%M")
        return res
    if last_closed:
        res["last_close"] = round(last_closed[4], 2)
        res["distance_pct"] = round((trigger_level - last_closed[4]) / trigger_level * 100, 2)
        res["why"] = "last bar closed %.2f, %.2f%% below the trigger %.2f" % (
            last_closed[4], res["distance_pct"], trigger_level)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("--setup", default="orb", choices=["orb", "level"])
    ap.add_argument("--level", type=float, help="trigger on a close above this price instead of the OR high")
    ap.add_argument("--or-minutes", type=int, default=15)
    ap.add_argument("--interval", default="5m", choices=["1m", "5m"])
    ap.add_argument("--vol-mult", type=float, default=0.0, help="require bar volume >= this x the average OR bar (0 = off)")
    ap.add_argument("--watch", action="store_true", help="keep polling until triggered or expired")
    ap.add_argument("--poll", type=int, default=30, help="seconds between polls with --watch")
    ap.add_argument("--now", help="pin the clock 'YYYY-MM-DD HH:MM' IST")
    ap.add_argument("--offline", help="a saved Yahoo chart JSON")
    ap.add_argument("--exchange", default="NSE", choices=["NSE", "BSE"])
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sym = a.symbol.upper()
    cfg = nc.load_config()
    no_before = nc.parse_hhmm(cfg.get("no_entries_before"), dt.time(9, 30))
    no_after = nc.parse_hhmm(cfg.get("no_entries_after"), dt.time(14, 45))
    interval_sec = 60 if a.interval == "1m" else 300
    level = a.level if a.setup == "level" or a.level else None
    while True:
        t = nc.now_ist(a.now)
        day = t.date().isoformat()
        if not nc.is_trading_day(t.date()) and not a.offline:
            print("%s is not an NSE trading day." % day)
            return 1
        try:
            bars, meta = load_bars(a, sym, day)
        except nc.DataError as e:
            print("Entry could not run: %s" % e)
            return 1
        res = evaluate(bars, day, int(t.timestamp()), a.or_minutes, level, a.vol_mult, interval_sec, no_before, no_after)
        res.update({"symbol": sym, "setup": a.setup if not level else "level", "as_of": t.strftime("%Y-%m-%d %H:%M IST"),
                    "interval": a.interval, "name": meta.get("longName"), "exchange": a.exchange})
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            s = res["status"]
            if s == "triggered":
                print("%s TRIGGERED at %s IST: bar closed %.2f above %.2f (range %.2f-%.2f, bar volume %s)%s" % (
                    sym, res["trigger_time"], res["entry"], res["trigger_level"], res["or_low"], res["or_high"],
                    res["bar_volume"], "" if res["in_window"] else "  - OUTSIDE the entry window; /nse-risk will block it"))
            elif s == "waiting":
                print("%s waiting. %s%s" % (sym, ("Range %.2f-%.2f. " % (res["or_low"], res["or_high"])) if res.get("or_high") else "", res.get("why", "")))
            elif s == "expired":
                print("%s expired: %s. No entry today." % (sym, res.get("why")))
            else:
                print("%s: market not open yet (opens 09:15 IST)." % sym)
        if not a.no_write and res["status"] == "triggered":
            p = nc.write_json(nc.ensure_trader_dir() / "entry.json", res)
            if not a.json:
                print("Wrote %s. Next: /nse-exit" % p)
            if cfg.get("notify", True):
                nc.notify("nse-entry %s" % sym, "Triggered %.2f at %s" % (res["entry"], res["trigger_time"]))
        if res["status"] in ("triggered", "expired") or not a.watch or a.now or a.offline:
            return 0 if res["status"] == "triggered" else 1
        time.sleep(a.poll)


if __name__ == "__main__":
    sys.exit(main())
