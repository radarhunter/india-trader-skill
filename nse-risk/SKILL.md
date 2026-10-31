---
name: nse-risk
description: >-
  Risk Manager for NSE and BSE orders. Checks every order against your
  limits and stops anything over them, before it can reach the broker. For
  a new buy: a stop is required, the loss at the stop (measured from the
  higher of the plan's entry and a fresh NSE price, charges included) must
  fit your max loss a trade, reward:risk must meet your minimum, today's
  realised loss plus open risk must fit the daily limit, no more than your
  trades and orders a day, no entries outside your window or when the
  market is shut, no blocked symbols, no ASM/GSM stocks, no F&O, margin,
  short or BSE unless allowed, notional inside max order, position cap and
  cash buffer, no price within 2% of the upper circuit, no intraday entry
  on a results day, no duplicate of a live order, a well-formed bracket.
  Closing a position is never blocked by a daily limit. Prints PASS or
  BLOCK with every reason and records the verdicts so /nse-broker only
  shows an order that passed. Use when the user says "check my order", "is
  this safe", "risk check", "does this break my rules", "can I take this
  trade", "how much have I risked today", "ye trade theek hai", or before
  /nse-broker, always.
argument-hint: "[--orders trader/orders.json] [--allow-closed]"
---

# nse-risk

The Risk Manager is the gate. Every order goes through it, and nothing
reaches /nse-broker without a PASS recorded in `trader/risk.json`. It does
not judge whether a trade is good; it checks whether it breaks the rules you
set in `trader/config.json`.

**Not financial advice.** The author is not a SEBI-registered adviser. A
PASS means the order fits your limits, not that it will make money.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                          # trader/orders.json -> trader/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-03 10:20" --no-fetch   # rehearsal
python3 "${CLAUDE_SKILL_DIR}/check.py" --allow-closed           # paper rehearsal when the market is shut
```

Exit 0 when every order passed, 1 when any was blocked or there was nothing
to check.

## The checks, in order

1. `trader/config.json` exists (otherwise BLOCK: "run /nse-budget set my rules").
2. The order is well formed: symbol, qty, price; bracket with a stop trigger
   below the entry, a stop limit at or under the trigger, a target above the
   entry.
3. Blocked list; BSE (`allow_bse`); F&O instruments (`allow_fno`); short
   sales (`allow_short`).
4. ASM / GSM surveillance, from NSE's live ASM/GSM lists or the universe tags; suspended stocks.
5. Notional against `max_order_inr`, `min_order_inr`, the position cap
   (`max_position_pct` of budget) and cash after `cash_buffer_pct`
   (`allow_margin` off means no leverage).
6. **Loss at the stop** from the higher of the plan entry and the fresh NSE
   price, plus the round-trip charges /nse-budget estimated, against
   `max_loss_per_trade_inr`.
7. Reward:risk from the fresh price against `min_reward_risk`.
8. Today's realised loss (from `trader/trades_log.json`) plus open risk plus
   this order against `max_daily_loss_inr`; `max_trades_per_day`;
   `max_orders_per_day`.
9. Entry window (`no_entries_before`-`no_entries_after` IST) for MIS or
   intraday orders; the market must be open (NSE calendar, holidays).
10. Upper circuit: if the price is within `max_circuit_distance_pct`
    (default 2%) of NSE's upper price band, BLOCK - a stock locked at its
    upper circuit has no sellers, so you cannot get out.
11. Results day: if `trader/news.json` shows a board meeting for results
    today, intraday entries are blocked (`avoid_results_day`).
12. Duplicate: a live order or MIS position already in that symbol.

A SELL that closes a position skips 5-11; getting out is never blocked.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | from /nse-budget |
| `trader/config.json` | the rules |
| `trader/trades_log.json` | today's placed / open / closed trades from /nse-broker |
| `trader/holdings.json` | open positions |
| `trader/news.json` | `results_due` from /nse-news |
| `trader/universe.txt` | ASM/GSM tags from /nse-scanner |
| `trader/risk.json` | the verdicts, with an `order_id` that /nse-broker matches |

## How to present it

The verdict line for each order, then every reason under a BLOCK, then the
day's running totals. If an order is blocked, do not suggest ways around the
rule; the rule is the user's own. They can change `trader/config.json` and
re-run, and you can say so.

## Indian-market notes

- NSE's quote API (`GetQuoteApi getSymbolData`) gives the price band
  (`priceBand` "low-high", `ppriceBand` "No Band" for F&O stocks or the
  percent for others), the tick size, the applicable margin and the index
  membership; `getMetaData` gives the F&O flag and suspension; `reportASM` /
  `reportGSM` give surveillance. When NSE refuses, the tool falls back to
  Yahoo for the price, skips the band check, and says so (`price_source`).
- A board meeting for results is public on NSE days in advance; the stock
  usually gaps the next morning. The results-day block is for MIS only; a
  CNC entry on a results day is allowed but worth a warning.
- Peak margin rules mean an MIS order beyond the cash buffer is a leveraged
  trade. The pack does not size on leverage; `allow_margin` only unblocks it.
