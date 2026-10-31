#!/usr/bin/env python3
"""nse-broker: the Order Executor. Paper trading by default, never without your yes.

  ticket            Show the order(s) in trader/orders.json that nse-risk
                    PASSED, as a plain ticket: shares, limit, stop, target,
                    the max loss, the product, paper or live, and the exact
                    broker payload (Angel One ROBO / Upstox v2 / Groww).
  place --yes WORD  Send it. Paper: a simulated fill at the limit, logged to
                    trader/trades_log.json and trader/holdings.json. Live:
                    the broker API, keys from environment variables only.
  close SYMBOL      Write the SELL that flattens it (then /nse-risk, then place --yes).
  fill ORDER_ID --price P [--qty Q]   Record a real fill the broker reported.
  status            Open positions, today's trades, today's P&L.

Rules: an order is only sent when (1) trader/risk.json holds a PASS for its
order_id, checked within the last 20 minutes, (2) --yes carries the user's
typed yes word for word, and (3) for live, config paper is false AND the
broker's credentials are in the environment. Your yes is recorded. Exit 0
when the action happened.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_here = Path(__file__).resolve().parent
for _c in (_here.parent / "nse-common", _here / "nse-common", Path.home() / ".claude" / "skills" / "nse-common"):
    if (_c / "nse_common.py").exists():
        sys.path.insert(0, str(_c))
        break
import nse_common as nc  # noqa: E402

PASS_TTL_MIN = 20


log_path = nc.log_path
load_log = nc.load_log
holdings_path = nc.holdings_path
load_holdings = nc.load_holdings


def save_log(doc):
    nc.write_json(nc.log_path(), doc)


def passed_orders(cfg, a):
    orders = (nc.read_json(a.orders or (nc.trader_dir() / "orders.json"), {}) or {}).get("orders") or []
    risk = nc.read_json(nc.trader_dir() / "risk.json", {}) or {}
    verdicts = {v["order_id"]: v for v in risk.get("verdicts") or []}
    checked = risk.get("checked_at")
    fresh = False
    if checked:
        try:
            t = dt.datetime.strptime(checked.replace(" IST", ""), "%Y-%m-%d %H:%M").replace(tzinfo=nc.IST)
            age = (nc.now_ist() - t).total_seconds()
            fresh = 0 <= age <= PASS_TTL_MIN * 60
        except ValueError:
            fresh = False
    out = []
    for o in orders:
        oid = order_id(o)
        v = verdicts.get(oid)
        o = dict(o)
        o["order_id"] = oid
        o["risk"] = "PASS" if v and v.get("verdict") == "PASS" and fresh else (
            "STALE" if v and v.get("verdict") == "PASS" else ("BLOCK" if v else "UNCHECKED"))
        o["risk_reasons"] = (v or {}).get("reasons", [])
        out.append(o)
    return out


def order_id(o):
    key = json.dumps({k: o.get(k) for k in ("symbol", "side", "qty", "price", "product", "bracket")}, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# broker payloads
# --------------------------------------------------------------------------

def payload_angelone(o):
    br = o.get("bracket") or {}
    px = float(o["price"])
    p = {
        "variety": "ROBO" if br.get("stop_trigger") and o.get("product", "MIS") == "MIS" else "NORMAL",
        "tradingsymbol": "%s-EQ" % o["symbol"],
        "symboltoken": "<lookup in OpenAPIScripMaster.json>",
        "transactiontype": o.get("side", "BUY"),
        "exchange": o.get("exchange", "NSE"),
        "ordertype": o.get("order_type", "LIMIT"),
        "producttype": "INTRADAY" if o.get("product", "MIS") == "MIS" else "DELIVERY",
        "duration": "DAY",
        "price": "%.2f" % px,
        "quantity": str(o["qty"]),
    }
    if p["variety"] == "ROBO":
        p["squareoff"] = "%.2f" % (float(br["target"]) - px)
        p["stoploss"] = "%.2f" % (px - float(br["stop_trigger"]))
        p["trailingStopLoss"] = "0"
    return p


def payload_upstox(o):
    br = o.get("bracket") or {}
    entry = {
        "quantity": int(o["qty"]), "product": "I" if o.get("product", "MIS") == "MIS" else "D",
        "validity": "DAY", "price": float(o["price"]), "tag": o.get("tag", "nse-skill"),
        "instrument_token": "NSE_EQ|<ISIN of %s>" % o["symbol"], "order_type": o.get("order_type", "LIMIT"),
        "transaction_type": o.get("side", "BUY"), "disclosed_quantity": 0, "trigger_price": 0, "is_amo": False,
    }
    legs = {"entry": entry}
    if br.get("stop_trigger") and o.get("side", "BUY") == "BUY":
        legs["stop_loss"] = dict(entry, transaction_type="SELL", order_type="SL", price=float(br["stop_limit"]),
                                 trigger_price=float(br["stop_trigger"]))
        legs["target"] = dict(entry, transaction_type="SELL", order_type="LIMIT", price=float(br["target"]), trigger_price=0)
    return legs


def payload_groww(o):
    br = o.get("bracket") or {}
    entry = {
        "trading_symbol": o["symbol"], "quantity": int(o["qty"]), "price": float(o["price"]),
        "validity": "DAY", "exchange": o.get("exchange", "NSE"), "segment": "CASH",
        "product": "MIS" if o.get("product", "MIS") == "MIS" else "CNC",
        "order_type": o.get("order_type", "LIMIT"), "transaction_type": o.get("side", "BUY"),
        "order_reference_id": "nse-skill-%s" % o.get("order_id", "")[:8],
    }
    legs = {"entry": entry}
    if br.get("stop_trigger") and o.get("side", "BUY") == "BUY":
        legs["stop_loss"] = dict(entry, transaction_type="SELL", order_type="SL", price=float(br["stop_limit"]),
                                 trigger_price=float(br["stop_trigger"]), order_reference_id=entry["order_reference_id"] + "-sl")
        legs["target"] = dict(entry, transaction_type="SELL", order_type="LIMIT", price=float(br["target"]),
                              order_reference_id=entry["order_reference_id"] + "-tg")
    return legs


PAYLOADS = {"angelone": payload_angelone, "upstox": payload_upstox, "groww": payload_groww}


def show_ticket(o, cfg):
    br = o.get("bracket") or {}
    live = not cfg.get("paper", True)
    print("%s %s %d x %s at a %s limit  [%s, %s]  risk %s" % (
        "LIVE" if live else "PAPER", o.get("side", "BUY"), int(o["qty"]), o["symbol"], nc.inr(float(o["price"])),
        o.get("product", "MIS"), o.get("exchange", "NSE"), o["risk"]))
    if br.get("stop_trigger"):
        print("  stop-loss %s (SL limit %s)   target %s   max loss at the stop %s   gain at the target %s" % (
            nc.inr(br["stop_trigger"]), nc.inr(br.get("stop_limit")), nc.inr(br["target"]),
            nc.inr(o.get("loss_at_stop_inr")), nc.inr(o.get("gain_at_target_inr"))))
    if o.get("flat_by"):
        print("  flat by %s IST (broker MIS square-off ~15:15)" % o["flat_by"])
    for r in o.get("risk_reasons") or []:
        print("  BLOCK: " + r)
    broker = cfg.get("broker", "paper")
    if live and broker in ("upstox", "groww") and br.get("stop_trigger"):
        print("  NOTE: on %s the stop-loss and target legs are placed AFTER the entry fills (no native bracket); until then the position has no stop." % broker)
    if broker in PAYLOADS:
        print("  %s payload:" % broker)
        print("  " + json.dumps(PAYLOADS[broker](o), indent=2).replace("\n", "\n  "))
    print("  order_id %s" % o["order_id"])


# --------------------------------------------------------------------------
# live adapters (credentials from the environment only)
# --------------------------------------------------------------------------

def _post(url, body, headers):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=dict({"Content-Type": "application/json"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise nc.DataError("broker HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))
    except (urllib.error.URLError, OSError) as e:
        raise nc.DataError("broker unreachable: %s" % e)


def totp(secret, digits=6, period=30):
    import base64
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(time.time()) // period)
    h = hmac.new(key, counter, hashlib.sha1).digest()
    off = h[-1] & 0x0F
    code = (struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def live_angelone(o):
    need = ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PIN", "ANGEL_TOTP_SECRET")
    miss = [k for k in need if not os.environ.get(k)]
    if miss:
        raise nc.DataError("Angel One: set %s in the environment (never in chat)" % ", ".join(miss))
    base = "https://apiconnect.angelone.in"
    hdr = {"Accept": "application/json", "X-UserType": "USER", "X-SourceID": "WEB", "X-ClientLocalIP": "127.0.0.1",
           "X-ClientPublicIP": "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00", "X-PrivateKey": os.environ["ANGEL_API_KEY"]}
    login = _post(base + "/rest/auth/angelbroking/user/v1/loginByPassword",
                  {"clientcode": os.environ["ANGEL_CLIENT_ID"], "password": os.environ["ANGEL_PIN"],
                   "totp": totp(os.environ["ANGEL_TOTP_SECRET"])}, hdr)
    if not login.get("status"):
        raise nc.DataError("Angel One login failed: %s" % login.get("message"))
    jwt = login["data"]["jwtToken"]
    # symbol token from the scrip master
    cache = nc.trader_dir() / "angel_scrip_master.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86400:
        master = json.loads(cache.read_text(encoding="utf-8"))
    else:
        text = nc.http_get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json", timeout=120)
        master = json.loads(text)
        cache.write_text(text, encoding="utf-8")
    tok = next((r["token"] for r in master if r.get("symbol") == "%s-EQ" % o["symbol"] and r.get("exch_seg") == "NSE"), None)
    if not tok:
        raise nc.DataError("Angel One: no symbol token for %s-EQ" % o["symbol"])
    body = payload_angelone(o)
    body["symboltoken"] = tok
    hdr["Authorization"] = "Bearer " + jwt
    res = _post(base + "/rest/secure/angelbroking/order/v1/placeOrder", body, hdr)
    if not res.get("status"):
        raise nc.DataError("Angel One rejected the order: %s" % res.get("message"))
    return {"broker": "angelone", "order_ids": [res["data"].get("orderid")], "raw": res}


def live_upstox(o):
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not tok:
        raise nc.DataError("Upstox: set UPSTOX_ACCESS_TOKEN in the environment (never in chat)")
    isin = os.environ.get("ISIN_%s" % o["symbol"]) or isin_lookup(o["symbol"])
    if not isin:
        raise nc.DataError("Upstox needs the ISIN for %s; set ISIN_%s in the environment" % (o["symbol"], o["symbol"]))
    legs = payload_upstox(o)
    hdr = {"Authorization": "Bearer " + tok, "Accept": "application/json"}
    ids = []
    for name in ("entry",):  # SL and target go after the fill (see SKILL.md)
        body = dict(legs[name], instrument_token="NSE_EQ|" + isin)
        res = _post("https://api.upstox.com/v2/order/place", body, hdr)
        if res.get("status") != "success":
            raise nc.DataError("Upstox rejected the %s leg: %s" % (name, res))
        ids.append(res["data"].get("order_id"))
    return {"broker": "upstox", "order_ids": ids, "pending_legs": {k: dict(v, instrument_token="NSE_EQ|" + isin) for k, v in legs.items() if k != "entry"}}


def isin_lookup(sym):
    try:
        text = nc.nse_archive("/content/equities/EQUITY_L.csv")
    except nc.DataError:
        return None
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0].upper() == sym.upper():
            for p in parts:
                if p.startswith("INE") and len(p) == 12:
                    return p
    return None


def live_groww(o):
    tok = os.environ.get("GROWW_ACCESS_TOKEN")
    if not tok:
        raise nc.DataError("Groww: set GROWW_ACCESS_TOKEN in the environment (never in chat)")
    legs = payload_groww(o)
    hdr = {"Authorization": "Bearer " + tok, "Accept": "application/json", "X-API-VERSION": "1.0"}
    res = _post("https://api.groww.in/v1/order/create", legs["entry"], hdr)
    if (res.get("status") or "").upper() not in ("SUCCESS", "OK") and not res.get("payload"):
        raise nc.DataError("Groww rejected the order: %s" % res)
    oid = (res.get("payload") or {}).get("groww_order_id") or res.get("order_id")
    return {"broker": "groww", "order_ids": [oid], "pending_legs": {k: v for k, v in legs.items() if k != "entry"}}


LIVE = {"angelone": live_angelone, "upstox": live_upstox, "groww": live_groww}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_ticket(a):
    cfg = nc.load_config(strict=True)
    orders = passed_orders(cfg, a)
    if not orders:
        print("No orders in trader/orders.json. Run /nse-budget size, then /nse-risk.")
        return 1
    for o in orders:
        show_ticket(o, cfg)
    ok = [o for o in orders if o["risk"] == "PASS"]
    if not ok:
        print("Nothing can be sent: no fresh PASS from /nse-risk (run it, within %d minutes of sending)." % PASS_TTL_MIN)
        return 1
    print("To send: say yes in the chat; Claude runs `place --yes \"<your words>\"`. Nothing is sent before that.")
    return 0


def cmd_place(a):
    cfg = nc.load_config(strict=True)
    if not a.yes or not a.yes.strip():
        print("Refused: --yes must carry the user's typed yes.")
        return 1
    orders = [o for o in passed_orders(cfg, a) if o["risk"] == "PASS"]
    if a.order_id:
        orders = [o for o in orders if o["order_id"] == a.order_id]
    if not orders:
        print("Refused: no order with a fresh risk PASS. Run /nse-risk first.")
        return 1
    live = not cfg.get("paper", True)
    broker = cfg.get("broker", "paper")
    if live and broker not in LIVE:
        print("Refused: config says live but broker is %r; set broker to angelone, upstox or groww, or paper to true." % broker)
        return 1
    log = load_log()
    hold = load_holdings(cfg)
    t = nc.now_ist()
    sent = 0
    for o in orders:
        side = (o.get("side") or "BUY").upper()
        rec = {"order_id": o["order_id"], "date": t.date().isoformat(), "time": t.strftime("%H:%M:%S"),
               "symbol": o["symbol"], "side": side, "qty": int(o["qty"]), "price": float(o["price"]),
               "product": o.get("product", "MIS"), "bracket": o.get("bracket"), "loss_at_stop_inr": o.get("loss_at_stop_inr"),
               "paper": not live, "broker": broker if live else "paper", "yes": a.yes, "status": "placed",
               "flat_by": o.get("flat_by"), "horizon": o.get("horizon")}
        pos = next((p for p in hold.get("positions", []) if p["symbol"].upper() == o["symbol"].upper()), None)
        if side == "SELL":
            if not pos:
                print("%s: NOT sent - no position held to close (short sales are not supported here)." % o["symbol"])
                continue
            rec["closes"] = pos.get("order_id")
            rec["qty"] = min(int(o["qty"]), int(pos["qty"]))
            if live:
                try:
                    res = LIVE[broker](o)
                except nc.DataError as e:
                    print("%s: NOT sent - %s" % (o["symbol"], e))
                    rec.update({"status": "rejected", "error": str(e)})
                    log["trades"].append(rec)
                    continue
                rec["broker_order_ids"] = res.get("order_ids")
                print("%s: SELL sent to %s, order id(s) %s. Record the fill with `fill %s --price P`." % (
                    o["symbol"], broker, res.get("order_ids"), o["order_id"]))
                log["trades"].append(rec)
            else:
                save_log(log)
                nc.write_json(holdings_path(cfg), hold)
                pnl = nc.paper_close(cfg, o["symbol"], float(o["price"]), "close order", rec["qty"])
                log = load_log()
                hold = load_holdings(cfg)
                print("%s: PAPER closed %d shares at %s, P&L %s." % (o["symbol"], rec["qty"], nc.inr(float(o["price"])), nc.inr(pnl)))
            sent += 1
            continue
        if pos and not live:
            print("%s: NOT filled - a paper position is already open in it (close it first)." % o["symbol"])
            continue
        if live:
            try:
                res = LIVE[broker](o)
            except nc.DataError as e:
                print("%s: NOT sent - %s" % (o["symbol"], e))
                rec["status"] = "rejected"
                rec["error"] = str(e)
                log["trades"].append(rec)
                continue
            rec["broker_order_ids"] = res.get("order_ids")
            rec["pending_legs"] = res.get("pending_legs")
            print("%s: sent to %s, order id(s) %s. Record the fill with `fill %s --price P` when the broker reports it." % (
                o["symbol"], broker, res.get("order_ids"), o["order_id"]))
            if rec.get("pending_legs"):
                print("  WARNING: the stop-loss and target legs are NOT placed yet on %s; place them right after the fill (see SKILL.md). Until then there is no stop." % broker)
        else:
            rec["status"] = "open"
            rec["fill_price"] = float(o["price"])
            rec["fill_time"] = t.strftime("%H:%M:%S")
            hold["positions"] = [p for p in hold.get("positions", []) if p["symbol"] != o["symbol"]] + [{
                "symbol": o["symbol"], "qty": int(o["qty"]), "avg_price": float(o["price"]), "product": o.get("product", "MIS"),
                "stop": (o.get("bracket") or {}).get("stop_trigger"), "target": (o.get("bracket") or {}).get("target"),
                "entered": t.strftime("%Y-%m-%d %H:%M"), "paper": True, "order_id": o["order_id"], "horizon": o.get("horizon")}]
            print("%s: PAPER fill, %d shares at %s. Stop %s, target %s. Logged. (Paper stops and targets are booked by `/nse-picks exits` or `/nse-alerts watch`.)" % (
                o["symbol"], int(o["qty"]), nc.inr(float(o["price"])), nc.inr((o.get("bracket") or {}).get("stop_trigger")),
                nc.inr((o.get("bracket") or {}).get("target"))))
        log["trades"].append(rec)
        sent += 1
    save_log(log)
    nc.write_json(holdings_path(cfg), hold)
    # the orders file is consumed
    od = nc.trader_dir() / "orders.json"
    if od.exists() and sent:
        nc.write_json(od, {"as_of": t.strftime("%Y-%m-%d %H:%M IST"), "orders": [], "consumed": [o["order_id"] for o in orders]})
    if sent and cfg.get("notify", True):
        nc.notify("nse-broker", "%d order(s) %s" % (sent, "sent" if live else "paper-filled"))
    return 0 if sent else 1


def cmd_close(a):
    """Write the SELL that flattens a position to trader/orders.json. It is sent only by `place --yes` after /nse-risk."""
    cfg = nc.load_config(strict=True)
    hold = load_holdings(cfg)
    sym = a.symbol.upper()
    pos = next((p for p in hold.get("positions", []) if p["symbol"].upper() == sym), None)
    if not pos:
        print("No open position in %s." % sym)
        return 1
    last = None
    try:
        last = nc.yahoo_quote_fast(sym)["last"]
    except nc.DataError:
        pass
    if a.price:
        price = float(a.price)
    elif last:
        price = nc.tick_round(float(last) * 0.998, down=True)
    elif pos.get("avg_price"):
        price = nc.tick_round(float(pos["avg_price"]), down=True)
        print("No live quote; using the average price as the limit. Pass --price to set it.")
    else:
        print("No live quote and no avg_price on the position; pass --price.")
        return 1
    qty = min(int(a.qty or pos["qty"]), int(pos["qty"]))
    order = {"symbol": sym, "exchange": cfg.get("exchange", "NSE"), "side": "SELL", "product": pos.get("product", "MIS"),
             "qty": qty, "order_type": "LIMIT", "price": price, "closes_position": True, "bracket": {},
             "tag": "nse-skill-close", "horizon": pos.get("horizon")}
    order["order_id"] = order_id(order)
    t = nc.now_ist()
    avg = float(pos.get("avg_price") or price)
    print("CLOSE %s: SELL %d x %s at a %s limit (last %s, avg %s, P&L about %s)" % (
        sym, qty, sym, nc.inr(price), nc.inr(last), nc.inr(avg), nc.inr((price - avg) * qty)))
    nc.write_json(nc.trader_dir() / "orders.json", {"as_of": t.strftime("%Y-%m-%d %H:%M IST"), "orders": [order]})
    print("Written to trader/orders.json. Next: /nse-risk (a close is never blocked by the day limit), then say yes and `place --yes`.")
    return 0


def cmd_fill(a):
    cfg = nc.load_config(strict=True)
    log = load_log()
    hold = load_holdings(cfg)
    rec = next((r for r in log["trades"] if r.get("order_id") == a.order_id), None)
    if not rec:
        print("No order %s in trader/trades_log.json." % a.order_id)
        return 1
    qty = int(a.qty or rec["qty"])
    t = nc.now_ist()
    rec.update({"fill_price": a.price, "fill_qty": qty, "fill_time": t.strftime("%H:%M:%S")})
    if rec["side"] == "BUY":
        rec["status"] = "open"
        br = rec.get("bracket") or {}
        hold["positions"] = [p for p in hold.get("positions", []) if p["symbol"] != rec["symbol"]] + [{
            "symbol": rec["symbol"], "qty": qty, "avg_price": a.price, "product": rec.get("product", "MIS"),
            "stop": br.get("stop_trigger"), "target": br.get("target"), "entered": t.strftime("%Y-%m-%d %H:%M"),
            "paper": rec.get("paper", True), "order_id": rec["order_id"], "horizon": rec.get("horizon")}]
        print("Recorded the BUY fill: %d x %s at %s. Position is open." % (qty, rec["symbol"], nc.inr(a.price)))
    else:
        pos = next((p for p in hold.get("positions", []) if p["symbol"] == rec["symbol"]), None)
        pnl = (a.price - float(pos["avg_price"])) * qty if pos else None
        rec.update({"status": "closed", "pnl": round(pnl, 2) if pnl is not None else None})
        for r in log["trades"]:
            if r.get("order_id") == rec.get("closes") and r.get("status") == "open":
                r.update({"status": "closed", "pnl": round(pnl, 2) if pnl is not None else None, "exit_price": a.price})
        hold["positions"] = [p for p in hold.get("positions", []) if p["symbol"] != rec["symbol"]]
        print("Recorded the SELL fill: %d x %s at %s. P&L %s." % (qty, rec["symbol"], nc.inr(a.price), nc.inr(pnl)))
    save_log(log)
    nc.write_json(holdings_path(cfg), hold)
    return 0


def cmd_status(a):
    cfg = nc.load_config()
    hold = load_holdings(cfg)
    log = load_log()
    today = nc.now_ist().date().isoformat()
    rows = [r for r in log["trades"] if r.get("date") == today]
    pnl = sum(float(r.get("pnl") or 0) for r in rows if r.get("status") == "closed" and r.get("side") == "SELL")
    print("Positions (%s):" % ("paper" if cfg.get("paper", True) else cfg.get("broker")))
    if not hold.get("positions"):
        print("  none")
    for p in hold.get("positions", []):
        print("  %-12s %5d @ %-10s stop %-9s target %-9s %s %s" % (
            p["symbol"], p["qty"], nc.inr(p["avg_price"]), nc.inr(p.get("stop")), nc.inr(p.get("target")), p.get("product", ""), p.get("entered", "")))
    print("Today: %d order(s), %d trade(s), realised P&L %s." % (len(rows), sum(1 for r in rows if r.get("side") == "BUY"), nc.inr(pnl)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ticket")
    p = sub.add_parser("place")
    p.add_argument("--yes", required=True, help="the user's typed yes, word for word")
    p.add_argument("--order-id")
    c = sub.add_parser("close")
    c.add_argument("symbol")
    c.add_argument("--qty", type=int)
    c.add_argument("--price", type=float)
    f = sub.add_parser("fill")
    f.add_argument("order_id")
    f.add_argument("--price", type=float, required=True)
    f.add_argument("--qty", type=int)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"ticket": cmd_ticket, "place": cmd_place, "close": cmd_close, "fill": cmd_fill, "status": cmd_status}[a.cmd](a)
    except nc.ConfigError as e:
        print("Broker could not run: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
