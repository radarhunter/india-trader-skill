---
name: nse-exit
description: >-
  Exit Planner for NSE and BSE trades. Sets your stop and your target before
  you enter, so you know when to get out. Reads the entry from /nse-entry
  (or a price you give), puts the stop at the opening-range midpoint by
  default (or the range low, your price, a percent, or a multiple of the
  14-day ATR), rounds it down to the NSE tick for that price, and sets the target at
  2R (or 1R, a price, a percent). Refuses a stop at or above the entry, a
  target at or below it, a stop too tight to survive the spread, or a
  reward:risk under your minimum, and says why. Handles intraday (flat by
  15:10 IST before the broker's MIS square-off) and swing (CNC, percent
  stop, hold up to N days). Writes trader/plan.json for the Time Machine,
  the Budget Manager and Price Alerts. Long side only. Use when the user
  says "where's my stop", "set my stop and target", "plan the exit", "when
  do I get out", "stop loss", "what's my R", "risk reward", "stoploss kahan
  lagau", or right after /nse-entry triggers, before /nse-timemachine.
argument-hint: "[SYMBOL --entry PRICE] [--stop or-mid|or-low|PRICE|1.5%|1.5atr] [--target 2R|PRICE|3%] [--swing]"
---

# nse-exit

The Exit Planner writes the two numbers every trade needs before the entry:
where you are wrong (the stop) and where you take the money (the target). It
refuses a plan that does not make sense instead of writing a bad one.

**Not financial advice.** The author is not a SEBI-registered adviser. A
stop is a plan, not a promise: in a gap or a fast market it fills past its
price, and on NSE a stop-loss order (SL / SL-M) is only a trigger. Nothing
here places an order.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py"                                    # from trader/entry.json: stop or-mid, target 2R
python3 "${CLAUDE_SKILL_DIR}/exit.py" --stop or-low --target 1R
python3 "${CLAUDE_SKILL_DIR}/exit.py" RELIANCE --entry 2984.50 --stop 1.2% --target 3%
python3 "${CLAUDE_SKILL_DIR}/exit.py" TATASTEEL --entry 182.40 --stop 1.5atr --target 2R
python3 "${CLAUDE_SKILL_DIR}/exit.py" INFY --entry 1510 --swing            # positional: stop 3%, target 2R, hold <= 5 days
```

Flags: `--min-rr` (overrides `min_reward_risk` in config, default 1.0),
`--min-stop-pct 0.25`, `--entry-file`, `--no-write`, `--json`.
Exit code 0 when a plan was written, 1 when refused or nothing to plan.

## The rules

| | intraday (default) | swing (`--swing`) |
| --- | --- | --- |
| stop | opening-range midpoint | `swing_stop_pct` (3%) below entry, or `--stop` |
| target | 2R | 2R |
| product | MIS (from config) | CNC |
| time stop | flat by `flat_by` (15:10 IST) | `swing_max_hold_days` (5) |

Stops and targets are rounded **down** to the NSE tick for that price band
(Rs 0.01 up to Rs 250, Rs 0.05 to Rs 1,000, Rs 0.10 to Rs 5,000, Rs 0.50
to Rs 10,000, Re 1 to Rs 20,000, Rs 5 above) so the stop is never tighter
than planned and the target is reachable one tick early.

Refusals: stop >= entry; target <= entry; stop closer than
`--min-stop-pct` (0.25%) to the entry; reward:risk under the minimum.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | from /nse-entry: `symbol`, `entry`, `or_high`, `or_low`, `trigger_time` |
| `trader/config.json` | `min_reward_risk`, `flat_by`, `product`, `swing_stop_pct`, `swing_max_hold_days` |
| `trader/plan.json` | `entry`, `stop`, `target`, `risk_per_share`, `reward_risk`, `stop_rule`, `target_rule`, `horizon`, `flat_by` |

## Indian-market notes

- **Square-off.** Brokers auto-square MIS positions between 15:15 and 15:20
  IST, often at market and with a charge. `flat_by` 15:10 keeps you ahead
  of it. /nse-alerts rings at that time.
- **Stop orders.** On NSE a stop is placed as SL (with a limit) or SL-M
  (market on trigger). SL-M is not allowed on many stocks by most brokers
  for cash equity; the pack writes an SL order with a limit at least 0.4%
  below the trigger. Across a circuit halt no stop fills at all.
- **Gaps.** Indian stocks gap on results and global cues at the 09:15 open.
  A swing stop does not protect against the gap; size the position so a
  gap through the stop is survivable (/nse-budget does the arithmetic).
- **STT and charges.** Intraday sells carry 0.025% STT and exchange and
  GST charges; delivery buys and sells carry 0.1% STT each. A 1R target that
  is only 0.3% away is a loss after charges. /nse-budget subtracts them.
