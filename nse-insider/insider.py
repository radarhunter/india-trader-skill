#!/usr/bin/env python3
"""nse-insider: the Insider Tracker for NSE.

  insider   Promoter / director / KMP trades disclosed under SEBI (Prohibition
            of Insider Trading) Regulations 7(2) - the Indian STOCK Act:
            who, their category, buy or sell, shares, value, the mode
            (market, off-market, pledge, ESOP), the trade date and how many
            days it took to disclose. Flags trades in your holdings and
            watchlist. Alerts once per new filing.
  deals     Today's bulk deals (>= 0.5% of equity in a day) and block deals
            (the 08:45 and 14:05 windows), and short-selling disclosures.
  flows     FII / DII net buying and selling in the cash market, by day.
  watch     Poll insider filings for your symbols and ring on a new one.

Sources: nseindia.com (cookie handshake; refuses now and then) and NSE's
archive CSVs. Exit 0 when something was found.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
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


def seen_path():
    return nc.trader_dir() / "insider_seen.json"


def my_symbols():
    cfg = nc.load_config()
    syms = set()
    for p in nc.load_holdings(cfg).get("positions") or []:
        syms.add(p["symbol"].upper())
    for h in (nc.read_json(nc.trader_dir() / "watchlist.json", {}) or {}).get("hits") or []:
        syms.add(h["symbol"].upper())
    return syms


def ddmmyyyy(d):
    return d.strftime("%d-%m-%Y")


def parse_any_date(s):
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return dt.datetime.strptime(s[:20], fmt).date()
        except ValueError:
            continue
    return None


XBRL_TAGS = {
    "name": "NameOfThePerson", "category": "CategoryOfPerson", "instrument": "TypeOfInstrument",
    "pre_qty": "SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity",
    "pre_pct": "SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding",
    "qty": "SecuritiesAcquiredOrDisposedNumberOfSecurity", "value": "SecuritiesAcquiredOrDisposedValueOfSecurity",
    "type": "SecuritiesAcquiredOrDisposedTransactionType",
    "post_qty": "SecuritiesHeldPostAcquistionOrDisposalNumberOfSecurity",
    "post_pct": "SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding",
    "from": "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate",
    "to": "DateOfAllotmentAdviceOrAcquisitionOrSaleOfSharesSpecifyToDate",
    "to2": "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate",
    "mode": "ModeOfAcquisitionOrDisposal", "intimated": "DateOfIntimationToCompany",
    "exchange": "ExchangeOnWhichTheTradeWasExecuted",
}


def parse_xbrl(xml_text):
    """SEBI PIT Reg 7(2) XBRL -> list of disclosures (one per contextRef Disclosure<n>)."""
    import re as _re
    by_ctx = {}
    for m in _re.finditer(r"<[A-Za-z0-9_-]+:([A-Za-z0-9_]+)\s+[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]*)<", xml_text):
        tag, ctx, val = m.group(1), m.group(2), m.group(3).strip()
        by_ctx.setdefault(ctx, {})[tag] = val
    out = []
    for ctx, tags in by_ctx.items():
        if not ctx.lower().startswith("disclosure"):
            continue
        g = lambda k: tags.get(XBRL_TAGS[k]) if k in XBRL_TAGS else None
        out.append({"name": g("name"), "category": g("category"), "instrument": g("instrument"),
                    "qty": num(g("qty")), "value_inr": num(g("value")), "type": g("type"), "mode": g("mode"),
                    "pre_pct": num(g("pre_pct")), "post_pct": num(g("post_pct")),
                    "trade_from": g("from"), "trade_to": g("to") or g("to2"), "intimated": g("intimated"),
                    "exchange": g("exchange")})
    return out


def fetch_insider(sess, days, symbol=None, mine=None, max_xml=60, pause=0.4):
    """NSE's insider-filing list (corporates-pit-gg) + the XBRL of each filing for the trade details.
    Returns rows newest first. Only the first `max_xml` filings (after the symbol / mine filter) are expanded."""
    doc = sess.json("/api/corporates-pit-gg?index=equities")
    filings = doc.get("data") if isinstance(doc, dict) else doc
    if not filings:
        raise nc.DataError("insider filing list came back empty")
    today = nc.now_ist().date()
    cutoff = today - dt.timedelta(days=days)
    keep = []
    for f in filings:
        sym = (f.get("symbol") or "").upper()
        if symbol and sym != symbol:
            continue
        if mine is not None and sym not in mine:
            continue
        bd = parse_any_date((f.get("broadcastDateTime") or "")[:11])
        if bd and bd < cutoff:
            continue
        keep.append(f)
    keep.sort(key=lambda f: f.get("broadcastDateTime") or "", reverse=True)
    out = []
    for f in keep[:max_xml]:
        sym = (f.get("symbol") or "").upper()
        xml_url = f.get("xmlFileName") or ""
        broadcast = (f.get("broadcastDateTime") or "").strip()
        bd = parse_any_date(broadcast[:11])
        details = []
        if xml_url:
            try:
                details = parse_xbrl(nc.http_get(xml_url, headers={"Referer": nc.NSE_BASE + "/"}, retries=1))
            except nc.DataError:
                details = []
            time.sleep(pause)
        if not details:
            out.append({"id": "%s|%s|%s" % (sym, f.get("appId"), broadcast), "symbol": sym, "company": f.get("companyName"),
                        "name": None, "category": None, "type": None, "mode": None, "qty": None, "value_inr": None,
                        "pre_pct": None, "post_pct": None, "trade_date": None, "disclosed": bd.isoformat() if bd else None,
                        "days_to_disclose": None, "broadcast": broadcast, "xbrl": xml_url, "note": "details not parsed"})
            continue
        for i, d in enumerate(details):
            td = parse_any_date(d.get("trade_to") or d.get("trade_from") or "")
            out.append({"id": "%s|%s|%s|%d" % (sym, f.get("appId"), broadcast, i), "symbol": sym, "company": f.get("companyName"),
                        "name": d.get("name"), "category": d.get("category"), "type": d.get("type"), "mode": d.get("mode"),
                        "security": d.get("instrument"), "qty": d.get("qty"), "value_inr": d.get("value_inr"),
                        "pre_pct": d.get("pre_pct"), "post_pct": d.get("post_pct"),
                        "trade_date": td.isoformat() if td else None, "disclosed": bd.isoformat() if bd else None,
                        "days_to_disclose": (bd - td).days if (bd and td) else None, "broadcast": broadcast, "xbrl": xml_url})
    return out


def num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None


def fetch_deals(sess):
    out = {"bulk": [], "block": [], "short": []}
    try:
        doc = sess.json("/api/snapshot-capital-market-largedeal")
        for key, name in (("BULK_DEALS_DATA", "bulk"), ("BLOCK_DEALS_DATA", "block"), ("SHORT_DEALS_DATA", "short")):
            for r in doc.get(key) or []:
                out[name].append({"symbol": (r.get("symbol") or "").upper(), "name": r.get("name"), "client": r.get("clientName"),
                                  "side": (r.get("buySell") or "").upper(), "qty": num(r.get("qty")), "price": num(r.get("watp")),
                                  "date": r.get("date")})
        if any(out.values()):
            return out, "nse api"
    except nc.DataError:
        pass
    for name, path in (("bulk", "/content/equities/bulk.csv"), ("block", "/content/equities/block.csv")):
        try:
            text = nc.nse_archive(path)
        except nc.DataError:
            continue
        for r in csv.DictReader(io.StringIO(text)):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            out[name].append({"symbol": (r.get("Symbol") or "").upper(), "name": r.get("Security Name"),
                              "client": r.get("Client Name"), "side": (r.get("Buy/Sell") or "").upper(),
                              "qty": num(r.get("Quantity Traded")), "price": num(r.get("Trade Price / Wght. Avg. Price")),
                              "date": r.get("Date")})
    return out, "nse archive csv"


def fetch_flows(sess):
    doc = sess.json("/api/fiidiiTradeReact")
    rows = doc if isinstance(doc, list) else doc.get("data") or []
    out = []
    for r in rows:
        out.append({"category": r.get("category"), "date": r.get("date"), "buy_cr": num(r.get("buyValue")),
                    "sell_cr": num(r.get("sellValue")), "net_cr": num(r.get("netValue"))})
    return out


def cmd_insider(a):
    sess = nc.NSESession()
    mine = my_symbols()
    try:
        rows = fetch_insider(sess, a.days, a.symbol.upper() if a.symbol else None, mine if a.mine else None, a.max_xml, a.pause)
    except nc.DataError as e:
        print("Insider feed did not answer: %s" % e)
        return 1
    seen = set((nc.read_json(seen_path(), {}) or {}).get("ids", []))
    if a.min_value:
        rows = [r for r in rows if (r["value_inr"] or 0) >= a.min_value]
    rows.sort(key=lambda r: (r["disclosed"] or "", r["symbol"]), reverse=True)
    new = [r for r in rows if r["id"] not in seen]
    for r in rows:
        r["mine"] = r["symbol"] in mine
    doc = {"as_of": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "days": a.days, "rows": rows, "new": len(new)}
    nc.write_json(nc.ensure_trader_dir() / "insider.json", doc)
    if not a.all:
        old = (nc.read_json(seen_path(), {}) or {}).get("ids", [])
        nc.write_json(seen_path(), {"ids": (old + [r["id"] for r in rows if r["id"] not in seen])[-5000:]})
    show = rows if a.all else new
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if show else 1
    print("Insider filings (SEBI PIT Reg 7(2)), last %d days: %d disclosures read, %d new.%s" % (
        a.days, len(rows), len(new), "" if a.all else " Showing the new ones."))
    for r in show[: a.show]:
        print("  %s%-12s %-28s %-22s %-10s %10s sh  %12s  %s  trade %s, disclosed %s%s" % (
            "* " if r["mine"] else "  ", r["symbol"], (r["name"] or "")[:28], (r["category"] or "")[:22], (r["type"] or "")[:10],
            fmtq(r["qty"]), nc.inr(r["value_inr"], 0) if r["value_inr"] else "-", (r["mode"] or "")[:12], r["trade_date"], r["disclosed"],
            (" (%d d)" % r["days_to_disclose"]) if r["days_to_disclose"] is not None else ""))
    if any(r["mine"] for r in show):
        print("  * = in your holdings or watchlist")
    return 0 if show else 1


def fmtq(q):
    return "-" if q is None else format(int(q), ",")


def cmd_deals(a):
    sess = nc.NSESession()
    deals, src = fetch_deals(sess)
    mine = my_symbols()
    total = sum(len(v) for v in deals.values())
    nc.write_json(nc.ensure_trader_dir() / "deals.json", {"as_of": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "source": src, "deals": deals})
    if a.json:
        print(json.dumps(deals, indent=2, ensure_ascii=False))
        return 0 if total else 1
    if not total:
        print("No bulk / block deals published yet (NSE posts them after the close, usually by 18:00 IST).")
        return 1
    for name in ("block", "bulk", "short"):
        rows = deals[name]
        if not rows:
            continue
        print("%s deals (%d, %s):" % (name.title(), len(rows), src))
        for r in sorted(rows, key=lambda r: -(r["qty"] or 0) * (r["price"] or 0))[: a.show]:
            print("  %s%-12s %-4s %12s sh @ %-10s %s  %s" % ("* " if r["symbol"] in mine else "  ", r["symbol"], r["side"],
                                                            fmtq(r["qty"]), nc.inr(r["price"]) if r["price"] else "-",
                                                            nc.inr((r["qty"] or 0) * (r["price"] or 0), 0), (r["client"] or "")[:40]))
    return 0


def cmd_flows(a):
    sess = nc.NSESession()
    try:
        rows = fetch_flows(sess)
    except nc.DataError as e:
        print("FII/DII feed did not answer: %s" % e)
        return 1
    nc.write_json(nc.ensure_trader_dir() / "flows.json", {"as_of": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "rows": rows})
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    print("FII / DII cash market (Rs crore):")
    for r in rows:
        print("  %-6s %-12s buy %10s  sell %10s  net %+10s" % (r["category"], r["date"], fmtn(r["buy_cr"]), fmtn(r["sell_cr"]), fmtn(r["net_cr"])))
    return 0


def fmtn(v):
    return "-" if v is None else "%,.0f" % v


def cmd_watch(a):
    cfg = nc.load_config()
    poll = a.poll or max(120, int(cfg.get("watch_poll_seconds", 30)) * 4)
    while True:
        a.all = False
        a.mine = True
        rc = cmd_insider(a)
        if rc == 0 and cfg.get("notify", True):
            nc.notify("nse-insider", "New insider filing in a stock you hold or watch")
        if a.once:
            return rc
        time.sleep(poll)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["insider", "deals", "flows", "watch"])
    ap.add_argument("--symbol")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-value", type=float, default=0, help="rupees")
    ap.add_argument("--mine", action="store_true", help="only holdings and watchlist symbols")
    ap.add_argument("--all", action="store_true", help="show all, not just new")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--max-xml", type=int, default=60, help="insider: how many filings to open for details (each is one request)")
    ap.add_argument("--pause", type=float, default=0.4)
    ap.add_argument("--poll", type=int)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"insider": cmd_insider, "deals": cmd_deals, "flows": cmd_flows, "watch": cmd_watch}[a.cmd](a)
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
