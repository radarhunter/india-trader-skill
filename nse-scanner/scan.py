#!/usr/bin/env python3
"""nse-scanner: the Chart Scanner for NSE / BSE.

  universe   Build trader/universe.txt: Nifty 500 + every F&O underlying,
             flagged when NSE lists it under ASM or GSM surveillance.
  daily      Pre-market scan of daily closes (Yahoo, 3 months): 20-day
             closing-high breakout, bull flag, reclaimed the 20-day average,
             oversold (RSI<30), oversold bounce, inside day / NR7 squeeze.
  orb        Intraday opening-range breakout on 5-minute bars: the first
             15 minutes (09:15-09:30) set the range; a bar that CLOSES above
             the range high, on volume, is a hit.

Everything is free, unofficial, delayed data. A hit is a chart that matched a
rule. It is not a stock to buy. Exit code 0 = found something, 1 = nothing
or could not run (one plain line says why).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
import time
import urllib.parse
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402

SPARK_MAX = 20
NIFTY500_CSV = "/content/indices/ind_nifty500list.csv"
FO_LOTS_CSV = "/content/fo/fo_mktlots.csv"
FALLBACK = _here / "universe.fallback.txt"


# --------------------------------------------------------------------------
# universe
# --------------------------------------------------------------------------

def fetch_index_live(sess, name):
    rows = nc.nse_index_constituents(sess, name)
    return {r["symbol"]: {"name": r["name"], "industry": ""} for r in rows}


def fetch_nifty500_csv():
    text = nc.nse_archive(NIFTY500_CSV)
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        sym = (r.get("Symbol") or "").strip().upper()
        if sym:
            out[sym] = {"name": (r.get("Company Name") or "").strip(), "industry": (r.get("Industry") or "").strip()}
    if len(out) < 400:
        raise nc.DataError("Nifty 500 CSV looks truncated (%d rows)" % len(out))
    return out


def fetch_fo_csv():
    text = nc.nse_archive(FO_LOTS_CSV)
    syms = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        under, sym = row[0].strip(), row[1].strip().upper()
        if sym in ("SYMBOL", "") or sym.startswith(("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")):
            continue
        syms[sym] = {"name": under.title() if under else sym, "industry": ""}
    if len(syms) < 100:
        raise nc.DataError("F&O CSV came back short (%d)" % len(syms))
    return syms


def cmd_universe(a):
    out = nc.ensure_trader_dir() / "universe.txt"
    sess = nc.NSESession()
    names, src = {}, []
    # Nifty 500 (or the index the user asked for): live market-watch API first, archive CSV next
    idx = a.index or "NIFTY 500"
    try:
        n = fetch_index_live(sess, idx)
        src.append("%s live (%d)" % (idx, len(n)))
    except nc.DataError as e:
        nc.progress(a, "%s live list: %s; trying the archive CSV" % (idx, e))
        n = {}
        if idx == "NIFTY 500":
            try:
                n = fetch_nifty500_csv()
                src.append("Nifty 500 CSV (%d)" % len(n))
            except nc.DataError as e2:
                nc.progress(a, "Nifty 500 CSV: %s" % e2)
    for s_, m in n.items():
        names[s_] = dict(m, n500=True)
    # F&O underlyings
    if not a.no_fno:
        fo = {}
        try:
            fo = fetch_index_live(sess, "SECURITIES IN F&O")
            src.append("F&O live (%d)" % len(fo))
        except nc.DataError as e:
            nc.progress(a, "F&O live list: %s; trying the archive CSV" % e)
            try:
                fo = fetch_fo_csv()
                src.append("F&O CSV (%d)" % len(fo))
            except nc.DataError as e2:
                nc.progress(a, "F&O CSV: %s" % e2)
        for s_, m in fo.items():
            if s_ not in names:
                names[s_] = dict(m)
            names[s_]["fno"] = True
    if not names:
        if FALLBACK.exists():
            out.write_text(FALLBACK.read_text(encoding="utf-8"), encoding="utf-8")
            n_fb = sum(1 for l in FALLBACK.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))
            print("NSE did not answer; copied the bundled universe (%d symbols, dated in the file) to %s. Re-run later for a fresh one." % (n_fb, out))
            return 0
        print("Could not build the universe: NSE did not answer and no fallback is bundled.")
        return 1
    # keep industries from the archive CSV when the live list had none
    if any(not m.get("industry") for m in names.values()):
        try:
            for s_, m in fetch_nifty500_csv().items():
                if s_ in names and m.get("industry"):
                    names[s_]["industry"] = m["industry"]
        except nc.DataError:
            pass
    flags = {} if a.no_surveillance else nc.nse_surveillance(sess)
    lines = ["# india-trader-skill universe, built %s from %s" % (nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), " + ".join(src))]
    for s_ in sorted(names):
        m = names[s_]
        tags = []
        if m.get("n500"):
            tags.append("N500")
        if m.get("fno"):
            tags.append("FNO")
        if s_ in flags:
            tags.append(flags[s_].replace("|", "/").replace(",", " "))
        lines.append("%s|%s|%s|%s" % (s_, (m.get("name") or s_).replace("|", " "), (m.get("industry") or "").replace("|", " "), ",".join(tags)))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Universe: %d symbols from %s%s -> %s" % (
        len(lines) - 1, " + ".join(src), (", %d under ASM/GSM surveillance" % sum(1 for s_ in names if s_ in flags)), out))
    return 0


def load_universe(a):
    p = Path(a.universe_file) if a.universe_file else nc.trader_dir() / "universe.txt"
    if a.symbols:
        return [(s.strip().upper(), s.strip().upper(), "", "") for s in a.symbols.split(",") if s.strip()]
    if a.watchlist:
        doc = nc.read_json(a.watchlist, {})
        return [(h["symbol"], h.get("name", h["symbol"]), "", "") for h in doc.get("hits", [])]
    if not p.exists():
        if FALLBACK.exists():
            p = FALLBACK
        else:
            raise nc.DataError("no %s - run `scan.py universe` first" % p)
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = (line.split("|") + ["", "", ""])[:4]
        rows.append(tuple(x.strip() for x in parts))
    cfg = nc.load_config()
    if cfg.get("avoid_asm_gsm", True) and not a.include_surveillance:
        before = len(rows)
        rows = [r for r in rows if "ASM" not in r[3] and "GSM" not in r[3]]
        if before != len(rows):
            nc.progress(a, "Skipping %d symbols under ASM/GSM surveillance (avoid_asm_gsm in config)" % (before - len(rows)))
    if a.limit:
        rows = rows[: a.limit]
    return rows


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def spark_fetch(symbols, rng, interval, a, exchange="NSE"):
    """{SYM: {'ts':[], 'close':[]}} via Yahoo spark, 20 symbols a request."""
    data, failed = {}, []
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        ys = [nc.yahoo_symbol(s, exchange) for s in batch]
        path = "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
            urllib.parse.quote(",".join(ys)), rng, interval)
        try:
            doc = json.loads(nc.yahoo_get(path))
        except (nc.DataError, ValueError) as e:
            failed.extend(batch)
            nc.progress(a, "  spark batch %d: %s" % (k + 1, e))
            time.sleep(a.pause)
            continue
        got = absorb_spark(doc, data)
        for s in batch:
            if s not in got:
                failed.append(s)
        if (k + 1) % 10 == 0 or k + 1 == len(batches):
            nc.progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
        time.sleep(a.pause)
    return data, failed


def absorb_spark(doc, data):
    got = set()
    sp = doc.get("spark") if isinstance(doc, dict) else None
    results = (sp or {}).get("result") or []
    for r in results:
        sym = nc.plain_symbol(r.get("symbol") or "")
        resp = (r.get("response") or [None])[0]
        if not resp or resp.get("error"):
            continue
        ts = resp.get("timestamp") or []
        q = ((resp.get("indicators") or {}).get("quote") or [{}])[0]
        closes = q.get("close") or []
        meta = resp.get("meta") or {}
        pairs = [(int(t), c) for t, c in zip(ts, closes) if c is not None]
        if not pairs:
            continue
        data[sym] = {"ts": [p[0] for p in pairs], "close": [p[1] for p in pairs],
                     "last": meta.get("regularMarketPrice"), "prev_close": meta.get("chartPreviousClose")}
        got.add(sym)
    return got


def load_offline(a):
    """--offline DIR: SYMBOL.json v8 chart files or spark answers."""
    data = {}
    p = Path(a.offline)
    files = [p] if p.is_file() else sorted(p.glob("*.json"))
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "spark" in doc:
            absorb_spark(doc, data)
        elif "chart" in doc:
            try:
                ch = nc.parse_chart(doc, f.stem)
            except nc.DataError:
                continue
            bars = nc.clean_bars(ch)
            data[ch["symbol"]] = {"ts": [b[0] for b in bars], "close": [b[4] for b in bars],
                                  "bars": bars, "last": ch["meta"].get("regularMarketPrice"),
                                  "prev_close": ch["meta"].get("previousClose")}
    return data


# --------------------------------------------------------------------------
# daily setups
# --------------------------------------------------------------------------

def daily_setups(closes, bars=None):
    """closes: list of daily closes (oldest first). Returns [(setup, detail)]."""
    hits = []
    n = len(closes)
    if n < 25:
        return hits
    last, prev = closes[-1], closes[-2]
    hi20 = max(closes[-21:-1])
    sma20 = nc.sma(closes, 20)
    sma50 = nc.sma(closes, 50) if n >= 50 else None
    r = nc.rsi(closes, 14)
    if last > hi20:
        hits.append(("breakout20", "closed at %.2f above the 20-day closing high %.2f" % (last, hi20)))
    # bull flag: a run of >= 8% in the prior 5-15 days, then a tight drift of <= 4% over 3-7 days
    for run in (10, 15):
        if n >= run + 8:
            base = closes[-(run + 7)]
            peak_i = max(range(n - run - 6, n - 2), key=lambda i: closes[i])
            peak = closes[peak_i]
            if base > 0 and (peak - base) / base >= 0.08 and n - 1 - peak_i >= 3:
                flag = closes[peak_i:]
                if (max(flag) - min(flag)) / peak <= 0.04 and last >= peak * 0.97:
                    hits.append(("bullflag", "ran %.1f%% then held within 4%% for %d days" % (
                        (peak - base) / base * 100, n - 1 - peak_i)))
                    break
    if sma20 and prev < nc.sma(closes[:-1], 20) and last > sma20:
        hits.append(("reclaim20", "closed back above the 20-day average %.2f" % sma20))
    if r is not None and r < 30:
        hits.append(("oversold", "RSI(14) %.1f" % r))
    if r is not None and 30 <= r < 45 and nc.rsi(closes[:-1], 14) is not None and nc.rsi(closes[:-1], 14) < 30 and last > prev:
        hits.append(("oversoldbounce", "RSI(14) crossed back up from under 30 to %.1f" % r))
    if bars and len(bars) >= 8:
        rng = [b[2] - b[3] for b in bars[-7:]]
        if rng[-1] == min(rng) and rng[-1] > 0:
            hits.append(("nr7", "narrowest range in 7 days (%.2f)" % rng[-1]))
        if bars[-1][2] < bars[-2][2] and bars[-1][3] > bars[-2][3]:
            hits.append(("insideday", "inside day: %.2f-%.2f within %.2f-%.2f" % (
                bars[-1][3], bars[-1][2], bars[-2][3], bars[-2][2])))
    if sma20 and sma50 and last > sma20 > sma50:
        hits.append(("trend", "above the 20-day (%.2f) above the 50-day (%.2f)" % (sma20, sma50)))
    return hits


def cmd_daily(a):
    rows = load_universe(a)
    symbols = [r[0] for r in rows]
    names = {r[0]: r[1] for r in rows}
    if not symbols:
        print("The universe is empty.")
        return 1
    t0 = time.time()
    if a.offline:
        data, failed = load_offline(a), []
    else:
        nc.progress(a, "Fetching 3 months of daily closes for %d symbols (%d spark requests)..." % (
            len(symbols), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = spark_fetch(symbols, "3mo", "1d", a)
    hits = []
    wanted = set(a.setups.split(",")) if a.setups else None
    for sym in symbols:
        d = data.get(sym)
        if not d:
            continue
        closes = d["close"]
        if not closes or closes[-1] < a.min_price:
            continue
        found = daily_setups(closes, d.get("bars"))
        if wanted:
            found = [f for f in found if f[0] in wanted]
        if not found:
            continue
        hits.append({"symbol": sym, "name": names.get(sym, sym), "close": round(closes[-1], 2),
                     "setups": [f[0] for f in found], "why": "; ".join(f[1] for f in found),
                     "rsi14": round(nc.rsi(closes, 14) or 0, 1), "score": score_daily(found)})
    # liquidity confirm on the top hits (volume from v8)
    if hits and not a.offline and a.min_volume > 0:
        hits.sort(key=lambda h: -h["score"])
        keep = []
        nc.progress(a, "Checking 20-day average volume on the top %d hits..." % min(len(hits), a.max_confirm))
        for h in hits[: a.max_confirm]:
            try:
                ch = nc.yahoo_chart(h["symbol"], "1mo", "1d")
                vols = [v for v in ch["volume"] if v]
                av = sum(vols[-20:]) / max(1, len(vols[-20:])) if vols else 0
                h["avg_volume_20d"] = int(av)
                if av >= a.min_volume:
                    keep.append(h)
            except nc.DataError:
                keep.append(h)
            time.sleep(a.pause / 2)
        hits = keep
    hits.sort(key=lambda h: (-h["score"], h["symbol"]))
    t = nc.now_ist(a.now)
    doc = {"mode": "daily", "as_of": t.strftime("%Y-%m-%d %H:%M IST"), "universe": len(symbols),
           "scanned": len(data), "failed": len(failed), "hits": hits,
           "note": "Charts that matched a rule from free delayed Yahoo data. Not a list of stocks to buy."}
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print("Daily scan, %s. %d of %d symbols read (%d failed) in %.0fs." % (
            doc["as_of"], len(data), len(symbols), len(failed), time.time() - t0))
        if not hits:
            print("No setups matched today.")
        for h in hits[: a.show]:
            print("  %-12s %9.2f  %-32s %s" % (h["symbol"], h["close"], ",".join(h["setups"]), h["why"][:90]))
        if len(hits) > a.show:
            print("  ... %d more in the watchlist" % (len(hits) - a.show))
    if not a.no_write:
        p = nc.write_json(nc.ensure_trader_dir() / "watchlist.json", doc)
        nc.progress(a, "Wrote %s (%d hits)" % (p, len(hits)))
    return 0 if hits else 1


WEIGHT = {"breakout20": 3, "bullflag": 3, "reclaim20": 2, "trend": 1, "nr7": 1, "insideday": 1,
          "oversoldbounce": 2, "oversold": 1}


def score_daily(found):
    return sum(WEIGHT.get(f[0], 1) for f in found)


# --------------------------------------------------------------------------
# opening range breakout
# --------------------------------------------------------------------------

def opening_range(bars, minutes, day):
    """bars on `day` -> (or_high, or_low, or_end_ts, or_volume) from the first `minutes`."""
    start = dt.datetime.combine(dt.date.fromisoformat(day), nc.OPEN, nc.IST)
    end = start + dt.timedelta(minutes=minutes)
    inside = [b for b in bars if start.timestamp() <= b[0] < end.timestamp()]
    if not inside:
        return None
    return max(b[2] for b in inside), min(b[3] for b in inside), int(end.timestamp()), sum(b[5] for b in inside)


def orb_hit(bars, or_minutes, day, cutoff_ts=None, vol_mult=1.0):
    """First completed 5-minute bar after the range that CLOSES above the range high."""
    rng = opening_range(bars, or_minutes, day)
    if not rng:
        return None
    hi, lo, or_end, or_vol = rng
    n_or = max(1, or_minutes // 5)
    avg_or_vol = or_vol / n_or
    for b in bars:
        if b[0] < or_end:
            continue
        if cutoff_ts and b[0] + 300 > cutoff_ts:  # the bar is not complete yet
            break
        if b[4] > hi and (vol_mult <= 0 or b[5] >= avg_or_vol * vol_mult):
            return {"or_high": round(hi, 2), "or_low": round(lo, 2), "trigger_ts": b[0],
                    "trigger_time": nc.fmt_time_ist(b[0]), "trigger_close": round(b[4], 2),
                    "bar_volume": int(b[5]), "or_avg_bar_volume": int(avg_or_vol),
                    "range_pct": round((hi - lo) / lo * 100, 2) if lo else None}
    return None


def cmd_orb(a):
    t = nc.now_ist(a.now)
    if a.at:
        hh, mm = a.at.split(":")
        t = t.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    day = t.date().isoformat()
    if not nc.is_trading_day(t.date()):
        print("%s is not an NSE trading day." % day)
        return 1
    or_end = dt.datetime.combine(t.date(), nc.OPEN, nc.IST) + dt.timedelta(minutes=a.or_minutes)
    if t < or_end + dt.timedelta(minutes=5) and not a.offline:
        print("Too early: the %d-minute opening range ends at %s IST and the first bar after it closes at %s." % (
            a.or_minutes, or_end.strftime("%H:%M"), (or_end + dt.timedelta(minutes=5)).strftime("%H:%M")))
        return 1
    rows = load_universe(a)
    symbols = [r[0] for r in rows]
    names = {r[0]: r[1] for r in rows}
    cfg = nc.load_config()
    no_after = nc.parse_hhmm(cfg.get("no_entries_after"), dt.time(14, 45))
    t0 = time.time()
    if a.offline:
        data = load_offline(a)
        stage2 = {s: d["bars"] for s, d in data.items() if d.get("bars")}
        failed = []
    else:
        # stage 1: 5-minute closes for every symbol (spark); keep those whose last close is above the OR high
        nc.progress(a, "Stage 1: 5-minute closes for %d symbols (%d spark requests)..." % (
            len(symbols), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = spark_fetch(symbols, "1d", "5m", a)
        cand = []
        for s, d in data.items():
            pairs = [(ts, c) for ts, c in zip(d["ts"], d["close"])]
            start = dt.datetime.combine(t.date(), nc.OPEN, nc.IST).timestamp()
            inside = [c for ts, c in pairs if start <= ts < or_end.timestamp()]
            after = [(ts, c) for ts, c in pairs if or_end.timestamp() <= ts and ts + 300 <= t.timestamp()]
            if inside and after and max(c for _, c in after) > max(inside) and after[-1][1] >= a.min_price:
                cand.append(s)
        cand = cand[: a.max_confirm]
        nc.progress(a, "Stage 2: confirming %d candidates on full 5-minute bars..." % len(cand))
        stage2 = {}
        for i, s in enumerate(cand):
            try:
                ch = nc.yahoo_chart(s, "1d", "5m")
                stage2[s] = nc.session_bars(nc.clean_bars(ch), day)
            except nc.DataError:
                failed.append(s)
            time.sleep(a.pause / 2)
    hits = []
    for s, bars in stage2.items():
        h = orb_hit(bars, a.or_minutes, day, cutoff_ts=int(t.timestamp()), vol_mult=a.vol_mult)
        if not h:
            continue
        trig_time = dt.datetime.fromtimestamp(h["trigger_ts"], nc.IST).time()
        late = trig_time > no_after
        h.update({"symbol": s, "name": names.get(s, s), "setup": "orb", "late": late,
                  "last": round(bars[-1][4], 2) if bars else None})
        hits.append(h)
    hits.sort(key=lambda h: (h["late"], h["trigger_ts"], -h["bar_volume"]))
    doc = {"mode": "orb", "as_of": t.strftime("%Y-%m-%d %H:%M IST"), "or_minutes": a.or_minutes,
           "universe": len(symbols), "scanned": len(data), "failed": len(failed), "hits": hits,
           "note": "Bars that closed above the opening range on free delayed Yahoo data. Not a list of stocks to buy."}
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print("Opening-range breakouts at %s (%d-minute range). %d of %d read, %d failed, %.0fs." % (
            doc["as_of"], a.or_minutes, len(data), len(symbols), len(failed), time.time() - t0))
        if not hits:
            print("Nothing has closed above its opening range yet.")
        for h in hits[: a.show]:
            print("  %-12s range %8.2f-%-8.2f broke at %s close %8.2f  vol %s%s" % (
                h["symbol"], h["or_low"], h["or_high"], h["trigger_time"], h["trigger_close"],
                h["bar_volume"], "  (after the entry window)" if h["late"] else ""))
    if not a.no_write:
        p = nc.write_json(nc.ensure_trader_dir() / "watchlist.json", doc)
        nc.progress(a, "Wrote %s (%d hits)" % (p, len(hits)))
    return 0 if hits else 1


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["universe", "daily", "orb"])
    ap.add_argument("--symbols", help="comma list, e.g. RELIANCE,TCS,INFY (skips the universe)")
    ap.add_argument("--watchlist", help="scan only hits[] of this watchlist.json")
    ap.add_argument("--universe-file")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--setups", help="daily: only these, comma list (breakout20,bullflag,reclaim20,oversold,oversoldbounce,nr7,insideday,trend)")
    ap.add_argument("--min-price", type=float, default=50.0, help="rupees (default 50)")
    ap.add_argument("--min-volume", type=int, default=500000, help="daily: 20-day average shares (default 5,00,000)")
    ap.add_argument("--vol-mult", type=float, default=1.0, help="orb: breakout bar volume vs average OR bar (default 1.0, 0 = off)")
    ap.add_argument("--or-minutes", type=int, default=15)
    ap.add_argument("--at", help="orb: cut the clock at HH:MM IST")
    ap.add_argument("--now", help="pin the clock: 'YYYY-MM-DD HH:MM' IST")
    ap.add_argument("--max-confirm", type=int, default=150)
    ap.add_argument("--pause", type=float, default=0.4)
    ap.add_argument("--offline", help="read saved Yahoo JSON files from this folder")
    ap.add_argument("--include-surveillance", action="store_true", help="keep ASM/GSM symbols")
    ap.add_argument("--no-surveillance", action="store_true", help="universe: skip the ASM/GSM lookup")
    ap.add_argument("--index", help="universe: an NSE index instead of NIFTY 500, e.g. 'NIFTY 200'")
    ap.add_argument("--no-fno", action="store_true", help="universe: do not add the F&O list")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.mode](a)
    except nc.DataError as e:
        print("Scan could not run: %s" % e)
        return 1
    except KeyboardInterrupt:
        print("Stopped.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
