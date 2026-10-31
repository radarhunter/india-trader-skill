---
name: nse-insider
description: >-
  Insider Tracker for NSE - the Indian counterpart of a Congress-trades
  tracker. Reads the trades promoters, directors and key managerial
  personnel must disclose under SEBI's Prohibition of Insider Trading
  Regulations 7(2) as NSE posts them: who, their category, buy or sell,
  shares and value, market or off-market or pledge, the trade date and how
  many days it took to disclose; flags trades in your holdings and
  watchlist and alerts once per new filing. Also today's bulk deals (0.5%+
  of equity) and block deals, short-selling disclosures, and the daily FII
  / DII net flows in the cash market. Use when the user says "what are
  promoters buying", "insider trades", "promoter selling", "did any
  director sell", "bulk deals today", "block deals", "FII DII data", "who
  bought my stock", "promoter ne kya kiya", "alert me on insider filings",
  or "run the insider tracker".
argument-hint: "[insider | deals | flows | watch] [--symbol X] [--days 7] [--mine]"
---

# nse-insider

The Insider Tracker reads the disclosures that insiders in India are
legally obliged to make, and the large deals the exchange publishes. It
shows what they did; it does not say why, and a promoter buy is not a
signal by itself (it can be a pledge release, an ESOP, a gift or an
inter-se transfer).

**Not financial advice.** The author is not a SEBI-registered adviser.
Copying an insider is a strategy with a long tail of bad outcomes; the
disclosure lag alone (up to two trading days after the trade) means the
price has usually moved.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" insider                        # new filings, last 7 days, all of NSE
python3 "${CLAUDE_SKILL_DIR}/insider.py" insider --symbol RELIANCE --days 90 --all
python3 "${CLAUDE_SKILL_DIR}/insider.py" insider --mine --min-value 10000000   # my symbols, >= 1 crore
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals                          # bulk, block, short-sale disclosures (after the close)
python3 "${CLAUDE_SKILL_DIR}/insider.py" flows                          # FII / DII net, Rs crore
python3 "${CLAUDE_SKILL_DIR}/insider.py" watch --once                   # one poll (for a scheduled task)
```

Exit 0 when something was found (new filings, deals, flows).

## What each row carries

Insider: NSE lists the filings (`corporates-pit-gg`: symbol, company,
broadcast time, a link to the XBRL); the tool opens each filing's XBRL and
reads the SEBI PIT schema tags into `name`, `category` (Promoter, Promoter
Group, Director, KMP...), `type` (Buy / Sell / Pledge / Revoke...), `mode`
(Market Sale, Market Purchase, Off Market, ESOP, Gift, Inter-se...), `qty`,
`value_inr`, pre and post holding %, `trade_date`, `disclosed`,
`days_to_disclose`, and `mine` when the symbol is in your holdings or
watchlist. One request per filing, so `--max-xml 60` caps how many are
opened (use `--mine` or `--symbol` to spend them where it matters). Shown
newest first; `trader/insider_seen.json` keeps what was already shown so
`watch` rings only on a new filing.

Deals: `block` (the 08:45-09:00 and 14:05-14:20 windows, min Rs 10 crore),
`bulk` (a client trading 0.5%+ of equity in a day), `short` (disclosed
short sales), with client name, side, quantity, price and value. NSE posts
them after the close.

Flows: FII and DII buy, sell and net in the cash market, by date.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/holdings.json`, `trader/watchlist.json` | your symbols, for the `*` flag and `--mine` |
| `trader/insider.json`, `trader/deals.json`, `trader/flows.json` | this run |
| `trader/insider_seen.json` | ids already shown |

## Indian-market notes

- SEBI PIT Reg 7(2): insiders disclose trades over Rs 10 lakh in a quarter
  within two trading days; the company passes it to the exchange within two
  more. The `days_to_disclose` column shows the actual lag.
- A pledge creation or invocation shows up here and in /nse-news; an
  invocation (the lender selling pledged shares) is usually a sell signal
  the market has already priced.
- NSE's site rate-limits aggressively. If the feed "did not answer", wait a
  minute; the bulk/block CSVs on the archive host usually still work.
