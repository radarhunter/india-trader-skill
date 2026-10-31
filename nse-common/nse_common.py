"""nse_common - the shared library for the India Trader skill pack.

Every nse-* tool imports this. It knows:

* Indian Standard Time and the NSE trading calendar (pre-open 09:00-09:08,
  continuous 09:15-15:30, closing 15:40-16:00, the exchange holidays).
* Yahoo Finance chart JSON for NSE (.NS) and BSE (.BO) symbols. Free,
  unofficial, delayed; it can lag or rate-limit. query2 first, query1 next.
* The NSE website API (needs a cookie handshake and browser headers) and the
  BSE India API (needs a Referer). Both are unofficial and change without
  notice; every caller degrades gracefully when they fail.
* trader/config.json (the user's rules, in rupees) and the trader/ folder
  where every skill writes what the next one reads.
* A macOS / Linux desktop notification plus a terminal bell for alerts.

Nothing here places an order. Nothing here is advice.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import io
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata") if ZoneInfo else _dt.timezone(_dt.timedelta(hours=5, minutes=30), "IST")

PRE_OPEN_START = _dt.time(9, 0)
PRE_OPEN_END = _dt.time(9, 8)
OPEN = _dt.time(9, 15)
CLOSE = _dt.time(15, 30)
CLOSING_SESSION_START = _dt.time(15, 40)
CLOSING_SESSION_END = _dt.time(16, 0)
# Most brokers auto-square-off MIS (intraday) positions between 15:15 and 15:20.
BROKER_SQUARE_OFF = _dt.time(15, 15)

# NSE trading holidays. Source: NSE "Trading Holidays" circular. Verified
# against https://www.nseindia.com/api/holiday-master?type=trading when
# the pack was built; `nse-alerts holidays --refresh` re-fetches it.
# Muhurat trading (Diwali evening) is a special one-hour session, not a holiday.
HOLIDAYS = {
    # 2026: NSE holiday-master (type=trading, segment CM) as fetched on 31 Oct 2026.
    2026: {
        "2026-01-15": "Municipal Corporation Election - Maharashtra",
        "2026-01-26": "Republic Day",
        "2026-02-15": "Mahashivratri",
        "2026-03-03": "Holi",
        "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
        "2026-03-26": "Shri Ram Navami",
        "2026-03-31": "Shri Mahavir Jayanti",
        "2026-04-03": "Good Friday",
        "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
        "2026-05-01": "Maharashtra Day",
        "2026-05-28": "Bakri Id",
        "2026-06-26": "Muharram",
        "2026-08-15": "Independence Day",
        "2026-09-14": "Ganesh Chaturthi",
        "2026-10-02": "Mahatma Gandhi Jayanti",
        "2026-10-20": "Dussehra",
        "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading in the evening)",
        "2026-11-10": "Diwali-Balipratipada",
        "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
        "2026-12-25": "Christmas",
    },
    # 2027: PROVISIONAL (computed from the usual calendar; NSE publishes the list in December).
    # `nse-alerts holidays --refresh` replaces this with NSE's own list.
    2027: {
        "2027-01-26": "Republic Day",
        "2027-03-05": "Mahashivratri",
        "2027-03-22": "Holi",
        "2027-03-26": "Good Friday",
        "2027-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
        "2027-04-15": "Shri Ram Navami",
        "2027-04-20": "Shri Mahavir Jayanti",
        "2027-05-01": "Maharashtra Day",
        "2027-05-17": "Bakri Id",
        "2027-06-16": "Muharram",
        "2027-08-15": "Independence Day",
        "2027-09-03": "Ganesh Chaturthi",
        "2027-10-02": "Mahatma Gandhi Jayanti",
        "2027-10-09": "Dussehra",
        "2027-10-28": "Diwali-Laxmi Pujan",
        "2027-10-29": "Diwali-Balipratipada",
        "2027-11-13": "Prakash Gurpurb Sri Guru Nanak Dev",
        "2027-12-25": "Christmas",
    },
}


def holidays_file():
    return trader_dir() / "nse_holidays.json"


def load_holidays():
    """Bundled list, overridden by trader/nse_holidays.json when nse-alerts refreshed it."""
    out = {}
    for yr, days in HOLIDAYS.items():
        out.update(days)
    f = holidays_file()
    if f.exists():
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            for k, v in (doc.get("holidays") or {}).items():
                out[k] = v
        except (OSError, ValueError):
            pass
    return out


def now_ist(pin=None):
    """Current IST, or a pinned "YYYY-MM-DD HH:MM" string (IST) for tests and replays."""
    if pin:
        if isinstance(pin, _dt.datetime):
            return pin if pin.tzinfo else pin.replace(tzinfo=IST)
        s = str(pin).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                return _dt.datetime.strptime(s, fmt).replace(tzinfo=IST)
            except ValueError:
                continue
        raise ValueError("cannot read the time %r (use YYYY-MM-DD HH:MM, IST)" % pin)
    return _dt.datetime.now(IST)


def is_trading_day(d, holidays=None):
    if isinstance(d, _dt.datetime):
        d = d.date()
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in (holidays or load_holidays())


def next_trading_day(d, holidays=None):
    if isinstance(d, _dt.datetime):
        d = d.date()
    hol = holidays or load_holidays()
    n = d + _dt.timedelta(days=1)
    while not is_trading_day(n, hol):
        n += _dt.timedelta(days=1)
    return n


def prev_trading_day(d, holidays=None):
    if isinstance(d, _dt.datetime):
        d = d.date()
    hol = holidays or load_holidays()
    n = d - _dt.timedelta(days=1)
    while not is_trading_day(n, hol):
        n -= _dt.timedelta(days=1)
    return n


def market_status(t=None):
    """A dict: phase (closed-holiday, closed-weekend, pre-market, pre-open, open,
    post-close, closing-session, after-hours), plus the next open in IST."""
    t = t or now_ist()
    hol = load_holidays()
    d = t.date()
    tod = t.time()
    phase = None
    if d.isoformat() in hol:
        phase = "closed-holiday (%s)" % hol[d.isoformat()]
    elif d.weekday() >= 5:
        phase = "closed-weekend"
    elif tod < PRE_OPEN_START:
        phase = "pre-market"
    elif tod < PRE_OPEN_END:
        phase = "pre-open"
    elif tod < OPEN:
        phase = "pre-open-buffer"
    elif tod < CLOSE:
        phase = "open"
    elif tod < CLOSING_SESSION_START:
        phase = "post-close"
    elif tod < CLOSING_SESSION_END:
        phase = "closing-session"
    else:
        phase = "after-hours"
    if phase == "open":
        next_open = _dt.datetime.combine(d, OPEN, IST)
    elif is_trading_day(d, hol) and tod < OPEN:
        next_open = _dt.datetime.combine(d, OPEN, IST)
    else:
        next_open = _dt.datetime.combine(next_trading_day(d, hol), OPEN, IST)
    close_today = _dt.datetime.combine(d, CLOSE, IST)
    return {
        "now_ist": t.strftime("%Y-%m-%d %H:%M:%S IST"),
        "phase": phase,
        "is_open": phase == "open",
        "next_open_ist": next_open.strftime("%Y-%m-%d %H:%M IST"),
        "minutes_to_open": int((next_open - t).total_seconds() // 60),
        "minutes_to_close": int((close_today - t).total_seconds() // 60) if phase == "open" else None,
        "close_ist": "15:30",
        "broker_square_off_ist": BROKER_SQUARE_OFF.strftime("%H:%M"),
    }


def parse_hhmm(s, default=None):
    if s is None or s == "":
        return default
    if isinstance(s, _dt.time):
        return s
    h, m = str(s).split(":")[:2]
    return _dt.time(int(h), int(m))


def fmt_time_ist(ts):
    """epoch seconds -> 'HH:MM IST'."""
    return _dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def fmt_dt_ist(ts):
    return _dt.datetime.fromtimestamp(int(ts), IST).strftime("%Y-%m-%d %H:%M IST")


# --------------------------------------------------------------------------
# folders and config
# --------------------------------------------------------------------------

def trader_dir():
    """trader/ under the project (cwd), or $TRADER_DIR."""
    p = Path(os.environ.get("TRADER_DIR") or "trader")
    return p


def ensure_trader_dir():
    d = trader_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULT_CONFIG = {
    "budget_inr": 100000,
    "cash_buffer_pct": 10,
    "max_position_pct": 40,
    "max_order_inr": 40000,
    "min_order_inr": 500,
    "max_loss_per_trade_inr": 1000,
    "max_daily_loss_inr": 3000,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "flat_by": "15:10",
    "product": "MIS",
    "exchange": "NSE",
    "broker": "paper",
    "paper": True,
    "blocked": [],
    "allow_fno": False,
    "allow_margin": False,
    "allow_short": False,
    "allow_bse": False,
    "avoid_asm_gsm": True,
    "swing_max_hold_days": 5,
    "swing_stop_pct": 3.0,
    "watch_poll_seconds": 30,
    "notify": True,
    "holdings_file": "trader/holdings.json",
    "user_agent": "india-trader-skill/1.0 (you@example.com)",
}


def config_path():
    return trader_dir() / "config.json"


def load_config(path=None, strict=False):
    """trader/config.json merged over the defaults. strict=True raises when it is missing."""
    p = Path(path) if path else config_path()
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ConfigError("%s is not valid JSON: %s" % (p, e))
        if not isinstance(user, dict):
            raise ConfigError("%s must hold one JSON object" % p)
        cfg.update(user)
        cfg["_path"] = str(p)
        cfg["_found"] = True
    else:
        if strict:
            raise ConfigError("no %s - run /nse-budget set my rules first" % p)
        cfg["_path"] = str(p)
        cfg["_found"] = False
    return cfg


class ConfigError(Exception):
    pass


class DataError(Exception):
    pass


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, doc):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def inr(x, decimals=2):
    """Indian grouping: 12,34,567.89"""
    if x is None:
        return "-"
    neg = x < 0
    x = abs(float(x))
    whole = int(x)
    frac = x - whole
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    if decimals:
        s += ("%%.%df" % decimals % frac)[1:]
    return ("-" if neg else "") + "₹" + s


def pct(x, d=2):
    return "-" if x is None else ("%%+.%df%%%%" % d) % x


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def http_get(url, headers=None, timeout=20, retries=2, pause=1.0, binary=False):
    """GET with retries on 429/5xx. Raises DataError with a plain reason."""
    hdr = {"User-Agent": BROWSER_UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
    hdr.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding", "") == "gzip" or raw[:2] == b"\x1f\x8b":
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except OSError:
                        pass
                return raw if binary else raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = "HTTP %s from %s" % (e.code, urllib.parse.urlsplit(url).netloc)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(pause * (attempt + 1))
                continue
            raise DataError(last)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = "%s unreachable (%s)" % (urllib.parse.urlsplit(url).netloc, getattr(e, "reason", e))
            if attempt < retries:
                time.sleep(pause * (attempt + 1))
                continue
            raise DataError(last)
    raise DataError(last or "request failed")


# --------------------------------------------------------------------------
# Yahoo Finance (NSE .NS, BSE .BO)
# --------------------------------------------------------------------------

YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")


def yahoo_symbol(sym, exchange="NSE"):
    """RELIANCE -> RELIANCE.NS; RELIANCE.BO stays; ^NSEI stays."""
    s = sym.strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    s = s.replace("&", "%26") if False else s
    return s + (".BO" if exchange.upper() == "BSE" else ".NS")


def plain_symbol(ysym):
    s = ysym.upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def yahoo_get(path, timeout=20):
    err = None
    for host in YAHOO_HOSTS:
        try:
            return http_get(host + path, headers={"Accept": "application/json"}, timeout=timeout, retries=1)
        except DataError as e:
            err = e
            continue
    raise DataError("Yahoo: %s" % err)


def yahoo_chart(sym, rng="3mo", interval="1d", exchange="NSE", prepost=False):
    """One symbol's bars -> dict(symbol, ysymbol, ts[], open[], high[], low[], close[], volume[], meta)."""
    ys = yahoo_symbol(sym, exchange)
    path = "/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=%s&events=div%%2Csplit" % (
        urllib.parse.quote(ys), rng, interval, "true" if prepost else "false")
    text = yahoo_get(path)
    try:
        doc = json.loads(text)
    except ValueError:
        raise DataError("Yahoo sent something that is not JSON for %s" % ys)
    return parse_chart(doc, ys)


def parse_chart(doc, ys="?"):
    chart = (doc or {}).get("chart") or {}
    if chart.get("error"):
        e = chart["error"]
        raise DataError("Yahoo %s: %s" % (ys, e.get("description") or e.get("code") or e))
    res = (chart.get("result") or [None])[0]
    if not res:
        raise DataError("Yahoo has no data for %s" % ys)
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    out = {
        "symbol": plain_symbol(meta.get("symbol") or ys),
        "ysymbol": meta.get("symbol") or ys,
        "ts": ts,
        "open": q.get("open") or [],
        "high": q.get("high") or [],
        "low": q.get("low") or [],
        "close": q.get("close") or [],
        "volume": q.get("volume") or [],
        "meta": {
            "currency": meta.get("currency"),
            "exchange": meta.get("exchangeName"),
            "regularMarketPrice": meta.get("regularMarketPrice"),
            "previousClose": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "regularMarketTime": meta.get("regularMarketTime"),
            "longName": meta.get("longName") or meta.get("shortName"),
            "interval": meta.get("dataGranularity"),
        },
    }
    return out


def clean_bars(ch):
    """Drop bars with a None close. Returns list of (ts, o, h, l, c, v)."""
    bars = []
    for i, t in enumerate(ch.get("ts") or []):
        c = _at(ch["close"], i)
        if c is None:
            continue
        o = _at(ch["open"], i)
        h = _at(ch["high"], i)
        lo = _at(ch["low"], i)
        v = _at(ch["volume"], i) or 0
        bars.append((int(t), o if o is not None else c, h if h is not None else c,
                     lo if lo is not None else c, c, v))
    return bars


def _at(lst, i):
    try:
        return lst[i]
    except (IndexError, TypeError):
        return None


def bars_by_day(bars):
    """Group intraday bars by IST date -> {date: [bars]}"""
    days = {}
    for b in bars:
        d = _dt.datetime.fromtimestamp(b[0], IST).date().isoformat()
        days.setdefault(d, []).append(b)
    return days


def session_bars(bars, day=None):
    """Only bars inside 09:15-15:30 IST on `day` (or every day)."""
    out = []
    for b in bars:
        t = _dt.datetime.fromtimestamp(b[0], IST)
        if day and t.date().isoformat() != day:
            continue
        if OPEN <= t.time() < CLOSE:
            out.append(b)
    return out


def yahoo_quote_fast(sym, exchange="NSE"):
    """Last price, previous close and day's bars from the 1-day/1-minute chart."""
    ch = yahoo_chart(sym, "1d", "1m", exchange)
    bars = clean_bars(ch)
    last = ch["meta"].get("regularMarketPrice")
    if last is None and bars:
        last = bars[-1][4]
    return {
        "symbol": ch["symbol"],
        "last": last,
        "prev_close": ch["meta"].get("previousClose"),
        "time": ch["meta"].get("regularMarketTime"),
        "bars": bars,
        "name": ch["meta"].get("longName"),
    }


# --------------------------------------------------------------------------
# NSE website API (cookie handshake)
# --------------------------------------------------------------------------

NSE_BASE = "https://www.nseindia.com"
NSE_ARCHIVES = "https://nsearchives.nseindia.com"


class NSESession:
    """nseindia.com refuses bare requests; a visit to the home page sets the
    cookies its /api/ endpoints want. One session per tool run."""

    def __init__(self, timeout=20):
        self.timeout = timeout
        self.jar = None
        self.opener = None
        self._ready = False

    def _prepare(self):
        import http.cookiejar
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [
            ("User-Agent", BROWSER_UA),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            ("Accept-Language", "en-US,en;q=0.9"),
            ("Accept-Encoding", "gzip"),
            ("Referer", NSE_BASE + "/"),
            ("Connection", "keep-alive"),
        ]
        for warm in ("/", "/market-data/live-equity-market"):
            try:
                with self.opener.open(NSE_BASE + warm, timeout=self.timeout) as r:
                    r.read(1024)
                break
            except Exception:  # noqa: BLE001
                continue
        self._ready = True

    def get(self, path, retries=2):
        if not self._ready:
            self._prepare()
        url = NSE_BASE + path if path.startswith("/") else path
        last = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, headers={"Accept": "application/json, text/plain, */*",
                                                        "X-Requested-With": "XMLHttpRequest"})
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding", "") == "gzip" or raw[:2] == b"\x1f\x8b":
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    return raw.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last = "NSE HTTP %s on %s" % (e.code, path.split("?")[0])
                if e.code in (401, 403, 429, 503) and attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    self._prepare()
                    continue
                raise DataError(last)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = "NSE unreachable (%s)" % getattr(e, "reason", e)
                if attempt < retries:
                    time.sleep(1.5)
                    continue
                raise DataError(last)
        raise DataError(last or "NSE request failed")

    def json(self, path):
        text = self.get(path)
        try:
            return json.loads(text)
        except ValueError:
            raise DataError("NSE answered %s with something that is not JSON (blocked?)" % path.split("?")[0])


def nse_archive(path, binary=False):
    """nsearchives.nseindia.com CSVs (no cookies needed, but wants a browser UA)."""
    return http_get(NSE_ARCHIVES + path, headers={"Referer": NSE_BASE + "/"}, binary=binary)


NSE_QUOTE_API = "/api/NextApi/apiClient/GetQuoteApi?functionName=%s"
NSE_MW_API = "/api/NextApi/apiClient/marketWatchApi?functionName=%s"


def nse_index_constituents(sess, index_name):
    """Members of an NSE index (or 'SECURITIES IN F&O') with live OHLC from the market-watch API.
    -> list of dicts: symbol, name, last, open, high, low, prev_close, volume, turnover, year_high, year_low."""
    doc = sess.json(NSE_MW_API % "getIndicesData" + "&symbol=" + urllib.parse.quote(index_name))
    rows = ((doc.get("data") or {}).get("data")) if isinstance(doc, dict) else None
    if not rows:
        raise DataError("NSE market-watch returned nothing for %s" % index_name)
    out = []
    for r in rows:
        if r.get("series") != "EQ" or not r.get("symbol"):
            continue
        out.append({"symbol": r["symbol"].upper(), "name": r.get("companyName") or r["symbol"],
                    "last": r.get("lastPrice"), "open": r.get("open"), "high": r.get("dayHigh"), "low": r.get("dayLow"),
                    "prev_close": r.get("previousClose"), "volume": r.get("totalTradedVolume"),
                    "turnover": r.get("totalTradedValue"), "year_high": r.get("yearHigh"), "year_low": r.get("yearLow"),
                    "pchange": r.get("pChange"), "updated": r.get("lastUpdateTime")})
    return out


def nse_quote(sess, sym):
    """Live NSE quote via GetQuoteApi getSymbolData (+ getMetaData for the F&O flag).
    -> last, prev_close, open, high, low, upper_cp, lower_cp, band_pct, tick, fno, margin_pct, industry, indices, updated."""
    doc = sess.json(NSE_QUOTE_API % "getSymbolData" + "&marketType=N&series=EQ&symbol=" + urllib.parse.quote(sym))
    eq = (doc.get("equityResponse") or [None])[0] if isinstance(doc, dict) else None
    if not eq:
        raise DataError("NSE quote API returned nothing for %s" % sym)
    md, pi, ti, si = eq.get("metaData") or {}, eq.get("priceInfo") or {}, eq.get("tradeInfo") or {}, eq.get("secInfo") or {}
    lo = hi = None
    band = str(pi.get("priceBand") or "")
    if "-" in band:
        try:
            lo, hi = [float(x) for x in band.split("-")[:2]]
        except ValueError:
            lo = hi = None
    q = {"symbol": sym.upper(), "name": md.get("companyName"), "last": md.get("closePrice") or ti.get("lastPrice") or md.get("lastPrice"),
         "prev_close": md.get("previousClose"), "open": md.get("open"), "high": md.get("dayHigh"), "low": md.get("dayLow"),
         "upper_cp": hi, "lower_cp": lo, "band_pct": pi.get("ppriceBand"), "tick": pi.get("tickSize"),
         "volume": ti.get("totalTradedVolume"), "turnover": ti.get("totalTradedValue"),
         "margin_pct": _f(ti.get("applicableMargin") or si.get("applicableMargin")), "industry": si.get("industryInfo") or si.get("basicIndustry"),
         "indices": si.get("indexList") or [], "status": si.get("isSuspended"), "updated": eq.get("lastUpdateTime"),
         "year_high": pi.get("yearHigh"), "year_low": pi.get("yearLow"), "fno": None, "isin": md.get("isinCode")}
    try:
        meta = sess.json(NSE_QUOTE_API % "getMetaData" + "&symbol=" + urllib.parse.quote(sym))
        q["fno"] = str(meta.get("isFNOSec")).lower() == "true"
        q["suspended"] = str(meta.get("isSuspended")).lower() == "true"
    except DataError:
        pass
    return q


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def nse_surveillance(sess):
    """{symbol: 'ASM-LT Stage I' | 'ASM-ST Stage I' | 'GSM <stage>' | combined}. Best effort; empty dict on failure."""
    flags = {}
    try:
        asm = sess.json("/api/reportASM")
        for key, tag in (("longterm", "ASM-LT"), ("shortterm", "ASM-ST")):
            for r in ((asm.get(key) or {}).get("data") or []):
                s_ = (r.get("symbol") or "").upper()
                if s_:
                    flags[s_] = "%s %s" % (tag, r.get("asmSurvIndicator") or "")
    except (DataError, AttributeError):
        pass
    try:
        gsm = sess.json("/api/reportGSM")
        for r in (gsm if isinstance(gsm, list) else gsm.get("data") or []):
            s_ = (r.get("symbol") or "").upper()
            if s_:
                g = "GSM %s" % (r.get("gsmStage") or "")
                flags[s_] = (flags[s_] + "/" + g) if s_ in flags else g
    except (DataError, AttributeError):
        pass
    return {k: v.strip() for k, v in flags.items()}


# --------------------------------------------------------------------------
# BSE India API
# --------------------------------------------------------------------------

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"


def bse_json(path, params=None):
    url = BSE_API + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    text = http_get(url, headers={"Referer": "https://www.bseindia.com/",
                                  "Origin": "https://www.bseindia.com",
                                  "Accept": "application/json, text/plain, */*"})
    try:
        return json.loads(text)
    except ValueError:
        raise DataError("BSE answered with something that is not JSON")


# --------------------------------------------------------------------------
# indicators
# --------------------------------------------------------------------------

def sma(xs, n):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def ema(xs, n):
    if len(xs) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1 + rs)


def atr(bars, n=14):
    """bars: (ts,o,h,l,c,v). Wilder ATR."""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, lo, pc = bars[i][2], bars[i][3], bars[i - 1][4]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def tick_for(price):
    """NSE cash-segment tick size by price band (in force since 2024):
    <= 250: 0.01; 250-1000: 0.05; 1000-5000: 0.10; 5000-10000: 0.50; 10000-20000: 1.00; above: 5.00.
    When the live quote carries `tickSize`, callers pass that instead."""
    p = float(price)
    if p <= 250:
        return 0.01
    if p <= 1000:
        return 0.05
    if p <= 5000:
        return 0.10
    if p <= 10000:
        return 0.50
    if p <= 20000:
        return 1.00
    return 5.00


def tick_round(price, down=True, tick=None):
    """Round to the tick for this price band (or the given tick). down=True floors (stops), else ceils."""
    import math
    tick = tick or tick_for(price)
    q = price / tick
    q = math.floor(q + 1e-9) if down else math.ceil(q - 1e-9)
    return round(round(q * tick, 4), 2)


# --------------------------------------------------------------------------
# notifications
# --------------------------------------------------------------------------

def notify(title, message, sound=True):
    """Desktop notification (macOS: Notification Center; Linux: notify-send) plus a bell."""
    try:
        if platform.system() == "Darwin":
            script = 'display notification "%s" with title "%s"%s' % (
                message.replace('"', "'"), title.replace('"', "'"), ' sound name "Ping"' if sound else "")
            subprocess.run(["osascript", "-e", script], check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif platform.system() == "Linux":
            subprocess.run(["notify-send", title, message], check=False, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass
    if sound:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass


def say(msg, quiet=False):
    if not quiet:
        print(msg, flush=True)


def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr, flush=True)


def today_tag(t=None):
    return (t or now_ist()).strftime("%Y-%m-%d")
