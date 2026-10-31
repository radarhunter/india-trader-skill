"""Build synthetic Yahoo-shaped fixtures: 60 sessions of 5-minute bars and 3 months of daily bars for DEMO.NS."""
import json, random, datetime as dt, sys
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")
rnd = random.Random(7)

def session_days(n, end):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return sorted(days)

def make_5m(sym="DEMO.NS", n_days=60, end=None, orb_break_today=True):
    end = end or dt.date(2026, 11, 3)
    days = session_days(n_days, end)
    ts, o, h, l, c, v = [], [], [], [], [], []
    px = 1000.0
    for di, day in enumerate(days):
        t = dt.datetime.combine(day, dt.time(9, 15), IST)
        px *= 1 + rnd.gauss(0, 0.006)
        for k in range(75):
            drift = 0.0
            if di == len(days) - 1 and orb_break_today and 3 <= k <= 12:
                drift = 0.0012
            op = px
            cl = op * (1 + rnd.gauss(drift, 0.0018))
            hi = max(op, cl) * (1 + abs(rnd.gauss(0, 0.0008)))
            lo = min(op, cl) * (1 - abs(rnd.gauss(0, 0.0008)))
            ts.append(int(t.timestamp())); o.append(round(op, 2)); h.append(round(hi, 2)); l.append(round(lo, 2)); c.append(round(cl, 2))
            v.append(int(abs(rnd.gauss(50000, 15000))) + (120000 if drift else 0))
            px = cl
            t += dt.timedelta(minutes=5)
    return {"chart": {"result": [{"meta": {"symbol": sym, "currency": "INR", "exchangeName": "NSI", "regularMarketPrice": c[-1],
            "chartPreviousClose": c[-76], "regularMarketTime": ts[-1], "longName": "Demo Industries Ltd", "dataGranularity": "5m"},
            "timestamp": ts, "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}], "error": None}}

def make_daily(sym="DEMO.NS", n_days=65, end=None):
    end = end or dt.date(2026, 11, 3)
    days = session_days(n_days, end)
    ts, o, h, l, c, v = [], [], [], [], [], []
    px = 900.0
    for i, day in enumerate(days):
        op = px
        cl = op * (1 + rnd.gauss(0.002 if i > 40 else 0, 0.012))
        hi = max(op, cl) * (1 + abs(rnd.gauss(0, 0.004))); lo = min(op, cl) * (1 - abs(rnd.gauss(0, 0.004)))
        ts.append(int(dt.datetime.combine(day, dt.time(9, 15), IST).timestamp())); o.append(round(op, 2)); h.append(round(hi, 2)); l.append(round(lo, 2)); c.append(round(cl, 2)); v.append(int(abs(rnd.gauss(2e6, 5e5))))
        px = cl
    return {"chart": {"result": [{"meta": {"symbol": sym, "currency": "INR", "regularMarketPrice": c[-1], "chartPreviousClose": c[-2], "longName": "Demo Industries Ltd", "dataGranularity": "1d"},
            "timestamp": ts, "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}], "error": None}}

if __name__ == "__main__":
    out = sys.argv[1]
    json.dump(make_5m(), open(out + "/DEMO-60d5m.json", "w"))
    json.dump(make_daily(), open(out + "/DEMO-3mo1d.json", "w"))
    print("wrote fixtures to", out)
