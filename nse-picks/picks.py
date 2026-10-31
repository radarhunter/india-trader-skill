#!/usr/bin/env python3
"""nse-picks: tomorrow's shortlist, and what to get out of.

  tomorrow   After the close (or before the open): rank the universe and
             print FIVE swing candidates (1-5 day holds) and FIVE intraday
             watch names for the next session, each with the reasons, the
             levels to plan from (entry above today's high, stop, 2R target)
             and the score; plus the EXIT list - every holding whose plan is
             broken (stop breached on a close, trend lost, hold period up,
             results due tomorrow) and the universe's breakdowns to avoid.
  exits      Any time: check every holding against its stop, its target, a
             drawdown and the clock, from live quotes; ring on anything that
             says "out now". Run it in a loop with --watch for same-day alerts.

Everything is a ranking of charts and filings by rules, from free delayed
data. It is a shortlist to do your own work on, not a tip sheet. Nothing is
bought or sold by this skill. Exit 0 when it produced a list.
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

SPARK_MAX = 20


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def load_universe(a):
    p = Path(a.universe_file) if a.universe_file else nc.trader_dir() / "universe.txt"
    fb = _here.parent / "nse-scanner" / "universe.fallback.txt"
    if a.symbols:
        return {s.strip().upper(): {"name": s.strip().upper(), "tags": ""} for s in a.symbols.split(",") if s.strip()}
    if not p.exists():
        if fb.exists():
            p = fb
        else:
            raise nc.DataError("no trader/universe.txt - run /nse-scanner universe first")
    out = {}
    cfg = nc.load_config()
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = (line.split("|") + ["", "", ""])[:4]
        sym, name, ind, tags = [x.strip() for x in parts]
        if cfg.get("avoid_asm_gsm", True) and ("ASM" in tags or "GSM" in tags):
            continue
        if sym in [b.upper() for b in cfg.get("blocked", [])]:
            continue
        out[sym] = {"name": name or sym, "industry": ind, "tags": tags}
    if a.limit:
        out = dict(list(out.items())[: a.limit])
    return out


def spark_closes(symbols, a):
    data = {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        ys = ",".join(nc.yahoo_symbol(s) for s in batch)
        try:
            doc = json.loads(nc.yahoo_get("/v7/finance/spark?symbols=%s&range=3mo&interval=1d" % urllib.parse.quote(ys)))
        except (nc.DataError, ValueError) as e:
            nc.progress(a, "  spark batch %d: %s" % (k + 1, e))
            time.sleep(a.pause)
            continue
        for r in ((doc.get("spark") or {}).get("result") or []):
            resp = (r.get("response") or [None])[0]
            if not resp or resp.get("error"):
                continue
            q = ((resp.get("indicators") or {}).get("quote") or [{}])[0]
            pairs = [(t, c) for t, c in zip(resp.get("timestamp") or [], q.get("close") or []) if c is not None]
            if len(pairs) >= 30:
                data[nc.plain_symbol(r.get("symbol") or "")] = [c for _, c in pairs]
        if (k + 1) % 10 == 0 or k + 1 == len(batches):
            nc.progress(a, "  closes %d of %d requests" % (k + 1, len(batches)))
        time.sleep(a.pause)
    return data


def nifty_closes(a):
    try:
        ch = nc.yahoo_chart("^NSEI", "3mo", "1d")
        return [b[4] for b in nc.clean_bars(ch)]
    except nc.DataError:
        return []


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def rel_strength(closes, bench, n=20):
    if len(closes) < n + 1 or len(bench) < n + 1:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100 - (bench[-1] / bench[-1 - n] - 1) * 100


def prescore(closes, bench):
    """From closes only: a first pass to pick the candidates worth the full bars."""
    s, why = 0.0, []
    last = closes[-1]
    e20 = nc.ema(closes, 20)
    s50 = nc.sma(closes, 50) if len(closes) >= 50 else None
    r = nc.rsi(closes, 14)
    hi20 = max(closes[-21:-1])
    rs = rel_strength(closes, bench)
    if e20 and last > e20:
        s += 1
    if e20 and s50 and e20 > s50:
        s += 1
        why.append("20>50")
    if r is not None and 50 <= r <= 72:
        s += 1
        why.append("RSI %.0f" % r)
    if last > hi20:
        s += 2
        why.append("20d breakout")
    elif e20 and 0.97 * e20 <= last <= 1.02 * e20 and closes[-1] > closes[-2] and last > closes[-5]:
        s += 1.5
        why.append("pullback to 20EMA held")
    if rs is not None and rs > 3:
        s += 1.5
        why.append("RS vs Nifty %+.1f%%" % rs)
    elif rs is not None and rs < -3:
        s -= 1
    chg5 = (last / closes[-6] - 1) * 100
    if chg5 > 12:
        s -= 1
        why.append("ran %.0f%% in 5d, extended" % chg5)
    return s, why, {"rsi": r, "rs20": rs, "chg5": chg5}


def full_score(sym, bars, meta, pre, results_due, cfg):
    """With OHLCV: liquidity, ATR%, closing strength, volume surge, 52w distance. Returns (swing, intraday, reasons, levels)."""
    closes = [b[4] for b in bars]
    vols = [b[5] for b in bars]
    last = bars[-1]
    atr = nc.atr(bars, 14)
    atr_pct = atr / last[4] * 100 if atr else None
    av20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    turnover = av20 * last[4] if av20 else 0
    vol_ratio = last[5] / av20 if av20 else None
    rng = last[2] - last[3]
    close_pos = (last[4] - last[3]) / rng if rng else 0.5
    hi52 = max(b[2] for b in bars)
    dist52 = (hi52 - last[4]) / hi52 * 100
    swing, intra = pre[0], 0.0
    reasons = list(pre[1])
    # liquidity gates
    if turnover < 5e7:
        return None
    if atr_pct is None or atr_pct < 1.0:
        swing -= 1
    if atr_pct and atr_pct > 6:
        swing -= 1
        reasons.append("ATR %.1f%%, wild" % atr_pct)
    if vol_ratio and vol_ratio >= 1.5:
        swing += 1.5
        intra += 1.5
        reasons.append("volume %.1fx avg" % vol_ratio)
    if close_pos >= 0.75:
        swing += 1
        intra += 1
        reasons.append("closed near the high")
    elif close_pos <= 0.25:
        swing -= 1
        intra -= 0.5
    if dist52 <= 5:
        swing += 1
        reasons.append("within 5%% of the 3-month high")
    # intraday-specific: tradeable range and turnover
    if atr_pct and 1.5 <= atr_pct <= 5:
        intra += 1.5
    if turnover >= 5e8:
        intra += 1.5
    elif turnover >= 2e8:
        intra += 0.5
    if len(bars) >= 8:
        rngs = [b[2] - b[3] for b in bars[-7:]]
        if rngs[-1] == min(rngs):
            intra += 1
            reasons.append("NR7 squeeze")
        if bars[-1][2] < bars[-2][2] and bars[-1][3] > bars[-2][3]:
            intra += 0.5
            reasons.append("inside day")
    intra += max(0.0, min(2.0, pre[0] / 3.0))
    if results_due is not None:
        days = results_due
        if days <= 1:
            swing -= 3
            intra -= 3
            reasons.append("RESULTS DUE in %d day(s)" % days)
        elif days <= 5:
            swing -= 1.5
            reasons.append("results in %d days" % days)
    # levels to plan from
    entry = nc.tick_round(last[2] + nc.tick_for(last[2]), down=False)
    sw_stop = nc.tick_round(min(min(b[3] for b in bars[-5:]), entry * (1 - cfg.get("swing_stop_pct", 3.0) / 100)), down=True)
    if entry - sw_stop > entry * 0.06:
        sw_stop = nc.tick_round(entry * 0.96, down=True)
    sw_target = nc.tick_round(entry + 2 * (entry - sw_stop), down=True)
    levels = {"entry_above": entry, "swing_stop": sw_stop, "swing_target_2R": sw_target,
              "swing_risk_pct": round((entry - sw_stop) / entry * 100, 2), "atr14": round(atr, 2) if atr else None,
              "atr_pct": round(atr_pct, 2) if atr_pct else None, "avg_turnover_cr": round(turnover / 1e7, 1),
              "vol_ratio": round(vol_ratio, 2) if vol_ratio else None, "close": round(last[4], 2),
              "day_high": round(last[2], 2), "day_low": round(last[3], 2), "dist_3mo_high_pct": round(dist52, 1)}
    return round(swing, 2), round(intra, 2), reasons, levels


def breakdowns(closes):
    """For the avoid / exit list: closed below the 20-day low, or lost the 20EMA and 50SMA on a down day."""
    last = closes[-1]
    lo20 = min(closes[-21:-1])
    e20 = nc.ema(closes, 20)
    s50 = nc.sma(closes, 50) if len(closes) >= 50 else None
    why = []
    if last < lo20:
        why.append("closed below the 20-day low")
    if e20 and s50 and last < e20 < s50 and closes[-1] < closes[-2]:
        why.append("below 20EMA below 50SMA, down day")
    r = nc.rsi(closes, 14)
    if r is not None and r > 80 and closes[-1] < closes[-2]:
        why.append("RSI %.0f and a down day: extended" % r)
    return why


def results_days(symbol, news, today):
    for e in news.get("report") or []:
        if e["symbol"].upper() != symbol:
            continue
        for r in e.get("results_due") or []:
            try:
                d = dt.date.fromisoformat(r["date"])
            except ValueError:
                continue
            return (d - today).days
    return None


# --------------------------------------------------------------------------
# tomorrow
# --------------------------------------------------------------------------

def cmd_tomorrow(a):
    cfg = nc.load_config()
    uni = load_universe(a)
    syms = list(uni)
    t = nc.now_ist(a.now)
    today = t.date()
    nxt = nc.next_trading_day(today) if (t.time() >= nc.CLOSE or not nc.is_trading_day(today)) else today
    nc.progress(a, "Ranking %d symbols for %s..." % (len(syms), nxt))
    bench = nifty_closes(a)
    closes = spark_closes(syms, a)
    news = nc.read_json(nc.trader_dir() / "news.json", {}) or {}
    pre = {}
    for s, c in closes.items():
        if c[-1] < a.min_price:
            continue
        pre[s] = prescore(c, bench)
    cand = sorted(pre, key=lambda s: -pre[s][0])[: a.confirm]
    nc.progress(a, "Confirming the top %d on full bars..." % len(cand))
    rows = []
    for s in cand:
        try:
            ch = nc.yahoo_chart(s, "3mo", "1d")
        except nc.DataError:
            continue
        bars = nc.clean_bars(ch)
        if len(bars) < 30:
            continue
        rd = results_days(s, news, today)
        fs = full_score(s, bars, ch["meta"], pre[s], rd, cfg)
        if not fs:
            continue
        sw, intra, reasons, levels = fs
        rows.append({"symbol": s, "name": uni[s]["name"], "industry": uni[s].get("industry", ""), "fno": "FNO" in uni[s].get("tags", ""),
                     "swing_score": sw, "intraday_score": intra, "reasons": reasons, "levels": levels, "results_in_days": rd})
        time.sleep(a.pause / 2)
    # diversify: at most 2 per industry in each list
    def pick(key, n=5):
        out, per_ind = [], {}
        for r in sorted(rows, key=lambda r: -r[key]):
            if r[key] <= 0:
                break
            ind = r.get("industry") or ""
            if ind and per_ind.get(ind, 0) >= 2:
                continue
            if ind:
                per_ind[ind] = per_ind.get(ind, 0) + 1
            out.append(r)
            if len(out) >= n:
                break
        return out
    swing = pick("swing_score")
    intraday = [r for r in pick("intraday_score", 8) if r["symbol"] not in {x["symbol"] for x in swing}][:5]
    # exits
    hold = nc.load_holdings(cfg)
    exits = []
    for p in hold.get("positions") or []:
        s = p["symbol"].upper()
        why = []
        c = closes.get(s)
        if not c:
            try:
                c = [b[4] for b in nc.clean_bars(nc.yahoo_chart(s, "3mo", "1d"))]
            except nc.DataError:
                c = None
        last = c[-1] if c else None
        if last and p.get("stop") and last <= float(p["stop"]):
            why.append("closed %.2f at or below the stop %.2f" % (last, float(p["stop"])))
        if last and p.get("target") and last >= float(p["target"]):
            why.append("closed %.2f at or above the target %.2f - book it" % (last, float(p["target"])))
        if c and len(c) >= 21:
            why += [w for w in breakdowns(c) if "extended" not in w]
        if p.get("entered"):
            try:
                ent = dt.datetime.strptime(p["entered"][:10], "%Y-%m-%d").date()
                held = sum(1 for i in range((today - ent).days + 1) if nc.is_trading_day(ent + dt.timedelta(days=i))) - 1
                if (p.get("horizon") == "swing" or (p.get("product") or "").upper() == "CNC") and held >= int(cfg.get("swing_max_hold_days", 5)):
                    why.append("held %d sessions; the plan's max is %d" % (held, cfg.get("swing_max_hold_days", 5)))
            except ValueError:
                pass
        if (p.get("product") or "").upper() == "MIS":
            why.append("MIS position still open after the close - the broker will have squared it; reconcile")
        rd = results_days(s, news, today)
        if rd is not None and rd <= 1:
            why.append("results due in %d day(s): a gap either way" % rd)
        if last and p.get("avg_price"):
            pnl_pct = (last - float(p["avg_price"])) / float(p["avg_price"]) * 100
        else:
            pnl_pct = None
        if why:
            exits.append({"symbol": s, "qty": p.get("qty"), "avg_price": p.get("avg_price"), "last": last, "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None, "why": why})
    avoid = []
    for s, c in closes.items():
        w = breakdowns(c)
        if w and s not in {x["symbol"] for x in exits}:
            avoid.append({"symbol": s, "close": round(c[-1], 2), "why": w})
    avoid = avoid[:10]
    doc = {"for_session": nxt.isoformat(), "as_of": t.strftime("%Y-%m-%d %H:%M IST"), "universe": len(syms), "read": len(closes),
           "confirmed": len(rows), "swing": swing, "intraday": intraday, "exits": exits, "avoid": avoid,
           "note": "A ranking of charts and filings by rules on free delayed data. Not advice; not a SEBI-registered adviser. Do your own work, size with /nse-budget, and never skip /nse-risk."}
    if not a.no_write:
        nc.write_json(nc.ensure_trader_dir() / "picks.json", doc)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if (swing or intraday or exits) else 1
    print("Shortlist for %s (ranked %s, %d of %d read, %d confirmed on full bars)." % (nxt.strftime("%a %d %b %Y"), doc["as_of"], len(closes), len(syms), len(rows)))
    print("\nSWING candidates (1-5 day holds, CNC):")
    if not swing:
        print("  none scored above zero - the market is not offering clean long setups")
    for i, r in enumerate(swing, 1):
        L = r["levels"]
        print("  %d. %-12s %-30s score %.1f%s" % (i, r["symbol"], r["name"][:30], r["swing_score"], "  (F&O)" if r["fno"] else ""))
        print("     why: %s" % "; ".join(r["reasons"]))
        print("     plan from: entry above %.2f, stop %.2f (-%.1f%%), 2R target %.2f, ATR %.1f%%, turnover %s cr/day" % (
            L["entry_above"], L["swing_stop"], L["swing_risk_pct"], L["swing_target_2R"], L["atr_pct"] or 0, L["avg_turnover_cr"]))
    print("\nINTRADAY watch (run /nse-scanner orb at 09:35 on these):")
    if not intraday:
        print("  none")
    for i, r in enumerate(intraday, 1):
        L = r["levels"]
        print("  %d. %-12s %-30s score %.1f  ATR %.1f%%  turnover %s cr  close %.2f (day %.2f-%.2f)" % (
            i, r["symbol"], r["name"][:30], r["intraday_score"], L["atr_pct"] or 0, L["avg_turnover_cr"], L["close"], L["day_low"], L["day_high"]))
        print("     why: %s" % "; ".join(r["reasons"]))
    print("\nEXIT list (your holdings whose plan is broken):")
    if not exits:
        print("  none" if hold.get("positions") else "  no holdings in %s" % cfg.get("holdings_file"))
    for e in exits:
        print("  %-12s %s x %s, last %s (%s): %s" % (e["symbol"], e["qty"], nc.inr(e["avg_price"]), nc.inr(e["last"]), nc.pct(e["pnl_pct"]), "; ".join(e["why"])))
    if avoid:
        print("\nAVOID / breakdowns (not for longs):")
        for r in avoid:
            print("  %-12s %9.2f  %s" % (r["symbol"], r["close"], "; ".join(r["why"])))
    print("\n" + doc["note"])
    return 0 if (swing or intraday or exits) else 1


# --------------------------------------------------------------------------
# exits (same day)
# --------------------------------------------------------------------------

def cmd_exits(a):
    cfg = nc.load_config()
    hold = nc.load_holdings(cfg)
    pos = hold.get("positions") or []
    if not pos:
        print("No holdings in %s. Add positions there (symbol, qty, avg_price, stop, target, product) or let /nse-broker fill one." % cfg.get("holdings_file"))
        return 1
    fired_ids = set()
    while True:
        t = nc.now_ist(a.now)
        out, urgent = [], []
        flat_by = nc.parse_hhmm(cfg.get("flat_by"), dt.time(15, 10))
        for p in pos:
            s = p["symbol"].upper()
            try:
                q = nc.yahoo_quote_fast(s)
            except nc.DataError as e:
                out.append({"symbol": s, "error": str(e)})
                continue
            last = q["last"]
            why = []
            if p.get("stop") and last is not None and last <= float(p["stop"]):
                why.append("STOP hit: last %.2f <= %.2f" % (last, float(p["stop"])))
            if p.get("target") and last is not None and last >= float(p["target"]):
                why.append("TARGET hit: last %.2f >= %.2f" % (last, float(p["target"])))
            if p.get("avg_price") and last is not None:
                dd = (last - float(p["avg_price"])) / float(p["avg_price"]) * 100
                if dd <= -a.drawdown:
                    why.append("down %.1f%% from your average %.2f" % (-dd, float(p["avg_price"])))
            else:
                dd = None
            if q.get("prev_close") and last is not None:
                chg = (last - q["prev_close"]) / q["prev_close"] * 100
                if chg <= -a.day_drop:
                    why.append("down %.1f%% on the day" % -chg)
            else:
                chg = None
            if (p.get("product") or "").upper() == "MIS" and t.time() >= flat_by and nc.is_trading_day(t.date()):
                why.append("flat-by %s IST reached; square off before the broker does" % flat_by.strftime("%H:%M"))
            row = {"symbol": s, "qty": p.get("qty"), "avg_price": p.get("avg_price"), "last": last, "stop": p.get("stop"),
                   "target": p.get("target"), "pnl_pct": round(dd, 2) if dd is not None else None,
                   "day_pct": round(chg, 2) if chg is not None else None, "why": why}
            out.append(row)
            if why:
                urgent.append(row)
            time.sleep(0.3)
        doc = {"as_of": t.strftime("%Y-%m-%d %H:%M:%S IST"), "positions": out, "urgent": urgent}
        nc.write_json(nc.ensure_trader_dir() / "exits.json", doc)
        if a.json:
            print(json.dumps(doc, indent=2))
        else:
            print("%s  holdings check:" % t.strftime("%H:%M:%S"))
            for r in out:
                if r.get("error"):
                    print("  %-12s %s" % (r["symbol"], r["error"]))
                    continue
                print("  %-12s last %-10s avg %-10s %s day %s  stop %s target %s%s" % (
                    r["symbol"], nc.inr(r["last"]), nc.inr(r["avg_price"]), nc.pct(r["pnl_pct"]), nc.pct(r["day_pct"]),
                    nc.inr(r["stop"]), nc.inr(r["target"]), ("  >>> " + "; ".join(r["why"])) if r["why"] else ""))
        for r in urgent:
            key = r["symbol"] + "|" + r["why"][0]
            if key not in fired_ids:
                fired_ids.add(key)
                if cfg.get("notify", True):
                    nc.notify("nse-picks exit", "%s: %s" % (r["symbol"], r["why"][0]))
                if r["why"][0].startswith(("STOP", "TARGET")):  # a paper position: book it at the level
                    level = r["stop"] if r["why"][0].startswith("STOP") else r["target"]
                    pnl = nc.paper_close(cfg, r["symbol"], float(level), "paper " + r["why"][0].split(":")[0].lower())
                    if pnl is not None:
                        print("  PAPER position in %s booked at %s: P&L %s" % (r["symbol"], nc.inr(level), nc.inr(pnl)))
                        pos[:] = list(nc.load_holdings(cfg).get("positions") or [])
        if not a.watch or a.now:
            return 0 if urgent else 1
        if t.time() >= dt.time(15, 35) or not nc.is_trading_day(t.date()):
            print("Market closed; stopped.")
            return 0
        time.sleep(a.poll or int(cfg.get("watch_poll_seconds", 30)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["tomorrow", "exits"])
    ap.add_argument("--symbols")
    ap.add_argument("--universe-file")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--confirm", type=int, default=80, help="candidates to confirm on full bars")
    ap.add_argument("--min-price", type=float, default=50)
    ap.add_argument("--pause", type=float, default=0.4)
    ap.add_argument("--now", help="pin the clock 'YYYY-MM-DD HH:MM' IST")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--poll", type=int)
    ap.add_argument("--drawdown", type=float, default=2.0, help="exits: ring when down this %% from average")
    ap.add_argument("--day-drop", type=float, default=3.0, help="exits: ring when down this %% on the day")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"tomorrow": cmd_tomorrow, "exits": cmd_exits}[a.cmd](a)
    except nc.DataError as e:
        print("Picks could not run: %s" % e)
        return 1
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
