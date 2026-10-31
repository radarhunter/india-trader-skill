#!/usr/bin/env python3
"""nse-results: the Results Analyst for NSE-listed companies.

Reads a company's reported quarterly numbers from NSE's financial-results
feed (the figures companies file under SEBI LODR Regulation 33: total
income / revenue from operations, profit before tax, net profit, EPS) and
sums up what changed: each quarter against the previous one and against
the same quarter a year earlier, margins, and the trailing four quarters.
Also shows the next board meeting for results and the dividend record
dates. NSE's site rate-limits; when it will not answer the tool says so.

Exit 0 when it read something.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402

# NSE results-comparision (resCmpData) fields, verified 31 Oct 2026. Figures are in lakhs for most filers.
RC = {
    "revenue": ("re_net_sale", "re_tot_inc", "re_total_inc", "re_int_earned"),
    "total_income": ("re_total_inc", "re_tot_inc"),
    "other_income": ("re_oth_inc_new", "re_oth_inc"),
    "expenses": ("re_oth_tot_exp", "re_tot_exp_exc_pro_cont"),
    "pbt": ("re_pro_loss_bef_tax", "re_pro_loss_bef_tax_sum"),
    "tax": ("re_tax",),
    "net_profit": ("re_net_profit", "re_con_pro_loss", "re_proloss_ord_act"),
    "eps": ("re_diluted_eps", "re_dilut_eps_for_cont_dic_opr", "re_basic_eps", "re_basic_eps_for_cont_dic_opr"),
    "exceptional": ("re_excepn_items_new", "re_excepn_items"),
}


def num(v):
    if v in (None, "", "-", "NA", "null"):
        return None
    try:
        return float(str(v).replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return None


def first(row, keys):
    for k in keys:
        v = num(row.get(k))
        if v is not None:
            return v
    return None


def normalise(row):
    out = {k: first(row, keys) for k, keys in RC.items()}
    period = row.get("re_to_dt") or row.get("toDate") or row.get("to_date") or ""
    out["period"] = str(period).strip()
    out["period_date"] = parse_period(out["period"])
    out["from_date"] = parse_period(str(row.get("re_from_dt") or row.get("fromDate") or ""))
    out["consolidated"] = str(row.get("re_cons_nature") or row.get("consolidated") or "").strip().lower().startswith("c")
    out["audited"] = "audited" if str(row.get("re_res_type") or "").upper() == "A" else ("unaudited" if str(row.get("re_res_type") or "").upper() == "U" else str(row.get("audited") or "").lower())
    out["notes"] = (row.get("re_desc_note_fin") or "")[:400]
    out["seq"] = row.get("re_seq_num")
    return out


def parse_period(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s[:11].strip(), fmt).date()
        except ValueError:
            continue
    return None


def fetch_nse(sess, sym):
    """results-comparision carries the numbers; corporates-financial-results carries filing metadata only."""
    doc = sess.json("/api/results-comparision?symbol=%s" % urllib.parse.quote(sym))
    rows = doc.get("resCmpData") if isinstance(doc, dict) else None
    if not rows:
        raise nc.DataError("NSE results-comparision returned no rows for %s" % sym)
    norm = [normalise(r) for r in rows if isinstance(r, dict)]
    norm = [n for n in norm if n["period_date"] and (n["revenue"] is not None or n["net_profit"] is not None)]
    # a row whose from->to span is more than ~4 months is a half-year or annual figure; keep quarters only
    quarters = [n for n in norm if not n["from_date"] or (n["period_date"] - n["from_date"]).days <= 125]
    if not quarters:
        raise nc.DataError("NSE results for %s have no quarterly rows" % sym)
    bank = bool((doc.get("bankNonBnking") or "").lower().startswith("b")) if isinstance(doc, dict) else False
    return quarters, "nse results-comparision%s" % (" (bank)" if bank else "")


def pct_change(a, b):
    if a is None or b in (None, 0):
        return None
    return (a - b) / abs(b) * 100


def analyse(rows, prefer_consolidated=True):
    cons = [r for r in rows if r["consolidated"]]
    use = cons if (prefer_consolidated and len(cons) >= 2) else rows
    # one row per period, newest first
    byp = {}
    for r in use:
        byp.setdefault(r["period_date"], r)
    periods = sorted(byp, reverse=True)[:9]
    table = []
    for i, p in enumerate(periods):
        r = byp[p]
        prev = byp[periods[i + 1]] if i + 1 < len(periods) else None
        yoy = next((byp[q] for q in periods[i + 1:] if abs((p - q).days - 365) <= 20), None)
        margin = (r["net_profit"] / r["revenue"] * 100) if r["revenue"] and r["net_profit"] is not None else None
        table.append({
            "period": p.isoformat(), "revenue": r["revenue"], "pbt": r["pbt"], "net_profit": r["net_profit"], "eps": r["eps"],
            "net_margin_pct": round(margin, 1) if margin is not None else None,
            "rev_qoq_pct": round(pct_change(r["revenue"], prev["revenue"]), 1) if prev and pct_change(r["revenue"], prev["revenue"]) is not None else None,
            "rev_yoy_pct": round(pct_change(r["revenue"], yoy["revenue"]), 1) if yoy and pct_change(r["revenue"], yoy["revenue"]) is not None else None,
            "np_qoq_pct": round(pct_change(r["net_profit"], prev["net_profit"]), 1) if prev and pct_change(r["net_profit"], prev["net_profit"]) is not None else None,
            "np_yoy_pct": round(pct_change(r["net_profit"], yoy["net_profit"]), 1) if yoy and pct_change(r["net_profit"], yoy["net_profit"]) is not None else None,
            "consolidated": r["consolidated"], "audited": r["audited"],
        })
    ttm = None
    if len(table) >= 4 and all(t["net_profit"] is not None for t in table[:4]):
        ds = [dt.date.fromisoformat(t["period"]) for t in table[:4]]
        if all(60 <= (ds[i] - ds[i + 1]).days <= 125 for i in range(3)):
            ttm = {"revenue": sum(t["revenue"] or 0 for t in table[:4]), "net_profit": sum(t["net_profit"] for t in table[:4])}
    return table, ttm, ("consolidated" if use is cons else "standalone")


def summarise(table):
    if not table:
        return "no quarters to compare"
    t = table[0]
    parts = []
    if t["rev_yoy_pct"] is not None:
        parts.append("revenue %s YoY" % nc.pct(t["rev_yoy_pct"], 1))
    if t["rev_qoq_pct"] is not None:
        parts.append("%s QoQ" % nc.pct(t["rev_qoq_pct"], 1))
    if t["np_yoy_pct"] is not None:
        parts.append("net profit %s YoY" % nc.pct(t["np_yoy_pct"], 1))
    if t["np_qoq_pct"] is not None:
        parts.append("%s QoQ" % nc.pct(t["np_qoq_pct"], 1))
    if t["net_margin_pct"] is not None and len(table) > 1 and table[1]["net_margin_pct"] is not None:
        d = t["net_margin_pct"] - table[1]["net_margin_pct"]
        parts.append("net margin %.1f%% (%s%.1f pts vs last quarter)" % (t["net_margin_pct"], "+" if d >= 0 else "", d))
    return "latest quarter (%s): " % t["period"] + ", ".join(parts) if parts else "latest quarter %s: numbers present but no comparison possible" % t["period"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("--standalone", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", help="a saved NSE results JSON")
    a = ap.parse_args(argv)
    sym = a.symbol.upper()
    sess = nc.NSESession()
    source, note = None, ""
    try:
        if a.offline:
            doc = json.loads(Path(a.offline).read_text(encoding="utf-8"))
            rows = [normalise(r) for r in (doc if isinstance(doc, list) else doc.get("resCmpData") or doc.get("data") or [])]
            rows = [r for r in rows if r["period_date"]]
            source = "offline"
        else:
            rows, source = fetch_nse(sess, sym)
    except nc.DataError as e:
        print("Results could not run for %s: %s. NSE's site rate-limits; try again in a minute." % (sym, e))
        return 1
    table, ttm, basis = analyse(rows, not a.standalone)
    bms = []
    if not a.offline:
        try:
            doc = sess.json("/api/corporate-board-meetings?index=equities&symbol=%s" % urllib.parse.quote(sym))
            for r in (doc if isinstance(doc, list) else doc.get("data") or []):
                desc = str(r.get("bm_desc") or r.get("bm_purpose") or r.get("purpose") or "")
                d = r.get("bm_date") or r.get("meetingdate") or ""
                bd = parse_period(str(d))
                if re.search(r"result|financial", desc.lower()) and bd and bd >= nc.now_ist().date():
                    bms.append({"date": d, "purpose": desc})
            bms.sort(key=lambda b: parse_period(str(b["date"])) or dt.date.max)
        except nc.DataError:
            pass
    out = {"symbol": sym, "as_of": nc.now_ist().strftime("%Y-%m-%d %H:%M IST"), "source": source, "basis": basis,
           "unit": "as filed (NSE: lakhs for most companies)", "note": note,
           "quarters": table, "ttm": ttm, "board_meetings": bms[:3], "summary": summarise(table)}
    nc.write_json(nc.ensure_trader_dir() / "results_%s.json" % sym, out)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0
    print("%s results (%s, source %s)%s" % (sym, basis, source, ("  - " + note) if note else ""))
    print("  %-11s %12s %12s %12s %8s %8s %8s %8s %8s" % ("period", "revenue", "PBT", "net profit", "EPS", "margin", "rev YoY", "NP YoY", "NP QoQ"))
    for t in table:
            print("  %-11s %12s %12s %12s %8s %8s %8s %8s %8s" % (
            t["period"], fmtn(t["revenue"]), fmtn(t["pbt"]), fmtn(t["net_profit"]), fmtn(t["eps"], 2),
            ("%.1f%%" % t["net_margin_pct"]) if t["net_margin_pct"] is not None else "-",
            nc.pct(t["rev_yoy_pct"], 1) if t["rev_yoy_pct"] is not None else "-",
            nc.pct(t["np_yoy_pct"], 1) if t["np_yoy_pct"] is not None else "-",
            nc.pct(t["np_qoq_pct"], 1) if t["np_qoq_pct"] is not None else "-"))
    if ttm:
        print("  TTM: revenue %s, net profit %s" % (fmtn(ttm["revenue"]), fmtn(ttm["net_profit"])))
    print("  " + out["summary"])
    if bms:
        print("  Next board meeting for results: %s (%s)" % (bms[0]["date"], (bms[0]["purpose"] or "")[:60]))
    print("  Figures as filed (NSE: lakhs for most companies, i.e. divide by 100 for crore). Compare growth and margins, not absolutes, across sources.")
    return 0


def fmtn(v, d=0):
    if v is None:
        return "-"
    return ("%%,.%df" % d) % v


if __name__ == "__main__":
    sys.exit(main())
