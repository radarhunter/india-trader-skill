#!/usr/bin/env python3
"""nse-risk: the Risk Manager. Checks every order in trader/orders.json
against trader/config.json and stops anything over the limits before it can
reach a broker. Prints PASS or BLOCK with every reason, and records the
verdicts in trader/risk.json so nse-broker only shows an order that passed.

Checks for a BUY (new entry):
  config present, a stop present, loss at the stop (from the higher of the
  plan entry and a fresh price, charges included) within max loss a trade,
  reward:risk >= minimum, today's realised loss + open risk within the daily
  limit, trades per day, orders per day, entry window, market open (IST),
  not a blocked symbol, not ASM/GSM, not F&O/short/margin/BSE unless
  allowed, notional within max order, position cap and cash buffer, no
  duplicate of a live order, a well-formed bracket, price not within
  max_circuit_distance_pct of the upper circuit, no results due today
  (avoid_results_day), stop limit under the trigger.

A SELL that closes a position is never blocked by a daily limit.
Exit 0 when every order passed, 1 when any was blocked or nothing to check.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import urllib.parse
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402


def order_id(o):
    key = json.dumps({k: o.get(k) for k in ("symbol", "side", "qty", "price", "product", "bracket")}, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


_SURV = {}


def fresh_quote(sym, sess, a):
    """NSE live quote (price, bands, F&O flag) + ASM/GSM from the surveillance lists, with Yahoo fallback."""
    q = {"last": None, "upper_cp": None, "lower_cp": None, "surveillance": None, "fno": None, "source": None, "tick": None}
    if not a.no_fetch and sess is not None:
        global _SURV
        if not _SURV:
            _SURV = nc.nse_surveillance(sess) or {"_": None}
        q["surveillance"] = _SURV.get(sym)
        try:
            n = nc.nse_quote(sess, sym)
            q.update({"last": n["last"], "prev_close": n["prev_close"], "upper_cp": n["upper_cp"], "lower_cp": n["lower_cp"],
                      "fno": n["fno"], "tick": n["tick"], "source": "nse", "band_pct": n.get("band_pct"),
                      "margin_pct": n.get("margin_pct"), "suspended": n.get("suspended")})
            return q
        except nc.DataError:
            pass
    if not a.no_fetch:
        try:
            y = nc.yahoo_quote_fast(sym)
            q["last"], q["prev_close"], q["source"] = y["last"], y["prev_close"], "yahoo"
        except nc.DataError:
            pass
    return q


def log_path():
    return nc.trader_dir() / "trades_log.json"


def todays_log(today):
    doc = nc.read_json(log_path(), {}) or {}
    rows = [r for r in (doc.get("trades") or []) if str(r.get("date")) == today]
    return rows


def check_order(o, cfg, ctx):
    reasons, warns = [], []
    side = (o.get("side") or "").upper()
    sym = (o.get("symbol") or "").upper()
    qty = int(o.get("qty") or 0)
    price = float(o.get("price") or 0)
    product = (o.get("product") or cfg.get("product", "MIS")).upper()
    br = o.get("bracket") or {}
    closing = side == "SELL"
    if not sym or qty <= 0 or price <= 0:
        reasons.append("malformed order: symbol, qty and price are required")
        return reasons, warns
    if sym in [s.upper() for s in cfg.get("blocked", [])]:
        reasons.append("%s is on your blocked list" % sym)
    if (o.get("exchange") or "NSE").upper() == "BSE" and not cfg.get("allow_bse"):
        reasons.append("BSE orders are off (allow_bse)")
    if o.get("instrument", "EQ").upper() in ("FUT", "OPT", "CE", "PE") and not cfg.get("allow_fno"):
        reasons.append("F&O is off (allow_fno)")
    if closing and not o.get("closes_position", True):
        if not cfg.get("allow_short"):
            reasons.append("a short sale is off (allow_short)")
    q = ctx["quotes"].get(sym) or {}
    if q.get("surveillance") and cfg.get("avoid_asm_gsm", True) and not closing:
        reasons.append("%s is under surveillance (%s)" % (sym, q["surveillance"]))
    elif not closing and ctx["universe_tags"].get(sym) and any(t in ctx["universe_tags"][sym] for t in ("ASM", "GSM")) and cfg.get("avoid_asm_gsm", True):
        reasons.append("%s is tagged %s in the universe" % (sym, ctx["universe_tags"][sym]))
    notional = qty * price
    if not closing:
        if notional > float(cfg.get("max_order_inr", 1e18)) + 1e-6:
            reasons.append("notional %s is over max order %s" % (nc.inr(notional), nc.inr(cfg["max_order_inr"])))
        if notional < float(cfg.get("min_order_inr", 0)):
            reasons.append("notional %s is under min order %s" % (nc.inr(notional), nc.inr(cfg["min_order_inr"])))
        cap = float(cfg["budget_inr"]) * cfg.get("max_position_pct", 40) / 100
        if notional > cap + 1e-6:
            reasons.append("notional %s is over the position cap %s (%s%% of budget)" % (nc.inr(notional), nc.inr(cap), cfg.get("max_position_pct", 40)))
        usable = float(cfg["budget_inr"]) * (1 - cfg.get("cash_buffer_pct", 10) / 100)
        if notional > usable + 1e-6 and not cfg.get("allow_margin"):
            reasons.append("notional %s needs margin: cash after the %s%% buffer is %s (allow_margin is off)" % (
                nc.inr(notional), cfg.get("cash_buffer_pct", 10), nc.inr(usable)))
        if product == "MIS" and cfg.get("product", "MIS") == "CNC":
            warns.append("order is MIS but config product is CNC")
        # stop and loss
        if cfg.get("require_stop", True) and not br.get("stop_trigger"):
            reasons.append("no stop on the order (require_stop)")
        stop = float(br.get("stop_trigger") or 0)
        target = float(br.get("target") or 0)
        if stop:
            if stop >= price:
                reasons.append("stop %.2f is not below the entry %.2f" % (stop, price))
            if br.get("stop_limit") is not None and float(br["stop_limit"]) > stop:
                reasons.append("stop limit %.2f is above the stop trigger %.2f" % (float(br["stop_limit"]), stop))
            ref = price
            if q.get("last") and float(q["last"]) > price:
                ref = float(q["last"])
                warns.append("fresh price %.2f is above the plan entry %.2f; loss measured from the fresh price" % (ref, price))
            risk_share = ref - stop
            charges = float((o.get("charges_at_stop") or {}).get("total") or 0)
            loss = qty * risk_share + charges
            if loss > float(cfg["max_loss_per_trade_inr"]) + 1e-6:
                reasons.append("loss at the stop %s (incl. charges %s) is over max loss a trade %s" % (
                    nc.inr(loss), nc.inr(charges), nc.inr(cfg["max_loss_per_trade_inr"])))
            if target:
                if target <= price:
                    reasons.append("target %.2f is not above the entry %.2f" % (target, price))
                rr = (target - ref) / risk_share if risk_share > 0 else 0
                if rr < float(cfg.get("min_reward_risk", 1.0)) - 1e-9:
                    reasons.append("reward:risk %.2f from the fresh price is under your minimum %.2f" % (rr, cfg.get("min_reward_risk", 1.0)))
            # daily limit: realised losses today + open risk + this order
            day_loss = ctx["day_realised_loss"] + ctx["day_open_risk"]
            if day_loss + loss > float(cfg["max_daily_loss_inr"]) + 1e-6:
                reasons.append("today's loss %s + open risk %s + this trade's %s would pass the daily limit %s" % (
                    nc.inr(ctx["day_realised_loss"]), nc.inr(ctx["day_open_risk"]), nc.inr(loss), nc.inr(cfg["max_daily_loss_inr"])))
            ctx["pending_open_risk"] += loss
        if ctx["day_realised_loss"] >= float(cfg["max_daily_loss_inr"]) - 1e-6:
            reasons.append("the daily loss limit %s is already reached; no new entries today" % nc.inr(cfg["max_daily_loss_inr"]))
        if ctx["day_trades"] + ctx["pending_trades"] >= int(cfg.get("max_trades_per_day", 3)):
            reasons.append("already %d trade(s) today; max is %d" % (ctx["day_trades"] + ctx["pending_trades"], cfg.get("max_trades_per_day", 3)))
        ctx["pending_trades"] += 1
        # window and market
        t = ctx["now"]
        nb = nc.parse_hhmm(cfg.get("no_entries_before"), dt.time(9, 30))
        na = nc.parse_hhmm(cfg.get("no_entries_after"), dt.time(14, 45))
        if product == "MIS" or o.get("horizon") == "intraday":
            if not (nb <= t.time() <= na):
                reasons.append("outside the entry window %s-%s IST (now %s)" % (nb.strftime("%H:%M"), na.strftime("%H:%M"), t.strftime("%H:%M")))
        ms = nc.market_status(t)
        if not ms["is_open"] and not ctx["allow_closed"]:
            reasons.append("market is not open (%s); next open %s" % (ms["phase"], ms["next_open_ist"]))
        # circuit
        if q.get("upper_cp") and q.get("last"):
            dist = (float(q["upper_cp"]) - float(q["last"])) / float(q["last"]) * 100
            if dist < float(cfg.get("max_circuit_distance_pct", 2.0)):
                reasons.append("price %.2f is %.2f%% from the upper circuit %.2f; a buy there cannot exit" % (float(q["last"]), dist, float(q["upper_cp"])))
        if q.get("suspended"):
            reasons.append("%s is suspended from trading" % sym)
        if q.get("fno") and not cfg.get("allow_fno") and o.get("instrument", "EQ").upper() != "EQ":
            reasons.append("derivative on %s but allow_fno is off" % sym)
        # results day
        if cfg.get("avoid_results_day", True) and sym in ctx["results_today"] and product == "MIS":
            reasons.append("results are due today for %s (board meeting); intraday entries skipped (avoid_results_day)" % sym)
        # duplicates
        if sym in ctx["live_symbols"]:
            reasons.append("there is already a live order or position in %s" % sym)
    if ctx["day_orders"] + ctx["pending_orders"] >= int(cfg.get("max_orders_per_day", 10)):
        reasons.append("already %d orders today; max is %d" % (ctx["day_orders"] + ctx["pending_orders"], cfg.get("max_orders_per_day", 10)))
    ctx["pending_orders"] += 1
    return reasons, warns


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders", help="default trader/orders.json")
    ap.add_argument("--now", help="pin the clock 'YYYY-MM-DD HH:MM' IST")
    ap.add_argument("--no-fetch", action="store_true", help="do not fetch fresh prices")
    ap.add_argument("--allow-closed", action="store_true", help="paper rehearsal when the market is shut")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = nc.load_config(strict=True)
    except nc.ConfigError as e:
        print("BLOCK: %s" % e)
        return 1
    doc = nc.read_json(a.orders or (nc.trader_dir() / "orders.json"), None)
    orders = (doc or {}).get("orders") or []
    if not orders:
        print("Nothing to check: no orders in trader/orders.json (run /nse-budget size).")
        return 1
    t = nc.now_ist(a.now)
    today = t.date().isoformat()
    log = todays_log(today)
    realised = sum(-float(r.get("pnl") or 0) for r in log if r.get("status") == "closed" and r.get("side") == "SELL" and float(r.get("pnl") or 0) < 0)
    open_risk = sum(float(r.get("loss_at_stop_inr", 0)) for r in log if r.get("status") == "open")
    live_syms = {r["symbol"].upper() for r in log if r.get("status") in ("open", "placed")}
    holdings = nc.read_json(cfg.get("holdings_file", "trader/holdings.json"), {}) or {}
    for p in holdings.get("positions") or []:
        if (p.get("product") or "CNC").upper() == "MIS":
            live_syms.add(p["symbol"].upper())
    news = nc.read_json(nc.trader_dir() / "news.json", {}) or {}
    results_today = set()
    for e in news.get("report") or []:
        for r in e.get("results_due") or []:
            if r.get("date") == today:
                results_today.add(e["symbol"].upper())
    tags = {}
    up = nc.trader_dir() / "universe.txt"
    if up.exists():
        for line in up.read_text(encoding="utf-8").splitlines():
            parts = line.split("|")
            if len(parts) >= 4:
                tags[parts[0].strip().upper()] = parts[3].strip()
    sess = None if a.no_fetch else nc.NSESession()
    quotes = {}
    for o in orders:
        s = (o.get("symbol") or "").upper()
        if s and s not in quotes:
            quotes[s] = fresh_quote(s, sess, a)
    ctx = {"now": t, "quotes": quotes, "universe_tags": tags, "day_realised_loss": realised, "day_open_risk": open_risk,
           "day_trades": sum(1 for r in log if r.get("side") == "BUY"), "day_orders": len(log),
           "pending_open_risk": 0.0, "pending_trades": 0, "pending_orders": 0, "live_symbols": live_syms,
           "results_today": results_today, "allow_closed": a.allow_closed}
    verdicts = []
    all_pass = True
    for o in orders:
        reasons, warns = check_order(o, cfg, ctx)
        v = {"order_id": order_id(o), "symbol": o.get("symbol"), "side": o.get("side"), "qty": o.get("qty"),
             "price": o.get("price"), "verdict": "PASS" if not reasons else "BLOCK", "reasons": reasons, "warnings": warns,
             "checked_at": t.strftime("%Y-%m-%d %H:%M IST"), "fresh_price": (quotes.get((o.get("symbol") or "").upper()) or {}).get("last"),
             "price_source": (quotes.get((o.get("symbol") or "").upper()) or {}).get("source")}
        verdicts.append(v)
        all_pass = all_pass and not reasons
    out = {"checked_at": t.strftime("%Y-%m-%d %H:%M IST"), "config": cfg.get("_path"), "verdicts": verdicts,
           "day": {"realised_loss_inr": round(realised, 2), "open_risk_inr": round(open_risk, 2),
                   "trades": ctx["day_trades"], "orders": ctx["day_orders"]}}
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        for v in verdicts:
            print("%s  %s %s x%s @ %s%s" % (v["verdict"], v["side"], v["symbol"], v["qty"], v["price"],
                                            ("  (fresh %.2f, %s)" % (v["fresh_price"], v["price_source"])) if v["fresh_price"] else ""))
            for r in v["reasons"]:
                print("   - " + r)
            for w in v["warnings"]:
                print("   ~ " + w)
        print("Today so far: realised loss %s, open risk %s, %d trade(s), %d order(s). Daily limit %s." % (
            nc.inr(realised), nc.inr(open_risk), ctx["day_trades"], ctx["day_orders"], nc.inr(cfg["max_daily_loss_inr"])))
    if not a.no_write:
        nc.write_json(nc.ensure_trader_dir() / "risk.json", out)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
