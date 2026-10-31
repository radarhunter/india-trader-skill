---
name: nse-picks
description: >-
  Tomorrow's shortlist for the Indian market, and what to get out of. After
  the close it ranks the Nifty 500 and F&O universe by rules (trend, 20-day
  breakout or pullback held, relative strength against the Nifty, RSI,
  volume surge, closing strength, ATR, turnover, NR7 / inside-day squeeze,
  results due, surveillance) and prints FIVE swing candidates for 1-5 day
  holds and FIVE intraday watch names for the next session, each with the
  reasons and the levels to plan from (entry above today's high, stop, 2R
  target), at most two per industry. It also prints the EXIT list: every
  holding whose plan is broken (stop breached on a close, target reached,
  trend lost, hold period up, results due tomorrow) and the universe's
  breakdowns to avoid. During the session, `exits --watch` checks every
  holding against its stop, target, drawdown, day's drop and the flat-by
  clock from live quotes and rings the moment one says out. Use when the
  user says "what should I buy tomorrow", "give me 5 stocks for tomorrow",
  "kal kya lein", "what should I sell", "which of my stocks should I exit",
  "anything to sell today", "tomorrow's picks", "swing ideas", "intraday
  watchlist for tomorrow", "run the picks", or every evening after 16:00.
argument-hint: "[tomorrow | exits --watch]"
---

# nse-picks

The picks skill is the one most likely to be misread, so read this first.
It produces a **ranking by rules**: charts and filings scored on things a
trader can check. It does not know tomorrow. A stock at the top of the list
is a stock that matched more rules than the others on free delayed data,
with a plan to work from. It is not a buy, and "five stocks to buy tomorrow"
is not something this software, or anyone, can honestly supply.

**Not financial advice.** The author is not a SEBI-registered investment
adviser or research analyst, and giving individual stock recommendations
for a fee in India requires that registration. This tool gives the user a
shortlist and the levels to *plan* a trade; the user does the work, sizes it
with /nse-budget, and nothing is bought without /nse-risk and a typed yes.
Say this, in your own words, every time you present the list. Never phrase
a pick as "buy X".

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/picks.py" tomorrow                      # after 16:00 IST (or before 09:00)
python3 "${CLAUDE_SKILL_DIR}/picks.py" tomorrow --confirm 120 --json
python3 "${CLAUDE_SKILL_DIR}/picks.py" tomorrow --symbols RELIANCE,TCS,INFY,HDFCBANK
python3 "${CLAUDE_SKILL_DIR}/picks.py" exits                         # one check of every holding, now
python3 "${CLAUDE_SKILL_DIR}/picks.py" exits --watch                 # every 30s until 15:35, rings on "out"
```

Flags: `--limit N`, `--min-price 50`, `--pause 0.4`, `--drawdown 2`
(exits: % under the average price), `--day-drop 3` (exits: % on the day),
`--poll`, `--now`, `--no-write`. Exit 0 when it produced a list.

## The evening routine (what to run, in order)

1. `/nse-scanner universe` once a week.
2. `/nse-news` on the watchlist so results dates are known (`trader/news.json`).
3. `/nse-picks tomorrow` after 16:00 IST. Read out the two lists and the
   exit list with the reasons. For anything the user wants to pursue:
4. `/nse-timemachine SYMBOL --swing --entry --stop --target` with the
   levels printed, then `/nse-exit SYMBOL --entry --swing`, `/nse-budget
   size` and `/nse-risk`. A swing (CNC) order checked after the close
   passes with a warning that it will go as an after-market order; an
   intraday (MIS) one is blocked until the window opens.
5. Next morning 09:35: `/nse-scanner orb --watchlist` on the intraday
   names, `/nse-entry` on the one that fires, and so on.
6. All day: `/nse-picks exits --watch` (or `/nse-alerts holdings` +
   `watch`) in the background for same-day sell alerts.

## How the score is built

**Pre-score (closes only, whole universe):** above the 20-day EMA (+1);
20 EMA above the 50 SMA (+1); RSI(14) between 50 and 72 (+1); 20-day
closing-high breakout (+2) or a pullback to the 20 EMA that held and
turned up (+1.5); 20-day relative strength versus the Nifty 50 above +3%
(+1.5) or under -3% (-1); up more than 12% in five sessions (-1, extended).

**Full score (OHLCV, top 80):** average daily turnover under Rs 5 crore is
dropped; volume 1.5x the 20-day average (+1.5 both lists); closed in the
top quarter of the day's range (+1 both) or the bottom quarter (-1);
within 5% of the three-month high (+1 swing); ATR% under 1 (-1 swing) or
over 6 (-1 swing); intraday: ATR 1.5-5% (+1.5), turnover over Rs 50 crore
(+1.5) or Rs 20 crore (+0.5), NR7 (+1), inside day (+0.5), plus a share of
the trend score. **Results due** tomorrow or the day after: -3 on both;
within five days: -1.5 on swing.

**Lists:** top five by swing score and top five by intraday score (not
already in the swing list), scores above zero only, at most two per
industry so one sector rally does not fill the list.

**Levels to plan from (not orders):** entry above today's high plus one
tick; swing stop at the lower of the five-day low and `swing_stop_pct`
(3%) under the entry, capped at 6%; target at 2R. /nse-exit can refine.

## The exit list

For each position in `trader/holdings.json`: closed at or below its stop;
closed at or above its target (book it); closed below the 20-day low;
below the 20 EMA below the 50 SMA on a down day; held longer than
`swing_max_hold_days`; an MIS position still open after the close
(reconcile with the broker); results due in a day. Then up to ten universe
breakdowns under "avoid".

`exits` (live): stop hit, target hit, down `--drawdown` % from the average,
down `--day-drop` % on the day, or the flat-by clock for MIS. Each rings
once per reason via desktop notification and bell.

The user's holdings file is theirs to maintain: `positions[]` with
`symbol`, `qty`, `avg_price`, `stop`, `target`, `product` (MIS/CNC),
`entered` (YYYY-MM-DD), `horizon`. /nse-broker fills it for paper and
recorded trades; positions taken elsewhere are added by hand.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | from /nse-scanner (ASM/GSM dropped, blocked list dropped) |
| `trader/news.json` | results dates from /nse-news |
| `trader/holdings.json` | the positions for the exit list |
| `trader/picks.json` | both lists, exits, avoid, with scores and levels |
| `trader/exits.json` | the live holdings check |

## Honesty rules for presenting picks

- Lead with the disclaimer in one plain sentence, not a wall of it.
- Give the reasons, the levels and the score. Do not add conviction the
  score does not carry ("strong buy", "sure shot", "multibagger").
- If the lists are empty, say the market offered no clean setups; that is
  a result, not a failure.
- If a user asks for guaranteed or "pakka" picks, say no such thing
  exists and point at the Time Machine's coin-flip verdicts.
- SEBI's own study: 7 in 10 individual intraday traders lose money. Say so
  when the user is new.
