---
name: nse-timemachine
description: >-
  Time Machine for NSE and BSE trades. Replays every possible outcome of a
  trade before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from /nse-exit (entry, stop, target,
  flat-by), rebuilds the rest of the day 10,000 times from the stock's own
  5-minute bars over the last 60 sessions (time-of-day aware, seeded), and
  re-runs the same opening-range rule on every past session. For swing
  plans it does the same on two years of daily bars over the hold period,
  counting gap-opens through the stop. Prints the odds of target first,
  stop first or neither by the close, where the price sits at the close,
  the average R, the history and one honest verdict, then hands off to
  /nse-budget. Use when the user says "run the time machine", "replay this
  trade", "what are the odds", "simulate this trade", "monte carlo",
  "backtest this setup", "how often does this hit the target", "is this a
  coin flip", "kitna chance hai", or after /nse-exit, before /nse-budget.
argument-hint: "[SYMBOL] [--entry --stop --target] [--runs 10000] [--swing]"
---

# nse-timemachine

The Time Machine does the one thing a trader cannot do by hand: it plays the
plan out thousands of times on the stock's own recent behaviour and tells you
how it usually ends. It is a reality check, not a forecast.

**Not financial advice. A simulation, not a prediction.** The replay learns
from the last 60 sessions (or two years of daily bars for a swing). A results
announcement, an RBI surprise, a circuit halt or a global gap can do what
those sessions never did. The author is not a SEBI-registered adviser.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py"                              # the plan in trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" RELIANCE --entry 2984.50 --stop 2971.20 --target 3011.10
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" --runs 20000 --seed 7
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" INFY --swing --entry 1510 --stop 1464.70 --target 1600.60
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" --json
```

Flags: `--entry-time HH:MM` (IST; default from the plan or 09:35),
`--or-minutes 15`, `--offline FILE` (saved Yahoo 60d/5m JSON, `.gz` ok),
`--plan-file`, `--no-write`, `--json`. Exit 0 when it ran.

## What it does

**Simulation (intraday).** From the entry time to `flat_by` (15:10 IST
default) there are N five-minute bars. For each bar the replay draws a real
historical bar's shape - its open-to-high, open-to-low and open-to-close
fractions - preferring bars from the *same time of day* over the last 60
sessions, and applies it to the running price. Bar low at or under the stop
= stopped (a bar that touches both counts as the stop, which is the honest
assumption). Bar high at or over the target = target. Otherwise the price
rolls on to the next bar. 10,000 runs, seeded, so the number is repeatable.

**Simulation (swing).** The same on daily bars over `max_hold_days`, from
the last 250 sessions.

**History (intraday).** On each of the last 60 sessions: the opening range,
the first 5-minute close above it before `no_entries_after`, the stop at
the midpoint (or the low, matching the plan's rule), the target at the same
R multiple as the plan, out at `flat_by`. Target / stop / neither, and the
average R.

**History (swing).** Every 20-day closing-high breakout in two years, entered
at that close, with the plan's percent stop, the same R target and the time
stop. A gap-open through the stop is scored at the open, not the stop.

**Verdict.** One line: "close to a coin flip", "the edge is there on this
data, and it is small", "the stop is hit more often than the target",
"negative expectancy", or "mixed". Read it as a reality check.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | from /nse-exit |
| `trader/config.json` | `flat_by`, `no_entries_after`, `swing_max_hold_days`, `swing_stop_pct` |
| `trader/timemachine.json` | the odds, the history rows, the verdict |

## How to present it

The three percentages, the average R, the history line, the verdict, and
the disclaimer, in that order. Then the next step: `/nse-budget` sizes the
trade if the user still wants it. If the verdict is negative expectancy or
the stop is hit far more often than the target, say plainly that the
numbers argue against taking the trade.

## Indian-market notes

- Yahoo serves 60 days of 5-minute bars for `.NS` symbols (7 days of
  1-minute). Sessions with gaps in the data are skipped in the history.
- Muhurat trading sessions and half-days are included as they come; they
  are few and rarely change the odds.
- Results days inside the 60-day window stay in the history on purpose:
  they are part of how the stock behaves.
