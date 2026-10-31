#!/usr/bin/env python3
"""nse-news: the News Monitor for NSE / BSE stocks.

For every symbol you watch it reads, newest first:
  * Yahoo Finance headlines (RSS, .NS symbols)
  * NSE corporate announcements (results, board meetings, orders, pledges,
    acquisitions, rating actions - the filings every listed company must make
    under SEBI LODR Regulation 30)
  * NSE board-meeting calendar (so you know when results land)
  * Google News India as a fallback when the first two are thin
and how far the stock is from yesterday's close. Shows only what is new since
the last check, tags each item, and never repeats one.

Exit 0 when there is something new, 1 when nothing or it could not run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402

TAGS = [
    ("results", r"\b(result|results|q[1-4]\s*fy|quarter(ly)?|earnings|net profit|revenue|ebitda|pat\b)"),
    ("boardmeeting", r"board meeting|outcome of (the )?board"),
    ("dividend", r"\bdividend|buy-?back|bonus (issue|share)|stock split|record date"),
    ("order", r"\border(s)? (win|worth|from|received|book)|contract (win|worth|awarded)|letter of award|loa\b"),
    ("deal", r"acqui|merger|amalgamation|stake|joint venture|\bjv\b|demerger|takeover|slump sale|divest"),
    ("fundraise", r"\bqip\b|preferential (issue|allotment)|rights issue|\bfpo\b|\bipo\b|debenture|ncd\b|fund ?raising"),
    ("pledge", r"pledge|encumbr|invocation"),
    ("blockdeal", r"block deal|bulk deal|open offer|promoter (sells|sold|buys|bought)"),
    ("rating", r"\brating|crisil|icra|care ratings|india ratings|brickwork|target price|upgrade|downgrade|initiat"),
    ("management", r"\bresign|appoint|ceo\b|cfo\b|managing director|chairman|key managerial|kmp\b"),
    ("regulatory", r"\bsebi\b|show cause|penalt|\brbi\b|\bnclt\b|\birda|\btrai\b|\bcci\b|court|high court|supreme court|investigation|\bed\b raid|income tax"),
    ("halt", r"circuit|trading halt|suspend|\basm\b|\bgsm\b|surveillance|trade-for-trade|t2t\b"),
    ("capex", r"capacity|expansion|plant|commission|capex|greenfield|brownfield"),
    ("guidance", r"guidance|outlook|concall|conference call|investor (presentation|meet)|analyst meet"),
    ("macro", r"\bnifty\b|sensex|\bfii\b|\bdii\b|rupee|crude|fed\b|inflation|gdp|budget|rate (cut|hike)|monetary policy|tariff"),
]
FILING_IGNORE = r"newspaper publication|newspaper advertisement|compliance certificate|reg(ulation)? 74|closure of trading window|trading window|certificate under|annual secretarial|loss of share certificate|duplicate share|change in (registered )?office|disclosure under reg(ulation)? 29|reg(ulation)? 7\(|intimation of (shifting|change in address)|statement of investor complaints|reconciliation of share capital|schedule of analyst|analyst(s)?/institutional investor meet"


def tag_text(text):
    low = text.lower()
    found = [t for t, rx in TAGS if re.search(rx, low)]
    return found or ["other"]


def seen_path():
    return nc.trader_dir() / "news_seen.json"


def load_seen():
    return set((nc.read_json(seen_path(), {}) or {}).get("ids", []))


def save_seen(new_ids):
    old = (nc.read_json(seen_path(), {}) or {}).get("ids", [])
    seen = set(old)
    nc.write_json(seen_path(), {"ids": (old + [i for i in new_ids if i not in seen])[-5000:],
                                "updated": nc.now_ist().strftime("%Y-%m-%d %H:%M IST")})


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def parse_rss(text, source):
    items = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        ts = parse_pubdate(pub)
        if not title:
            continue
        items.append({"id": "%s:%s" % (source, link or title), "title": title, "link": link,
                      "ts": ts, "source": source})
    return items


def parse_pubdate(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return int(d.timestamp())
        except ValueError:
            continue
    return 0


def yahoo_headlines(sym):
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN" % urllib.parse.quote(nc.yahoo_symbol(sym))
    try:
        return parse_rss(nc.http_get(url, retries=1), "yahoo")
    except nc.DataError:
        return []


def google_news(sym, name):
    q = '"%s" stock' % (name or sym)
    url = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(q)
    try:
        return parse_rss(nc.http_get(url, retries=1), "google")
    except nc.DataError:
        return []


def nse_announcements(sess, sym):
    items = []
    try:
        doc = sess.json("/api/corporate-announcements?index=equities&symbol=%s" % urllib.parse.quote(sym))
    except nc.DataError:
        return items, False
    rows = doc if isinstance(doc, list) else (doc.get("data") or [])
    for r in rows:
        desc = (r.get("desc") or r.get("subject") or "").strip()
        detail = (r.get("attchmntText") or r.get("sm_desc") or "").strip()
        title = desc
        if detail and detail.lower() != desc.lower():
            title = "%s - %s" % (desc, detail[:160])
        if re.search(FILING_IGNORE, title.lower()):
            continue
        when = r.get("an_dt") or r.get("exchdisstime") or r.get("sort_date") or ""
        ts = parse_nse_dt(when)
        link = r.get("attchmntFile") or ""
        items.append({"id": "nse:%s:%s:%s" % (sym, when, desc[:60]), "title": title, "link": link,
                      "ts": ts, "source": "nse-filing"})
    return items, True


def parse_nse_dt(s):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return int(dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=nc.IST).timestamp())
        except ValueError:
            continue
    return 0


def nse_board_meetings(sess, sym):
    try:
        doc = sess.json("/api/corporate-board-meetings?index=equities&symbol=%s" % urllib.parse.quote(sym))
    except nc.DataError:
        return []
    rows = doc if isinstance(doc, list) else (doc.get("data") or [])
    out = []
    for r in rows:
        d = (r.get("bm_date") or r.get("meetingdate") or "").strip()
        purpose = (r.get("bm_desc") or r.get("bm_purpose") or r.get("purpose") or "").strip()
        if d:
            out.append({"date": d, "purpose": purpose})
    return out


def gap_from_close(sym):
    try:
        q = nc.yahoo_quote_fast(sym)
    except nc.DataError:
        return None
    last, pc = q.get("last"), q.get("prev_close")
    if not last or not pc:
        return None
    return {"last": round(last, 2), "prev_close": round(pc, 2), "change_pct": round((last - pc) / pc * 100, 2),
            "name": q.get("name")}


# --------------------------------------------------------------------------

def load_symbols(a):
    if a.symbols:
        return [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    wl = nc.read_json(a.watchlist or (nc.trader_dir() / "watchlist.json"), None)
    if wl and wl.get("hits"):
        return [h["symbol"] for h in wl["hits"]][: a.limit or None]
    hold = nc.load_holdings()
    if hold and hold.get("positions"):
        return [p["symbol"].upper() for p in hold["positions"]]
    raise nc.DataError("no symbols: pass --symbols or run /nse-scanner first")


def upcoming_results(bms, today):
    out = []
    for b in bms:
        if not re.search(r"result|financial", b["purpose"].lower()):
            continue
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                d = dt.datetime.strptime(b["date"][:11].strip(), fmt).date()
                break
            except ValueError:
                d = None
        if d and 0 <= (d - today).days <= 10:
            out.append((d, b["purpose"]))
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbols", nargs="?", help="comma list; default: trader/watchlist.json, then holdings")
    ap.add_argument("--symbols", dest="symbols_flag")
    ap.add_argument("--watchlist")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--all", action="store_true", help="show everything, not just what is new")
    ap.add_argument("--hours", type=float, default=36, help="ignore items older than this (default 36)")
    ap.add_argument("--no-filings", action="store_true", help="skip NSE announcements")
    ap.add_argument("--no-quotes", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--pause", type=float, default=0.5)
    a = ap.parse_args(argv)
    if a.symbols_flag:
        a.symbols = a.symbols_flag
    try:
        syms = load_symbols(a)
    except nc.DataError as e:
        print("News could not run: %s" % e)
        return 1
    seen = set() if a.all else load_seen()
    now = nc.now_ist()
    cutoff = int(now.timestamp()) - int(a.hours * 3600)
    sess = nc.NSESession()
    report, new_ids, filings_ok = [], set(), True
    nc.progress(a, "Reading news for %d symbols..." % len(syms))
    for sym in syms:
        entry = {"symbol": sym, "quote": None, "items": [], "results_due": []}
        if not a.no_quotes:
            entry["quote"] = gap_from_close(sym)
        items = yahoo_headlines(sym)
        if not a.no_filings and filings_ok:
            fil, ok = nse_announcements(sess, sym)
            if not ok:
                filings_ok = False
                nc.progress(a, "NSE announcements are not answering; continuing with headlines only.")
            items += fil
            bms = nse_board_meetings(sess, sym) if ok else []
            entry["results_due"] = [{"date": d.isoformat(), "purpose": p} for d, p in upcoming_results(bms, now.date())]
        if len([i for i in items if i["ts"] >= cutoff]) < 2:
            items += google_news(sym, (entry["quote"] or {}).get("name") or sym)
        fresh = []
        for it in items:
            if it["ts"] and it["ts"] < cutoff:
                continue
            if it["id"] in seen or it["id"] in new_ids:
                continue
            it["tags"] = tag_text(it["title"])
            it["when"] = nc.fmt_dt_ist(it["ts"]) if it["ts"] else "undated"
            fresh.append(it)
            new_ids.add(it["id"])
        fresh.sort(key=lambda i: -i["ts"])
        entry["items"] = fresh
        report.append(entry)
        time.sleep(a.pause)
    if not a.all:
        save_seen(sorted(new_ids))
    total = sum(len(e["items"]) for e in report)
    doc = {"as_of": now.strftime("%Y-%m-%d %H:%M IST"), "symbols": syms, "new_items": total, "report": report}
    nc.write_json(nc.ensure_trader_dir() / "news.json", doc)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if total else 1
    print("News check %s. %d new item%s across %d symbols." % (doc["as_of"], total, "" if total == 1 else "s", len(syms)))
    for e in report:
        q = e["quote"]
        head = e["symbol"]
        if q:
            head += "  %s (%s vs prev close %s)" % (nc.inr(q["last"]), nc.pct(q["change_pct"]), nc.inr(q["prev_close"]))
        print("\n" + head)
        for r in e["results_due"]:
            print("  !! board meeting %s: %s" % (r["date"], r["purpose"][:70]))
        if not e["items"]:
            print("  nothing new")
        for it in e["items"][:12]:
            print("  [%s] %s  %s  (%s)" % (",".join(it["tags"]), it["when"], it["title"][:110], it["source"]))
    print("\nSaved trader/news.json. Headlines are tagged by keyword; read the filing before trading on it.")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
