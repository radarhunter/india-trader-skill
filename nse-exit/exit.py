#!/usr/bin/env python3
"""nse-exit: the Exit Planner. Sets the stop and the target before you enter.

Reads the entry from trader/entry.json (or --entry PRICE), puts the stop at
the opening-range midpoint by default (or --stop or-low | PRICE | 1.5% |
1.5atr), rounds it DOWN to the Rs 0.05 tick, sets the target at 2R (or
--target 1R | PRICE | 3%), rounds it DOWN too, and refuses a plan that makes
no sense: stop at or above the entry, target at or below it, a stop so tight
the spread eats it, or a reward:risk under the config minimum. Writes
trader/plan.json for nse-timemachine, nse-budget and nse-alerts.

Long side only. Nothing is placed. Exit 0 = a plan was written.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402



def parse_stop(spec, entry, or_high, or_low, atr_val):
    s = (spec or "or-mid").strip().lower()
    if s in ("or-mid", "mid", "midpoint"):
        if or_high is None or or_low is None:
            raise nc.DataError("no opening range in the entry; give --stop PRICE or --stop 1.5%")
        return (or_high + or_low) / 2.0, "opening-range midpoint"
    if s in ("or-low", "low", "range-low"):
        if or_low is None:
            raise nc.DataError("no opening range in the entry; give --stop PRICE")
        return or_low, "opening-range low"
    m = re.fullmatch(r"([\d.]+)\s*%", s)
    if m:
        p = float(m.group(1))
        return entry * (1 - p / 100), "%.2f%% below the entry" % p
    m = re.fullmatch(r"([\d.]+)\s*atr", s)
    if m:
        if not atr_val:
            raise nc.DataError("ATR is not available for this symbol; give --stop PRICE")
        k = float(m.group(1))
        return entry - k * atr_val, "%.1f x ATR(14) %.2f below the entry" % (k, atr_val)
    try:
        return float(s), "your price"
    except ValueError:
        raise nc.DataError("cannot read --stop %r (or-mid, or-low, PRICE, 1.5%%, 1.5atr)" % spec)


def parse_target(spec, entry, risk):
    s = (spec or "2R").strip().lower()
    m = re.fullmatch(r"([\d.]+)\s*r", s)
    if m:
        k = float(m.group(1))
        return entry + k * risk, "%g R" % k
    m = re.fullmatch(r"([\d.]+)\s*%", s)
    if m:
        p = float(m.group(1))
        return entry * (1 + p / 100), "%.2f%% above the entry" % p
    try:
        return float(s), "your price"
    except ValueError:
        raise nc.DataError("cannot read --target %r (2R, 1R, PRICE, 3%%)" % spec)


def build_plan(entry, stop_spec, target_spec, or_high=None, or_low=None, atr_val=None, min_rr=1.0,
               min_stop_pct=0.25, flat_by="15:10"):
    stop_raw, stop_why = parse_stop(stop_spec, entry, or_high, or_low, atr_val)
    stop = nc.tick_round(stop_raw, down=True)
    if stop >= entry:
        raise nc.DataError("refused: the stop %.2f is at or above the entry %.2f" % (stop, entry))
    risk = entry - stop
    if risk / entry * 100 < min_stop_pct:
        raise nc.DataError("refused: the stop is only %.2f%% below the entry; the spread and slippage would eat it (min %.2f%%)" % (
            risk / entry * 100, min_stop_pct))
    target_raw, target_why = parse_target(target_spec, entry, risk)
    target = nc.tick_round(target_raw, down=True)
    if target <= entry:
        raise nc.DataError("refused: the target %.2f is at or below the entry %.2f" % (target, entry))
    reward = target - entry
    rr = reward / risk
    if rr < min_rr:
        raise nc.DataError("refused: reward:risk %.2f is under your minimum %.2f (stop %.2f, target %.2f)" % (
            rr, min_rr, stop, target))
    return {
        "side": "long", "entry": round(entry, 2), "stop": stop, "target": target,
        "risk_per_share": round(risk, 2), "reward_per_share": round(reward, 2), "reward_risk": round(rr, 2),
        "stop_pct": round(risk / entry * 100, 2), "target_pct": round(reward / entry * 100, 2),
        "stop_rule": stop_why, "target_rule": target_why, "flat_by": flat_by, "tick": nc.tick_for(entry),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("--entry", type=float, help="use this entry price instead of trader/entry.json")
    ap.add_argument("--stop", default="or-mid", help="or-mid | or-low | PRICE | 1.5%% | 1.5atr")
    ap.add_argument("--target", default="2R", help="2R | 1R | PRICE | 3%%")
    ap.add_argument("--min-rr", type=float, help="override config min_reward_risk")
    ap.add_argument("--min-stop-pct", type=float, default=0.25)
    ap.add_argument("--swing", action="store_true", help="a positional plan: default stop 3%% (config swing_stop_pct), target 2R, no flat-by")
    ap.add_argument("--entry-file", default=None)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = nc.load_config()
    ent = nc.read_json(a.entry_file or (nc.trader_dir() / "entry.json"), {}) or {}
    sym = (a.symbol or ent.get("symbol") or "").upper()
    if not sym:
        print("Exit could not run: no symbol - run /nse-entry first or pass SYMBOL --entry PRICE")
        return 1
    entry = a.entry if a.entry else (ent.get("entry") if ent.get("symbol", "").upper() == sym else None)
    if not entry:
        print("Exit could not run: no entry price for %s - run /nse-entry or pass --entry PRICE" % sym)
        return 1
    or_high, or_low = ent.get("or_high"), ent.get("or_low")
    atr_val = None
    if "atr" in a.stop.lower() or a.swing:
        try:
            ch = nc.yahoo_chart(sym, "3mo", "1d")
            atr_val = nc.atr(nc.clean_bars(ch), 14)
        except nc.DataError:
            atr_val = None
    stop_spec = a.stop
    if a.swing and a.stop == "or-mid":
        stop_spec = "%s%%" % cfg.get("swing_stop_pct", 3.0)
    min_rr = a.min_rr if a.min_rr is not None else float(cfg.get("min_reward_risk", 1.0))
    try:
        plan = build_plan(float(entry), stop_spec, a.target, or_high, or_low, atr_val, min_rr, a.min_stop_pct,
                          flat_by=None if a.swing else cfg.get("flat_by", "15:10"))
    except nc.DataError as e:
        print("Exit %s: %s" % (sym, e))
        return 1
    plan.update({"symbol": sym, "horizon": "swing" if a.swing else "intraday", "product": "CNC" if a.swing else cfg.get("product", "MIS"),
                 "as_of": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "or_high": or_high, "or_low": or_low,
                 "atr14": round(atr_val, 2) if atr_val else None, "entry_time": ent.get("trigger_time"),
                 "max_hold_days": cfg.get("swing_max_hold_days", 5) if a.swing else 0})
    if a.json:
        print(json.dumps(plan, indent=2))
    else:
        print("%s %s plan: entry %.2f, stop %.2f (%s, -%.2f%%), target %.2f (%s, +%.2f%%), %.2f R%s" % (
            sym, plan["horizon"], plan["entry"], plan["stop"], plan["stop_rule"], plan["stop_pct"], plan["target"],
            plan["target_rule"], plan["target_pct"], plan["reward_risk"],
            (", flat by %s IST" % plan["flat_by"]) if plan["flat_by"] else ", hold up to %d days" % plan["max_hold_days"]))
        print("Risk %.2f a share. A stop is a plan, not a promise: across a gap it can fill well past %.2f." % (plan["risk_per_share"], plan["stop"]))
    if not a.no_write:
        p = nc.write_json(nc.ensure_trader_dir() / "plan.json", plan)
        if not a.json:
            print("Wrote %s. Next: /nse-timemachine, then /nse-budget." % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
