#!/usr/bin/env python3
"""nse-alerts: Price Alerts for NSE / BSE. Rings once when a level hits.

  plan                 Turn trader/plan.json into three alerts: the stop, the
                       target, and "flat by" at the plan's flat_by time.
  holdings             One stop and one target alert for every position in
                       trader/holdings.json (so same-day sells ring the moment
                       a stop is hit), plus a -X% from entry warning.
  add SYMBOL above|below PRICE      a level alert
  add SYMBOL pct +3 | pct -2        percent move from the previous close
  add time HH:MM [label]            a clock alert (IST)
  add bell                          the 09:15 opening bell
  list / clear / remove ID
  watch [--once]       Poll every N seconds (config watch_poll_seconds, 30),
                       ring each alert once (desktop notification + bell),
                       write trader/prices.json, stop at 15:35 IST.
  quote SYMBOL...      Last price, previous close, change, from Yahoo.
  market               Is the market open? Next open, holidays ahead.
  holidays [--refresh] Show the NSE holiday list; --refresh pulls it live.

Free delayed quotes (Yahoo). Check anything that matters with the broker.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import uuid
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402


def alerts_path():
    return nc.trader_dir() / "alerts.json"


def load_alerts():
    return nc.read_json(alerts_path(), {"alerts": []}) or {"alerts": []}


def save_alerts(doc):
    nc.write_json(alerts_path(), doc)


def new_alert(kind, symbol=None, level=None, label="", **extra):
    a = {"id": uuid.uuid4().hex[:8], "kind": kind, "symbol": symbol, "level": level, "label": label,
         "created": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "fired": None}
    a.update(extra)
    return a


def cmd_plan(a):
    plan = nc.read_json(nc.trader_dir() / "plan.json", None)
    if not plan:
        print("No trader/plan.json. Run /nse-exit first.")
        return 1
    doc = load_alerts()
    s = plan["symbol"]
    doc["alerts"] = [x for x in doc["alerts"] if not (x.get("symbol") == s and x.get("label") in ("stop", "target"))]
    doc["alerts"].append(new_alert("below", s, plan["stop"], "stop"))
    doc["alerts"].append(new_alert("above", s, plan["target"], "target"))
    if plan.get("flat_by"):
        doc["alerts"] = [x for x in doc["alerts"] if x.get("label") != "flat-by"]
        doc["alerts"].append(new_alert("time", None, plan["flat_by"], "flat-by"))
    save_alerts(doc)
    print("Three alerts set for %s: stop %s, target %s%s." % (
        s, nc.inr(plan["stop"]), nc.inr(plan["target"]), (", flat by %s IST" % plan["flat_by"]) if plan.get("flat_by") else ""))
    return 0


def cmd_holdings(a):
    cfg = nc.load_config()
    hold = nc.load_holdings(cfg)
    pos = hold.get("positions") or []
    if not pos:
        print("No positions in %s." % cfg.get("holdings_file"))
        return 1
    doc = load_alerts()
    n = 0
    for p in pos:
        s = p["symbol"].upper()
        doc["alerts"] = [x for x in doc["alerts"] if not (x.get("symbol") == s and x.get("label") in ("stop", "target", "drawdown"))]
        if p.get("stop"):
            doc["alerts"].append(new_alert("below", s, float(p["stop"]), "stop"))
            n += 1
        if p.get("target"):
            doc["alerts"].append(new_alert("above", s, float(p["target"]), "target"))
            n += 1
        if p.get("avg_price") and a.drawdown:
            lvl = nc.tick_round(float(p["avg_price"]) * (1 - a.drawdown / 100), down=True)
            doc["alerts"].append(new_alert("below", s, lvl, "drawdown", note="%.1f%% under your average" % a.drawdown))
            n += 1
    save_alerts(doc)
    print("%d alerts set on %d position(s). Run `watch` to arm them." % (n, len(pos)))
    return 0


def cmd_add(a):
    doc = load_alerts()
    what = a.args
    if not what:
        print("add SYMBOL above|below PRICE | add SYMBOL pct +3 | add time HH:MM [label] | add bell")
        return 1
    if what[0].lower() == "bell":
        doc["alerts"].append(new_alert("time", None, "09:15", "opening bell"))
    elif what[0].lower() == "time":
        if len(what) < 2 or not re.fullmatch(r"\d{1,2}:\d{2}", what[1]):
            print("add time HH:MM [label]")
            return 1
        doc["alerts"].append(new_alert("time", None, what[1], " ".join(what[2:]) or "time"))
    elif len(what) >= 3 and what[1].lower() in ("above", "below"):
        doc["alerts"].append(new_alert(what[1].lower(), what[0].upper(), float(what[2]), " ".join(what[3:]) or what[1].lower()))
    elif len(what) >= 3 and what[1].lower() == "pct":
        doc["alerts"].append(new_alert("pct", what[0].upper(), float(what[2]), "%+.1f%% from prev close" % float(what[2])))
    else:
        print("Could not read that alert.")
        return 1
    save_alerts(doc)
    x = doc["alerts"][-1]
    print("Added %s: %s %s %s (%s)" % (x["id"], x.get("symbol") or "", x["kind"], x["level"], x["label"]))
    return 0


def cmd_list(a):
    doc = load_alerts()
    if not doc["alerts"]:
        print("No alerts.")
        return 1
    for x in doc["alerts"]:
        print("  %s  %-10s %-6s %-10s %-14s %s" % (x["id"], x.get("symbol") or "-", x["kind"], x["level"], x["label"],
                                                  ("fired %s" % x["fired"]) if x.get("fired") else "armed"))
    return 0


def cmd_clear(a):
    save_alerts({"alerts": []})
    print("Cleared.")
    return 0


def cmd_remove(a):
    doc = load_alerts()
    before = len(doc["alerts"])
    doc["alerts"] = [x for x in doc["alerts"] if x["id"] not in a.args]
    save_alerts(doc)
    print("Removed %d." % (before - len(doc["alerts"])))
    return 0


def fetch_prices(symbols):
    out = {}
    for s in symbols:
        try:
            q = nc.yahoo_quote_fast(s)
            out[s] = {"last": q["last"], "prev_close": q["prev_close"], "time": q.get("time"),
                      "change_pct": round((q["last"] - q["prev_close"]) / q["prev_close"] * 100, 2) if q["last"] and q["prev_close"] else None,
                      "day_low": min(b[3] for b in q["bars"]) if q["bars"] else None,
                      "day_high": max(b[2] for b in q["bars"]) if q["bars"] else None}
        except nc.DataError as e:
            out[s] = {"error": str(e)}
        time.sleep(0.3)
    return out


def check_alert(x, prices, now):
    if x.get("fired"):
        return None
    if x["kind"] == "time":
        hh, mm = [int(v) for v in str(x["level"]).split(":")[:2]]
        if now.time() >= dt.time(hh, mm):
            return "%s - it is %s IST" % (x["label"], now.strftime("%H:%M"))
        return None
    p = prices.get(x["symbol"]) or {}
    last = p.get("last")
    if last is None:
        return None
    if x["kind"] == "above" and last >= float(x["level"]):
        return "%s %s %s: last %s" % (x["symbol"], x["label"], nc.inr(x["level"]), nc.inr(last))
    if x["kind"] == "below" and last <= float(x["level"]):
        return "%s %s %s: last %s%s" % (x["symbol"], x["label"], nc.inr(x["level"]), nc.inr(last), (" (%s)" % x["note"]) if x.get("note") else "")
    if x["kind"] == "pct" and p.get("change_pct") is not None:
        lvl = float(x["level"])
        if (lvl >= 0 and p["change_pct"] >= lvl) or (lvl < 0 and p["change_pct"] <= lvl):
            return "%s moved %s from the previous close (last %s)" % (x["symbol"], nc.pct(p["change_pct"]), nc.inr(last))
    return None


def cmd_watch(a):
    cfg = nc.load_config()
    poll = a.poll or int(cfg.get("watch_poll_seconds", 30))
    end_t = dt.time(15, 35)
    while True:
        now = nc.now_ist(a.now)
        doc = load_alerts()
        today = now.date().isoformat()
        for x in doc["alerts"]:  # time alerts re-arm each trading day
            if x["kind"] == "time" and x.get("fired") and not str(x["fired"]).startswith(today):
                x["fired"] = None
        armed = [x for x in doc["alerts"] if not x.get("fired")]
        if not armed:
            print("No armed alerts. Add some, or run `plan` / `holdings`.")
            return 1
        syms = sorted({x["symbol"] for x in armed if x.get("symbol")})
        prices = fetch_prices(syms) if syms else {}
        nc.write_json(nc.ensure_trader_dir() / "prices.json", {"as_of": now.strftime("%Y-%m-%d %H:%M:%S IST"), "prices": prices})
        fired_any = False
        for x in doc["alerts"]:
            msg = check_alert(x, prices, now)
            if msg:
                x["fired"] = now.strftime("%Y-%m-%d %H:%M:%S IST")
                fired_any = True
                print("ALERT %s  %s" % (now.strftime("%H:%M:%S"), msg), flush=True)
                if cfg.get("notify", True):
                    nc.notify("nse-alerts", msg)
                if x.get("symbol") and x.get("label") in ("stop", "target"):  # a paper position: book it
                    pnl = nc.paper_close(cfg, x["symbol"], float(x["level"]), "paper %s hit" % x["label"])
                    if pnl is not None:
                        print("  PAPER %s booked for %s at %s: P&L %s" % (x["label"], x["symbol"], nc.inr(x["level"]), nc.inr(pnl)))
        if fired_any:
            save_alerts(doc)
        if not a.quiet:
            line = ", ".join("%s %s (%s)" % (s, nc.inr(p.get("last")), nc.pct(p.get("change_pct"))) for s, p in prices.items() if p.get("last"))
            print("%s  %s  armed %d" % (now.strftime("%H:%M:%S"), line or "-", sum(1 for x in doc["alerts"] if not x.get("fired"))), flush=True)
        if a.once or a.now:
            return 0
        if not any(not x.get("fired") for x in doc["alerts"]):
            print("All alerts fired. Done.")
            return 0
        if now.time() >= end_t or not nc.is_trading_day(now.date()):
            print("Market closed; watch stopped. Alerts stay armed for the next session.")
            return 0
        time.sleep(poll)


def cmd_quote(a):
    if not a.args:
        print("quote SYMBOL [SYMBOL...]")
        return 1
    prices = fetch_prices([s.upper() for s in a.args])
    for s, p in prices.items():
        if p.get("error"):
            print("  %-12s %s" % (s, p["error"]))
        else:
            print("  %-12s %-12s %s vs prev close %s  (day %s-%s)%s" % (
                s, nc.inr(p["last"]), nc.pct(p["change_pct"]), nc.inr(p["prev_close"]), nc.inr(p.get("day_low")), nc.inr(p.get("day_high")),
                ("  as of %s" % nc.fmt_dt_ist(p["time"])) if p.get("time") else ""))
    return 0


def cmd_market(a):
    ms = nc.market_status(nc.now_ist(a.now))
    if ms["is_open"]:
        print("%s: market OPEN, closes in %d minutes (15:30). Broker MIS square-off ~%s." % (
            ms["now_ist"], ms["minutes_to_close"], ms["broker_square_off_ist"]))
    else:
        print("%s: %s. Next open %s (in %d minutes). Hours 09:15-15:30, broker MIS square-off ~%s." % (
            ms["now_ist"], ms["phase"], ms["next_open_ist"], ms["minutes_to_open"], ms["broker_square_off_ist"]))
    hol = nc.load_holidays()
    today = nc.now_ist(a.now).date()
    ahead = sorted((d, n) for d, n in hol.items() if dt.date.fromisoformat(d) >= today)[:5]
    if ahead:
        print("Holidays ahead: " + "; ".join("%s %s" % (d, n) for d, n in ahead))
    return 0


def cmd_holidays(a):
    if a.refresh:
        sess = nc.NSESession()
        try:
            doc = sess.json("/api/holiday-master?type=trading")
        except nc.DataError as e:
            print("Could not refresh: %s. Using the bundled list." % e)
            doc = None
        if doc:
            rows = doc.get("CM") or doc.get("cm") or []
            got = {}
            for r in rows:
                d = r.get("tradingDate") or ""
                for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
                    try:
                        got[dt.datetime.strptime(d.strip(), fmt).date().isoformat()] = (r.get("description") or "").strip()
                        break
                    except ValueError:
                        continue
            if got:
                nc.write_json(nc.holidays_file(), {"holidays": got, "fetched": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "source": "nseindia.com holiday-master"})
                print("Refreshed %d holidays from NSE into %s." % (len(got), nc.holidays_file()))
    hol = nc.load_holidays()
    yr = a.year or nc.now_ist().year
    for d, n in sorted(hol.items()):
        if d.startswith(str(yr)):
            print("  %s  %s  %s" % (d, dt.date.fromisoformat(d).strftime("%a"), n))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "holdings", "add", "list", "clear", "remove", "watch", "quote", "market", "holidays"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--poll", type=int)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--now", help="pin the clock 'YYYY-MM-DD HH:MM' IST")
    ap.add_argument("--drawdown", type=float, default=2.0, help="holdings: also ring at this %% under the average price")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--year", type=int)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    fn = {"plan": cmd_plan, "holdings": cmd_holdings, "add": cmd_add, "list": cmd_list, "clear": cmd_clear, "remove": cmd_remove,
          "watch": cmd_watch, "quote": cmd_quote, "market": cmd_market, "holidays": cmd_holidays}[a.cmd]
    try:
        return fn(a)
    except KeyboardInterrupt:
        print("Stopped.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
