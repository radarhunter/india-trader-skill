---
name: nse-news
description: >-
  News Monitor for NSE and BSE stocks. Tracks breaking news on every stock
  you watch and flags what moves it: Yahoo Finance headlines per symbol,
  every company's corporate announcements filed with NSE under SEBI LODR
  (quarterly results, board meetings, order wins, acquisitions, QIPs,
  promoter pledges, rating actions, resignations, regulatory notices), the
  board-meeting calendar so you know when results land, Google News India
  as a fallback, and how far each stock is from yesterday's close. Shows
  only what is new since the last check, newest first, in IST, tagged
  (results, order, deal, pledge, rating, regulatory, halt, macro...) and
  never repeats an item. Use when the user says "any news on Reliance", "why
  is this moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "monitor the news", "did anything break", "results this
  week", "check the announcements", "koi news hai", or after /nse-scanner.
argument-hint: "[SYMBOL,SYMBOL...] [--all] [--hours 36]"
---

# nse-news

The News Monitor reads what is public about the stocks you watch and tells
you what is new. It does not say whether the news is good or bad for the
price, and it does not predict. A headline is a reason to read the filing,
not a reason to trade.

**Not financial advice.** The author is not a SEBI-registered adviser.
Keyword tags are a reading aid; the filing itself is the source of truth.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                        # the watchlist (or holdings) since the last check
python3 "${CLAUDE_SKILL_DIR}/news.py" RELIANCE,TCS,HDFCBANK  # these symbols
python3 "${CLAUDE_SKILL_DIR}/news.py" --all --hours 72        # everything, not just what is new
python3 "${CLAUDE_SKILL_DIR}/news.py" --json
```

Flags: `--watchlist FILE`, `--limit 25` (symbols from the watchlist),
`--hours 36` (drop older items), `--no-filings` (headlines only),
`--no-quotes`, `--pause 0.5`. Exit code 0 when something is new, 1 when not.

## How to present it

For each symbol: the last price and the move from the previous close, any
board meeting for results in the next ten days (flag it loudly - a day trade
across a results announcement is a coin toss with a gap), then the new items
newest first with their tags. Keep it to the items that could move the price;
the raw list is in `trader/news.json` if the user wants all of it.

When a symbol on the watchlist has **results due this week**, say so before
anything else, and note that `/nse-risk` blocks new intraday entries on a
results day when `avoid_results_day` is on.

## Sources

| source | what | notes |
| --- | --- | --- |
| Yahoo Finance RSS | headlines per `.NS` symbol | free, mixed quality |
| NSE corporate announcements | the company's own SEBI LODR Reg. 30 filings | the cleanest source; NSE's site needs a cookie handshake and sometimes refuses - the tool then says "headlines only" |
| NSE board meetings | `bm_date` and `bm_desc` (the stated purpose; `bm_purpose` is a generic label) | used for "results due" |
| Google News India | search by company name | only when the first two are thin |
| Yahoo chart | last price and previous close | delayed up to 15 minutes |

Routine filings (newspaper publications, trading-window closures, Reg. 74
certificates, investor-complaint statements, share-certificate losses) are
dropped so the signal is not buried.

## Tags

`results`, `boardmeeting`, `dividend` (also buy-backs, bonus, splits),
`order` (order wins and contracts), `deal` (M&A, stakes, JVs, demergers),
`fundraise` (QIP, preferential, rights, NCDs), `pledge` (promoter pledges and
invocations), `blockdeal`, `rating` (CRISIL, ICRA, CARE, broker targets),
`management` (resignations, appointments), `regulatory` (SEBI, RBI, NCLT,
courts, tax raids), `halt` (circuits, ASM/GSM, T2T), `capex`, `guidance`
(concalls, investor presentations), `macro` (Nifty, FII/DII, rupee, crude,
RBI policy, Budget, tariffs), `other`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the symbols, when none are given |
| `trader/holdings.json` | the symbols, when there is no watchlist |
| `trader/news.json` | this run's report (quotes, results due, tagged items) |
| `trader/news_seen.json` | item ids already shown, so nothing repeats |

## Indian-market notes

- Results season: the 45 days after each quarter end (mid-May, mid-August,
  mid-November, mid-February). Most Nifty 500 companies announce after 15:30
  or on a weekend; the price reacts at the next open.
- A company must file material events with the exchange within 24 hours
  (SEBI LODR Reg. 30), often minutes after the event. The NSE announcement
  therefore usually beats the headline.
- Promoter pledge creation or invocation and ASM/GSM moves are the two
  filings most likely to hit an intraday long. The tool tags both.
