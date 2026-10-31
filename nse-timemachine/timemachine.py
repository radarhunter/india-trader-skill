#!/usr/bin/env python3
"""nse-timemachine: replays every possible outcome of a trade before you risk a rupee.

Takes the plan from trader/plan.json (entry, stop, target, flat-by), pulls
60 sessions of the stock's own 5-minute bars from Yahoo, and

  1. SIMULATES the rest of the day 10,000 times (seeded): from the entry
     time to flat_by it draws, bar by bar, a real historical 5-minute bar's
     shape (open->high, open->low, open->close as fractions) and applies it
     to the running price. Target hit = bar high >= target; stop hit = bar
     low <= stop; both in one bar counts as the stop (conservative).
  2. REPLAYS the same rule on every past session: the opening range, the
     first close above it, the stop at the midpoint, the target at the same
     R multiple, flat at the flat-by time. Counts target / stop / neither.

Prints the odds, where the price sits at the close, the average R, the
history and one honest verdict, and writes trader/timemachine.json.
--swing does the same on daily bars over the plan's max_hold_days.

A simulation is not a prediction. The future can do things those 60 days
never did. Exit 0 when it ran.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import random
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402


def load_history(sym, a, interval="5m", rng="60d"):
    if a.offline:
        p = Path(a.offline)
        raw = gzip.open(p, "rt", encoding="utf-8").read() if p.suffix == ".gz" else p.read_text(encoding="utf-8")
        ch = nc.parse_chart(json.loads(raw), sym)
    else:
        ch = nc.yahoo_chart(sym, rng, interval)
    return nc.clean_bars(ch)


def bar_shapes(bars):
    """(h/o-1, l/o-1, c/o-1) for every bar with a positive open."""
    out = []
    for _, o, h, lo, c, _v in bars:
        if o and o > 0:
            out.append(((h - o) / o, (lo - o) / o, (c - o) / o))
    return out


def simulate(entry, stop, target, n_bars, shapes, n=10000, seed=19, time_of_day_shapes=None):
    rnd = random.Random(seed)
    res = {"target": 0, "stop": 0, "open": 0}
    closes, rs = [], []
    risk = entry - stop
    for _ in range(n):
        price = entry
        outcome = None
        for k in range(n_bars):
            pool = time_of_day_shapes[k] if time_of_day_shapes and time_of_day_shapes[k] else shapes
            hh, ll, cc = pool[rnd.randrange(len(pool))]
            bar_h, bar_l, bar_c = price * (1 + hh), price * (1 + ll), price * (1 + cc)
            if bar_l <= stop:
                outcome = "stop"
                break
            if bar_h >= target:
                outcome = "target"
                break
            price = bar_c
        if outcome == "stop":
            res["stop"] += 1
            rs.append(-1.0)
        elif outcome == "target":
            res["target"] += 1
            rs.append((target - entry) / risk)
        else:
            res["open"] += 1
            closes.append(price)
            rs.append((price - entry) / risk)
    out = {k: round(v / n * 100, 1) for k, v in res.items()}
    out["avg_R"] = round(sum(rs) / len(rs), 2) if rs else 0
    out["expectancy_pct_of_entry"] = round(out["avg_R"] * (risk / entry) * 100, 2)
    if closes:
        closes.sort()
        out["open_close_median"] = round(closes[len(closes) // 2], 2)
        out["open_close_p25"] = round(closes[len(closes) // 4], 2)
        out["open_close_p75"] = round(closes[3 * len(closes) // 4], 2)
        out["open_above_entry_pct"] = round(sum(1 for c in closes if c > entry) / len(closes) * 100, 1)
    return out


def replay_history(bars, or_minutes, stop_rule, r_mult, flat_by, no_after, entry_day=None):
    """Run the ORB rule on every session in `bars`."""
    days = nc.bars_by_day(bars)
    rows = []
    for day, dbars in sorted(days.items()):
        if entry_day and day == entry_day:
            continue
        dbars = nc.session_bars(dbars, day)
        start = dt.datetime.combine(dt.date.fromisoformat(day), nc.OPEN, nc.IST)
        or_end = start + dt.timedelta(minutes=or_minutes)
        inside = [b for b in dbars if start.timestamp() <= b[0] < or_end.timestamp()]
        if len(inside) < max(1, or_minutes // 5):
            continue
        hi, lo = max(b[2] for b in inside), min(b[3] for b in inside)
        entry = None
        for b in dbars:
            if b[0] < or_end.timestamp():
                continue
            t = dt.datetime.fromtimestamp(b[0], nc.IST).time()
            if t > no_after:
                break
            if b[4] > hi:
                entry, entry_ts = b[4], b[0]
                break
        if entry is None:
            rows.append({"day": day, "fired": False})
            continue
        stop = (hi + lo) / 2 if stop_rule == "or-mid" else lo
        risk = entry - stop
        if risk <= 0:
            rows.append({"day": day, "fired": False})
            continue
        target = entry + r_mult * risk
        outcome, exit_px = "open", entry
        for b in dbars:
            if b[0] <= entry_ts:
                continue
            t = dt.datetime.fromtimestamp(b[0], nc.IST).time()
            if t >= flat_by:
                exit_px = b[1]
                break
            if b[3] <= stop:
                outcome, exit_px = "stop", stop
                break
            if b[2] >= target:
                outcome, exit_px = "target", target
                break
            exit_px = b[4]
        rows.append({"day": day, "fired": True, "entry": round(entry, 2), "stop": round(stop, 2),
                     "target": round(target, 2), "outcome": outcome, "R": round((exit_px - entry) / risk, 2),
                     "entry_time": nc.fmt_time_ist(entry_ts)})
    return rows


def replay_swing(dbars, stop_pct, r_mult, hold_days, lookback_setup="breakout20"):
    """Daily bars: enter at the close of every 20-day closing-high breakout; stop -stop_pct, target r_mult R, time stop."""
    closes = [b[4] for b in dbars]
    rows = []
    for i in range(21, len(dbars) - 1):
        if closes[i] <= max(closes[i - 20:i]):
            continue
        entry = closes[i]
        stop = entry * (1 - stop_pct / 100)
        risk = entry - stop
        target = entry + r_mult * risk
        outcome, exit_px, j = "open", entry, i
        for j in range(i + 1, min(len(dbars), i + 1 + hold_days)):
            b = dbars[j]
            if b[1] <= stop:  # gap open through the stop
                outcome, exit_px = "stop", b[1]
                break
            if b[3] <= stop:
                outcome, exit_px = "stop", stop
                break
            if b[2] >= target:
                outcome, exit_px = "target", target
                break
            exit_px = b[4]
        day = dt.datetime.fromtimestamp(dbars[i][0], nc.IST).date().isoformat()
        rows.append({"day": day, "fired": True, "entry": round(entry, 2), "outcome": outcome,
                     "R": round((exit_px - entry) / risk, 2), "days_held": j - i})
    return rows


def verdict(sim, hist):
    t, s = sim["target"], sim["stop"]
    fired = [h for h in hist if h.get("fired")]
    hist_r = [h["R"] for h in fired]
    avg_hist = sum(hist_r) / len(hist_r) if hist_r else None
    if sim["avg_R"] <= 0 and (avg_hist is None or avg_hist <= 0):
        return "negative expectancy on both the replay and the history; this plan loses on average"
    if abs(t - s) <= 5 and (avg_hist is None or abs(avg_hist) < 0.15):
        return "close to a coin flip"
    if t > s + 10 and (avg_hist is None or avg_hist > 0.1):
        return "the edge is there on this data, and it is small"
    if s > t + 10:
        return "the stop is hit more often than the target; the plan is fighting the stock's own rhythm"
    return "mixed: no clear edge either way"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("--plan-file")
    ap.add_argument("--entry", type=float)
    ap.add_argument("--stop", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--entry-time", help="HH:MM IST, default from the plan or 09:35")
    ap.add_argument("--runs", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--or-minutes", type=int, default=15)
    ap.add_argument("--swing", action="store_true")
    ap.add_argument("--offline", help="saved Yahoo 60d/5m chart JSON (optionally .gz)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    cfg = nc.load_config()
    plan = nc.read_json(a.plan_file or (nc.trader_dir() / "plan.json"), {}) or {}
    sym = (a.symbol or plan.get("symbol") or "").upper()
    if not sym:
        print("Time Machine could not run: no symbol - run /nse-exit first or pass SYMBOL --entry --stop --target")
        return 1
    if plan.get("symbol", "").upper() != sym and not (a.entry and a.stop and a.target):
        print("Time Machine could not run: the plan is for %s, not %s - pass --entry --stop --target" % (plan.get("symbol"), sym))
        return 1
    entry = a.entry or plan.get("entry")
    stop = a.stop or plan.get("stop")
    target = a.target or plan.get("target")
    if not (entry and stop and target) or not (stop < entry < target):
        print("Time Machine could not run: need stop < entry < target (got %s, %s, %s)" % (stop, entry, target))
        return 1
    swing = a.swing or plan.get("horizon") == "swing"
    risk = entry - stop
    r_mult = (target - entry) / risk
    try:
        if swing:
            bars = load_history(sym, a, "1d", "2y")
        else:
            bars = load_history(sym, a, "5m", "60d")
    except nc.DataError as e:
        print("Time Machine could not run: %s" % e)
        return 1
    if not bars:
        print("Time Machine could not run: Yahoo returned no bars for %s" % sym)
        return 1
    today = nc.now_ist().date().isoformat()
    if swing:
        hold = int(plan.get("max_hold_days") or cfg.get("swing_max_hold_days", 5))
        shapes = bar_shapes(bars[-250:])
        sim = simulate(entry, stop, target, hold, shapes, a.runs, a.seed)
        hist = replay_swing(bars, plan.get("stop_pct") or cfg.get("swing_stop_pct", 3.0), r_mult, hold)
        horizon = "%d sessions" % hold
        sessions = len(bars)
    else:
        flat_by = nc.parse_hhmm(plan.get("flat_by") or cfg.get("flat_by"), dt.time(15, 10))
        no_after = nc.parse_hhmm(cfg.get("no_entries_after"), dt.time(14, 45))
        et = a.entry_time or plan.get("entry_time") or "09:35"
        hh, mm = [int(x) for x in et.split(":")[:2]]
        entry_t = dt.time(hh, mm)
        n_bars = max(1, int((dt.datetime.combine(dt.date.today(), flat_by) - dt.datetime.combine(dt.date.today(), entry_t)).total_seconds() // 300))
        sess = [b for b in nc.session_bars(bars) if dt.datetime.fromtimestamp(b[0], nc.IST).date().isoformat() != today]
        shapes = bar_shapes(sess)
        # time-of-day pools: bars at the same slot after the entry
        slots = {}
        for b in sess:
            t = dt.datetime.fromtimestamp(b[0], nc.IST)
            k = int((t.hour * 60 + t.minute - (hh * 60 + mm)) // 5)
            if 0 <= k < n_bars and b[1]:
                slots.setdefault(k, []).append(((b[2] - b[1]) / b[1], (b[3] - b[1]) / b[1], (b[4] - b[1]) / b[1]))
        tod = [slots.get(k, []) for k in range(n_bars)]
        sim = simulate(entry, stop, target, n_bars, shapes, a.runs, a.seed, tod)
        hist = replay_history(bars, a.or_minutes, plan.get("stop_rule", "or-mid") if "low" not in str(plan.get("stop_rule", "")) else "or-low",
                              r_mult, flat_by, no_after, entry_day=today)
        horizon = "%s to %s IST (%d bars)" % (entry_t.strftime("%H:%M"), flat_by.strftime("%H:%M"), n_bars)
        sessions = len(nc.bars_by_day(sess))
    fired = [h for h in hist if h.get("fired")]
    hist_sum = {"sessions": len(hist), "fired": len(fired),
                "target": sum(1 for h in fired if h["outcome"] == "target"),
                "stop": sum(1 for h in fired if h["outcome"] == "stop"),
                "open": sum(1 for h in fired if h["outcome"] == "open"),
                "avg_R": round(sum(h["R"] for h in fired) / len(fired), 2) if fired else None}
    v = verdict(sim, hist)
    doc = {"symbol": sym, "as_of": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "horizon": "swing" if swing else "intraday",
           "entry": entry, "stop": stop, "target": target, "R": round(r_mult, 2), "runs": a.runs, "seed": a.seed,
           "sessions_of_data": sessions, "window": horizon, "simulation": sim, "history": hist_sum,
           "history_rows": fired[-40:], "verdict": v,
           "note": "A simulation from the last %d sessions, not a prediction." % sessions}
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print("%s: %s replays of %s, entry %.2f stop %.2f target %.2f (%.1f R), seed %d, %d sessions of data." % (
            sym, format(a.runs, ","), horizon, entry, stop, target, r_mult, a.seed, sessions))
        print("  Target first  %5.1f%%" % sim["target"])
        print("  Stop first    %5.1f%%" % sim["stop"])
        print("  Still open    %5.1f%%%s" % (sim["open"], ("  (median close %.2f, %.0f%% of those above the entry)" % (
            sim["open_close_median"], sim["open_above_entry_pct"])) if sim.get("open_close_median") and sim["open"] >= 1 else ""))
        print("  Average R     %+.2f  (%+.2f%% of the entry)" % (sim["avg_R"], sim["expectancy_pct_of_entry"]))
        if fired:
            print("Same rule, %d real sessions: fired %d times - %d hit the target, %d hit the stop, %d neither; average %+.2f R." % (
                hist_sum["sessions"], hist_sum["fired"], hist_sum["target"], hist_sum["stop"], hist_sum["open"], hist_sum["avg_R"]))
        else:
            print("Same rule on %d real sessions: it never fired." % hist_sum["sessions"])
        print("Verdict: %s." % v)
        print("A simulation, not a prediction. Results day, an RBI announcement or a circuit can do what those sessions never did.")
    if not a.no_write:
        nc.write_json(nc.ensure_trader_dir() / "timemachine.json", doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
