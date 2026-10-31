---
name: nse-scanner
description: >-
  Chart Scanner for the Indian market (NSE and BSE). Scans the Nifty 500 and
  every F&O stock for setups: opening-range breakouts on 5-minute bars,
  20-day closing-high breakouts, bull flags, reclaimed 20-day averages,
  oversold bounces, inside-day and NR7 squeezes. Builds the universe from
  NSE's own index and derivatives lists, skips stocks under ASM/GSM
  surveillance, keeps Indian Standard Time and NSE hours (09:15-15:30), and
  writes trader/watchlist.json for nse-news, nse-entry and nse-picks. Free
  delayed Yahoo data (.NS / .BO), bar by bar. Use when the user says "scan
  the market", "find me setups", "what is breaking out", "opening range
  breakouts", "scan Nifty 500", "F&O stocks breaking out", "bull flags",
  "oversold stocks", "pre-market scan", "build my watchlist", "kya chal raha
  hai aaj", "what should I look at today", or at the start of a trading day.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# nse-scanner

The Chart Scanner is the first step of an Indian intraday or swing trade. It
reads hundreds of free charts and lists the ones that match a rule. That is
all it does. A scan is a list of charts that match a rule, not a list of
stocks to buy, and a setup is not a prediction. SEBI's own data says most
individual intraday traders lose money; the scanner does not change that. It
only saves you from flipping through 500 charts by hand.

**Not financial advice.** This is software reading public data. The author
is not a SEBI-registered investment adviser or research analyst. Nothing it
prints is a recommendation. No order is placed by this skill, and no skill in
this pack places one without a risk PASS and the user's typed yes.

`${CLAUDE_SKILL_DIR}` below is this skill's folder. The shared library lives
in the sibling folder `nse-common/`; keep the two together.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                    # -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                       # evening or pre-market -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --setups breakout20,bullflag
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                         # after 09:35 IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15              # cut the clock at 10:15 IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (rupees), `--min-volume 500000` (20-day average
shares, daily only), `--vol-mult 1.0` (orb: breakout bar volume against the
average opening-range bar; 0 turns it off), `--or-minutes 15`,
`--now "2026-11-03 10:15"` (IST) to pin the clock, `--offline DIR` to read
saved Yahoo JSON, `--include-surveillance`, `--no-write`, `--show N`,
`--json`. Exit code 0 when the scan found something, 1 when it found nothing
or could not run (one plain line says why).

## How to use it in a session

1. **Once a week, or when NSE reshuffles indices:** `universe`. It reads the
   Nifty 500 constituents and the "Securities in F&O" list from NSE's live
   market-watch API (`getIndicesData`), falls back to the archive CSVs, merges
   them (about 500 symbols - nearly every F&O stock is already in the Nifty
   500), and tags each with `N500`, `FNO` and any ASM/GSM surveillance stage
   from NSE's `reportASM` / `reportGSM` lists. `--index "NIFTY 200"` swaps
   the index. If NSE refuses every request (it rate-limits), the bundled
   `universe.fallback.txt` (dated in its first line) is used and the output
   says so.
2. **Evening after 16:00 IST, or morning before 09:00:** `daily`. Reads three
   months of daily closes for the whole universe (about 30 spark requests),
   finds the setups below, confirms 20-day average volume on the top hits,
   and writes the watchlist.
3. **Between 09:35 and the `no_entries_after` time in `trader/config.json`
   (default 14:45):** `orb`. The first 15 minutes (09:15-09:30) set the range;
   a 5-minute bar that *closes* above the range high, on at least the average
   opening-range bar's volume, is a hit. Hits that fired after the entry
   window are listed last and marked.
4. Hand the watchlist to `/nse-news` (what is moving these), then
   `/nse-entry` (the exact bar to get in on) and `/nse-exit` (the stop and
   the target), or let `/nse-picks` rank it.

Tell the user how many symbols were read, how many failed, and show the top
hits with the one-line reason for each. Never call a hit "a buy".

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: `SYMBOL\|Name\|Industry\|TAGS` a line |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]` with `symbol`, `name`, `setups`, `why`, `score` |
| `trader/config.json` | read for `no_entries_after` (default 14:45) and `avoid_asm_gsm` (default true) |

## The setups

| setup | rule (daily closes unless noted) |
| --- | --- |
| `breakout20` | today's close is above the highest close of the prior 20 sessions |
| `bullflag` | a run of 8%+ over the prior 10-15 sessions, then 3-7 sessions held within a 4% band near the peak |
| `reclaim20` | yesterday closed below the 20-day simple average, today closed above it |
| `trend` | close above the 20-day average, which is above the 50-day |
| `oversold` | RSI(14) under 30 |
| `oversoldbounce` | RSI(14) was under 30 yesterday, crossed back up today on an up close |
| `nr7` | today's high-low range is the narrowest of the last 7 sessions |
| `insideday` | today's range sits inside yesterday's |
| `orb` | 5-minute bar closed above the 09:15-09:30 high on volume (intraday) |

Each hit carries a `score` (breakout and flag 3, reclaim and bounce 2, the
rest 1) so `/nse-picks` can rank them.

## Indian-market details the scanner knows

- **Hours.** Pre-open 09:00-09:08, continuous session 09:15-15:30, closing
  session 15:40-16:00, all IST. The holiday list for 2026 and 2027 is in
  `nse-common`; `/nse-alerts holidays --refresh` pulls the live list.
- **Surveillance.** Stocks in ASM (Additional Surveillance Measure) or GSM
  (Graded Surveillance Measure) carry 100% margin, trade-for-trade settlement
  or price bands; intraday is pointless or impossible in them. They are
  tagged in the universe and skipped by default.
- **Price bands.** Non-F&O stocks have 2/5/10/20% circuit limits; a stock at
  its upper band cannot be bought. F&O stocks have no fixed band but a
  dynamic 10% one. The scanner does not filter by band; `/nse-risk` does.
- **Tick size** depends on the price band: Rs 0.01 up to Rs 250, Rs 0.05 to
  Rs 1,000, Rs 0.10 to Rs 5,000, Rs 0.50 to Rs 10,000, Re 1 to Rs 20,000,
  Rs 5 above. Stops and targets elsewhere in the pack are rounded to it.
- **Data.** Yahoo Finance mirrors NSE (`.NS`) and BSE (`.BO`) with a delay of
  up to 15 minutes and sometimes a missing bar. The orb scan therefore only
  trusts a bar whose 5 minutes have fully elapsed. Prices that matter are
  checked with the broker, never with Yahoo.

## When it cannot run

- "Too early" - the first bar after the opening range has not closed yet.
- "not an NSE trading day" - weekend or exchange holiday.
- "NSE did not answer" - the universe fell back to the bundled list; try
  `universe` again in a few minutes.
- Yahoo 429 - slow down with `--pause 1.0`.
