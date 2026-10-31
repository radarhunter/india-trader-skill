#!/usr/bin/env python3
"""nse-budget: the Budget Manager. Sizes every position to your budget and
your max loss per trade, in rupees, and writes ONE bracket order.

  size      Read trader/plan.json (entry, stop, target), find the whole
            shares that fit inside four limits - max loss per trade (after
            STT, brokerage, stamp duty, exchange charges and GST on the
            round trip), position cap, max order, cash after the buffer -
            say which limit decided it, and write trader/orders.json: a
            broker-neutral bracket (entry limit, stop-loss trigger + limit,
            target) that nse-risk checks and nse-broker translates.
  config    Write trader/config.json from flags (the "set my rules" step).
  charges   Show the charge arithmetic for a round trip.

A stop too far away for your max loss is a plain refusal, not a smaller
order. Nothing is placed. Exit 0 = an order was written / config saved.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402

# Statutory and exchange charges on NSE cash equity (rates in percent of turnover).
# STT: intraday 0.025% on the sell side; delivery 0.1% on both sides.
# Exchange transaction charge NSE ~0.00297%, SEBI turnover fee 0.0001%,
# stamp duty on the buy side: intraday 0.003%, delivery 0.015%. GST 18% on
# brokerage + exchange + SEBI charges. Brokerage per the config (discount
# broker default: min(0.03% of turnover, Rs 20) an order intraday, Rs 0 delivery).
RATES = {
    "MIS": {"stt_buy": 0.0, "stt_sell": 0.025, "stamp_buy": 0.003, "exch": 0.00297, "sebi": 0.0001},
    "CNC": {"stt_buy": 0.1, "stt_sell": 0.1, "stamp_buy": 0.015, "exch": 0.00297, "sebi": 0.0001},
}


def brokerage(turnover, product, cfg):
    if product == "CNC":
        return 0.0
    return min(turnover * cfg.get("brokerage_pct_intraday", 0.03) / 100, cfg.get("brokerage_per_order_inr", 20))


def round_trip_charges(qty, buy_px, sell_px, product, cfg):
    r = RATES.get(product, RATES["MIS"])
    tb, ts = qty * buy_px, qty * sell_px
    stt = tb * r["stt_buy"] / 100 + ts * r["stt_sell"] / 100
    exch = (tb + ts) * r["exch"] / 100
    sebi = (tb + ts) * r["sebi"] / 100
    stamp = tb * r["stamp_buy"] / 100
    brk = brokerage(tb, product, cfg) + brokerage(ts, product, cfg)
    gst = (brk + exch + sebi) * 0.18
    total = stt + exch + sebi + stamp + brk + gst
    return {"stt": round(stt, 2), "exchange": round(exch, 2), "sebi": round(sebi, 2), "stamp": round(stamp, 2),
            "brokerage": round(brk, 2), "gst": round(gst, 2), "total": round(total, 2)}


def size_order(plan, cfg, cash_available=None):
    entry, stop, target = float(plan["entry"]), float(plan["stop"]), float(plan["target"])
    risk = entry - stop
    if risk <= 0:
        raise nc.DataError("the plan's stop is not below the entry")
    product = plan.get("product") or cfg.get("product", "MIS")
    budget = float(cfg["budget_inr"])
    cash = float(cash_available) if cash_available is not None else budget
    usable = min(cash, budget) * (1 - cfg.get("cash_buffer_pct", 10) / 100)
    limits = {}
    max_loss = float(cfg["max_loss_per_trade_inr"])
    # iterate: charges depend on qty
    q = int(max_loss // risk)
    for _ in range(5):
        if q <= 0:
            break
        ch = round_trip_charges(q, entry, stop, product, cfg)
        q2 = int((max_loss - ch["total"]) // risk)
        if q2 == q:
            break
        q = max(0, q2)
    limits["max_loss_per_trade"] = q
    limits["position_cap"] = int(budget * cfg.get("max_position_pct", 40) / 100 // entry)
    limits["max_order"] = int(float(cfg.get("max_order_inr", budget)) // entry)
    limits["cash_after_buffer"] = int(usable // entry)
    qty = min(limits.values())
    decided = min(limits, key=limits.get)
    if qty <= 0:
        if limits["max_loss_per_trade"] <= 0:
            raise nc.DataError("refused: one share risks %s (plus charges) and your max loss a trade is %s; the stop is too far away" % (
                nc.inr(risk), nc.inr(max_loss)))
        raise nc.DataError("refused: %s allows 0 shares at %s" % (decided, nc.inr(entry)))
    notional = qty * entry
    if notional < float(cfg.get("min_order_inr", 0)):
        raise nc.DataError("refused: %d shares is %s, under your minimum order %s" % (qty, nc.inr(notional), nc.inr(cfg["min_order_inr"])))
    ch_stop = round_trip_charges(qty, entry, stop, product, cfg)
    ch_target = round_trip_charges(qty, entry, target, product, cfg)
    loss_at_stop = qty * risk + ch_stop["total"]
    gain_at_target = qty * (target - entry) - ch_target["total"]
    tick = plan.get("tick") or nc.tick_for(entry)
    limit_px = nc.tick_round(entry + tick, down=False, tick=tick)
    sl_trigger = stop
    sl_limit = nc.tick_round(stop - 3 * tick, down=True, tick=tick)
    order = {
        "symbol": plan["symbol"], "exchange": cfg.get("exchange", "NSE"), "side": "BUY", "product": product,
        "qty": qty, "order_type": "LIMIT", "price": limit_px,
        "bracket": {"stop_trigger": sl_trigger, "stop_limit": sl_limit, "target": target},
        "horizon": plan.get("horizon", "intraday"), "flat_by": plan.get("flat_by"),
        "notional_inr": round(notional, 2), "risk_per_share": round(risk, 2),
        "loss_at_stop_inr": round(loss_at_stop, 2), "gain_at_target_inr": round(gain_at_target, 2),
        "charges_at_stop": ch_stop, "charges_at_target": ch_target,
        "reward_risk_after_charges": round(gain_at_target / loss_at_stop, 2) if loss_at_stop else None,
        "limits": limits, "decided_by": decided, "paper": bool(cfg.get("paper", True)),
        "tag": "nse-skill",
    }
    return order


def cmd_size(a):
    cfg = nc.load_config(strict=True)
    plan = nc.read_json(a.plan_file or (nc.trader_dir() / "plan.json"), None)
    if not plan:
        print("Budget could not run: no trader/plan.json - run /nse-exit first")
        return 1
    if a.max_loss:
        cfg["max_loss_per_trade_inr"] = a.max_loss
    try:
        order = size_order(plan, cfg, a.cash)
    except nc.DataError as e:
        print("Budget %s: %s" % (plan.get("symbol"), e))
        return 1
    order["as_of"] = nc.now_ist().strftime("%Y-%m-%d %H:%M IST")
    doc = {"as_of": order["as_of"], "orders": [order], "config_path": cfg.get("_path")}
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print("%s: %d shares at a %s limit (entry %.2f + 1 tick). Decided by %s." % (
            order["symbol"], order["qty"], nc.inr(order["price"]), plan["entry"], order["decided_by"].replace("_", " ")))
        print("  Limits: max loss %d, position cap %d, max order %d, cash after buffer %d shares." % (
            order["limits"]["max_loss_per_trade"], order["limits"]["position_cap"], order["limits"]["max_order"], order["limits"]["cash_after_buffer"]))
        print("  Notional %s. Loss at the stop %s (incl. charges %s). Gain at the target %s (after charges %s). R after charges %.2f." % (
            nc.inr(order["notional_inr"]), nc.inr(order["loss_at_stop_inr"]), nc.inr(order["charges_at_stop"]["total"]),
            nc.inr(order["gain_at_target_inr"]), nc.inr(order["charges_at_target"]["total"]), order["reward_risk_after_charges"] or 0))
        print("  Bracket: stop trigger %.2f (limit %.2f), target %.2f. %s, %s." % (
            order["bracket"]["stop_trigger"], order["bracket"]["stop_limit"], order["bracket"]["target"], order["product"],
            "paper" if order["paper"] else "LIVE"))
    if not a.no_write:
        p = nc.write_json(nc.ensure_trader_dir() / "orders.json", doc)
        if not a.json:
            print("Wrote %s. Nothing is placed. Next: /nse-risk, then /nse-broker." % p)
    return 0


def cmd_config(a):
    cfg = nc.load_config()
    ex = json.loads((_here / "config.example.json").read_text(encoding="utf-8"))
    out = dict(ex)
    if cfg.get("_found"):
        out.update({k: v for k, v in cfg.items() if not k.startswith("_")})
    for key in ("budget_inr", "max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day", "max_position_pct",
                "max_order_inr", "cash_buffer_pct", "min_reward_risk", "no_entries_before", "no_entries_after", "flat_by",
                "product", "broker", "swing_stop_pct", "swing_max_hold_days", "user_agent"):
        v = getattr(a, key, None)
        if v is not None:
            out[key] = v
    if a.paper is not None:
        out["paper"] = a.paper
    if a.blocked is not None:
        out["blocked"] = [s.strip().upper() for s in a.blocked.split(",") if s.strip()]
    # sanity
    errs = []
    if out["max_loss_per_trade_inr"] > out["max_daily_loss_inr"]:
        errs.append("max loss a trade (%s) is above the daily loss limit (%s)" % (nc.inr(out["max_loss_per_trade_inr"]), nc.inr(out["max_daily_loss_inr"])))
    if out["max_loss_per_trade_inr"] > out["budget_inr"] * 0.05:
        errs.append("max loss a trade is over 5%% of the budget (%s of %s); 1%% is the usual rule" % (nc.inr(out["max_loss_per_trade_inr"]), nc.inr(out["budget_inr"])))
    if nc.parse_hhmm(out["no_entries_after"]) >= nc.parse_hhmm(out["flat_by"]):
        errs.append("no_entries_after must be before flat_by")
    if nc.parse_hhmm(out["flat_by"]) > nc.BROKER_SQUARE_OFF and out["product"] == "MIS":
        errs.append("flat_by %s is after most brokers' MIS square-off (15:15 IST)" % out["flat_by"])
    if errs and not a.force:
        print("Config not saved:")
        for e in errs:
            print("  - " + e)
        print("Fix the flags or pass --force.")
        return 1
    p = nc.write_json(nc.config_path(), out)
    print("Saved %s:" % p)
    for k in ("budget_inr", "max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day", "no_entries_before",
              "no_entries_after", "flat_by", "product", "broker", "paper"):
        print("  %-26s %s" % (k, nc.inr(out[k], 0) if k.endswith("_inr") else out[k]))
    for e in errs:
        print("  warning: " + e)
    return 0


def cmd_charges(a):
    cfg = nc.load_config()
    ch = round_trip_charges(a.qty, a.buy, a.sell, a.product, cfg)
    gross = a.qty * (a.sell - a.buy)
    print("%d shares, buy %.2f sell %.2f, %s: gross %s, charges %s (STT %s, brokerage %s, stamp %s, exch %s, SEBI %s, GST %s), net %s" % (
        a.qty, a.buy, a.sell, a.product, nc.inr(gross), nc.inr(ch["total"]), nc.inr(ch["stt"]), nc.inr(ch["brokerage"]),
        nc.inr(ch["stamp"]), nc.inr(ch["exchange"]), nc.inr(ch["sebi"]), nc.inr(ch["gst"]), nc.inr(gross - ch["total"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("size")
    s.add_argument("--plan-file")
    s.add_argument("--cash", type=float, help="cash actually available at the broker (default: the budget)")
    s.add_argument("--max-loss", type=float, help="override max_loss_per_trade_inr for this order")
    s.add_argument("--no-write", action="store_true")
    s.add_argument("--json", action="store_true")
    c = sub.add_parser("config")
    for key, typ in (("budget_inr", float), ("max_loss_per_trade_inr", float), ("max_daily_loss_inr", float),
                     ("max_trades_per_day", int), ("max_position_pct", float), ("max_order_inr", float),
                     ("cash_buffer_pct", float), ("min_reward_risk", float), ("swing_stop_pct", float),
                     ("swing_max_hold_days", int)):
        c.add_argument("--" + key.replace("_", "-"), dest=key, type=typ)
    for key in ("no_entries_before", "no_entries_after", "flat_by", "product", "broker", "user_agent"):
        c.add_argument("--" + key.replace("_", "-"), dest=key)
    c.add_argument("--paper", dest="paper", action="store_true", default=None)
    c.add_argument("--live", dest="paper", action="store_false")
    c.add_argument("--blocked", help="comma list of symbols never to trade")
    c.add_argument("--force", action="store_true")
    h = sub.add_parser("charges")
    h.add_argument("--qty", type=int, required=True)
    h.add_argument("--buy", type=float, required=True)
    h.add_argument("--sell", type=float, required=True)
    h.add_argument("--product", default="MIS", choices=["MIS", "CNC"])
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"size": cmd_size, "config": cmd_config, "charges": cmd_charges}[a.cmd](a)
    except nc.ConfigError as e:
        print("Budget could not run: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
