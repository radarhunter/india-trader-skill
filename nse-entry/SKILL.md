---
name: nse-entry
description: >-
  Entry Timer for NSE and BSE stocks. Watches a setup bar by bar and tells
  you the moment to get in. Reads free Yahoo 1-minute or 5-minute bars,
  builds the opening range (09:15 to 09:30 IST by default) and calls the
  entry on the first completed bar that CLOSES above the range high, or
  above a level you give it (a 20-day high from the scanner), with an
  optional volume check. Says waiting and how far, triggered with the time
  and price, expired (past your entry window), or not open yet, and writes
  trader/entry.json for /nse-exit. Long side only; it never places an order.
  Use when the user says "tell me when to get in", "watch Reliance",
  "has it broken out", "did it break the opening range", "entry signal",
  "time my entry", "check my watchlist", "entry kab lein", or after
  /nse-scanner and /nse-news, before /nse-exit.
argument-hint: "SYMBOL [--setup orb|level --level PRICE] [--watch]"
---

# nse-entry

The Entry Timer answers one question: has this stock done the thing the
setup needs, yet? It does not say the trade will work. It waits for a bar to
*close* above the line, because a wick above it is not a breakout.

**Not financial advice.** The author is not a SEBI-registered adviser. The
tool never places an order; only /nse-broker can, and only after a risk
PASS and the user's typed yes.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" RELIANCE                          # one look, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" RELIANCE --watch                  # keep polling every 30s until triggered/expired
python3 "${CLAUDE_SKILL_DIR}/entry.py" TATASTEEL --setup level --level 182.40
python3 "${CLAUDE_SKILL_DIR}/entry.py" INFY --interval 1m --vol-mult 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" RELIANCE --now "2026-11-03 10:15" --json
```

Flags: `--or-minutes 15`, `--interval 5m|1m`, `--vol-mult 0` (bar volume
must be at least this many times the average opening-range bar), `--poll 30`,
`--exchange NSE|BSE`, `--offline FILE`, `--no-write`, `--json`.
Exit code 0 when triggered, 1 otherwise.

## The rule

1. The opening range is the high and low of the first `--or-minutes` of the
   continuous session: 09:15 to 09:30 IST by default. (NSE's pre-open
   09:00-09:08 sets the opening price but is not part of the range.)
2. The trigger is a bar, *fully elapsed*, that closes above the range high
   (or `--level`). The entry price recorded is that bar's close. The tool
   never fires on a bar still forming, because Yahoo's last bar updates
   every few seconds and a breakout can un-happen.
3. If `no_entries_after` in `trader/config.json` (default 14:45 IST) has
   passed with no trigger, the setup is expired for the day. A trigger that
   lands outside `no_entries_before`-`no_entries_after` is reported with
   `in_window: false` and /nse-risk will block the order.

## Output

| status | means |
| --- | --- |
| `not-open-yet` | before 09:15 IST |
| `waiting` | range forming, or no close above it yet; shows the distance |
| `triggered` | time, entry (bar close), bar high/low/volume |
| `expired` | past the entry window |

`trader/entry.json` is written only on a trigger. /nse-exit reads it.
With `--watch`, a desktop notification and a bell fire on the trigger
(`notify: true` in config).

## Indian-market notes

- Many brokers fill a market order at the next tick; in a fast breakout the
  fill can be a few paise to a rupee above the bar close. Use a limit order
  a tick or two above the trigger, which /nse-budget writes.
- Yahoo bars for `.NS` symbols are delayed; the trigger time is the bar's
  start time in IST. Check the price with the broker before the order.
- Stocks under ASM/GSM or at an upper circuit cannot be bought; the scanner
  skips them and /nse-risk blocks them.
