---
name: nse-alerts
description: >-
  Price Alerts for NSE and BSE. Rings when a price, a stop, a target or the
  opening bell hits. Turns the trade plan into three alerts in one go (the
  stop, the target and flat-by at 15:10 IST), arms a stop and a target
  alert on every open position so a same-day sell rings the moment it is
  needed, plus above / below levels, percent moves from the previous close,
  any clock time and the 09:15 bell, each ringing once with a desktop
  notification and a bell. Knows NSE hours (pre-open 09:00, open 09:15,
  close 15:30, broker MIS square-off ~15:15), the 2026 and 2027 holiday
  list with a live refresh from NSE, works out IST itself, fetches free
  delayed quotes and writes trader/prices.json for sizing and the risk
  check. Use when the user says "alert me when", "tell me if Reliance
  hits", "watch my stop", "watch my positions", "remind me to square off",
  "is the market open", "when does the market open", "market holiday",
  "get me a price", "bhav batao", or right after /nse-exit writes a plan.
argument-hint: "[plan | holdings | add SYMBOL above|below PRICE | watch | quote SYMBOL | market | holidays --refresh]"
---

# nse-alerts

Price Alerts is the skill that watches so the user does not have to. It
polls free delayed quotes, rings each alert once, and keeps time in IST
against the NSE calendar. When /nse-alerts rings a stop on a holding, the
user decides; /nse-broker flattens; nothing sells on its own.

**Not financial advice.** The author is not a SEBI-registered adviser.
Yahoo quotes lag NSE by up to 15 minutes; a stop alert is a prompt to look
at the broker's screen, not a fill.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" plan                       # stop, target, flat-by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" holdings --drawdown 2      # stop + target + -2% on every position
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add RELIANCE above 3000
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add TCS pct -2
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add time 15:05 "square off"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" watch                      # polls every 30s until 15:35 IST or all fired
python3 "${CLAUDE_SKILL_DIR}/alerts.py" watch --once               # one pass (for a scheduled task)
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE TCS INFY
python3 "${CLAUDE_SKILL_DIR}/alerts.py" market
python3 "${CLAUDE_SKILL_DIR}/alerts.py" holidays --refresh
```

## Same-day sell alerts ("tell me the moment I should sell")

After /nse-broker fills a buy (paper or live), run `holdings` then `watch`.
Every position gets a `stop` alert (its stop), a `target` alert and a
`drawdown` alert (2% under the average price by default). `watch` rings
within one poll (30 seconds) of the level printing on Yahoo. For an
intraday position the `flat-by` time alert rings at 15:10 so the user can
square off before the broker does it at market around 15:15.

`watch` is a foreground loop; in Claude Code it runs in the background with
`run_in_background` and the user is told the moment it rings. For a
scheduled task, `watch --once` does one pass and exits.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | `plan`: stop, target, flat_by |
| `trader/holdings.json` | `holdings`: every position's stop and target |
| `trader/alerts.json` | the alerts, armed or fired |
| `trader/prices.json` | last poll's prices (/nse-risk and /nse-budget can read it) |
| `trader/nse_holidays.json` | the refreshed holiday list (overrides the bundled one) |

## Kinds

`above` / `below` a price; `pct` move from the previous close (signed);
`time` HH:MM IST; the `bell` at 09:15. Each fires once and is marked with
the time it fired.

## Indian-market notes

- Hours: pre-open 09:00-09:08 (orders matched at 09:08), continuous
  09:15-15:30, closing session 15:40-16:00 (at the closing price only).
  Muhurat trading on Diwali evening is a one-hour special session.
- `market` tells the phase, the next open and the holidays ahead. The
  bundled list covers 2026 and 2027; `holidays --refresh` pulls NSE's own
  (`holiday-master`) into `trader/nse_holidays.json`.
- NSE has no after-hours trading; an "after-hours" alert can only fire at
  the next open, where the stock may gap past the level.
