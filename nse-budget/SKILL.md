---
name: nse-budget
description: >-
  Budget Manager for NSE and BSE trades, in rupees. Sizes every position to
  your budget and your max loss per trade. Reads the plan from /nse-exit
  (entry, stop, target), finds the whole shares that fit inside four limits
  (max loss per trade after STT, brokerage, stamp duty, exchange charges and
  GST on the round trip; position cap; max order; cash after your buffer),
  says which limit decided it, and writes ONE broker-neutral bracket order
  (limit entry, stop-loss trigger and limit, target) to trader/orders.json
  for /nse-risk and /nse-broker. A stop too far away for your max loss is a
  plain refusal, not a smaller order. Also writes trader/config.json - the
  "set my rules" step - with sanity checks, and shows the charge arithmetic
  for any trade. Nothing is placed. Use when the user says "how many
  shares", "size this trade", "how much should I buy", "I have one lakh",
  "I only want to risk 1,000 a trade", "set my rules", "kitne share lun",
  "keep 10% in cash", "what will the charges be", or after /nse-exit.
argument-hint: "[size | config --budget-inr 100000 --max-loss-per-trade-inr 1000 ... | charges --qty --buy --sell]"
---

# nse-budget

The Budget Manager turns a plan into a quantity. Its only question is: how
many shares can this trade carry so that, if the stop is hit, the loss plus
every charge stays inside your max loss a trade - and inside your position
cap, your max order and the cash you keep free?

**Not financial advice.** The author is not a SEBI-registered adviser. The
order it writes is a file; no order reaches a broker without a /nse-risk
PASS and the user's typed yes in /nse-broker.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" size                       # trader/plan.json -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" size --cash 85000 --max-loss 750
python3 "${CLAUDE_SKILL_DIR}/size.py" config --budget-inr 100000 --max-loss-per-trade-inr 1000 \
    --max-daily-loss-inr 3000 --max-trades-per-day 3 --paper
python3 "${CLAUDE_SKILL_DIR}/size.py" charges --qty 25 --buy 2984.50 --sell 2971.20 --product MIS
```

Exit 0 when an order or config was written, 1 on a refusal.

## "Set my rules"

When the user says "set my rules" or has no `trader/config.json`, ask these
in one message, suggest the defaults, then run `config` with the answers:

1. **Budget** (`budget_inr`): only money they can afford to lose. Default 1,00,000.
2. **Max loss a trade** (`max_loss_per_trade_inr`): the usual rule is 1% of the
   budget. Default 1,000. Over 5% is refused without `--force`.
3. **Daily loss limit** (`max_daily_loss_inr`): no new trades after it. Default 3,000.
4. **Trades a day** (`max_trades_per_day`): default 3.
5. **Entry window** (`no_entries_before` / `no_entries_after`): 09:30-14:45 IST.
6. **Flat by** (`flat_by`): 15:10 IST, ahead of the broker's MIS square-off.
7. **Product**: MIS (intraday) or CNC (delivery / swing).
8. **Broker**: paper (default), angelone, upstox or groww. `paper` stays true
   until the user has watched paper orders work and asks for live.

Never write `"paper": false` unless the user explicitly asks for live trading
in that message.

## The four limits

| limit | shares |
| --- | --- |
| max loss a trade | `(max_loss - round-trip charges) / (entry - stop)`, iterated because charges depend on quantity |
| position cap | `budget * max_position_pct / entry` |
| max order | `max_order_inr / entry` |
| cash after buffer | `min(cash, budget) * (1 - cash_buffer_pct) / entry` |

The smallest wins and is named in the output. Zero shares is a refusal with
the reason, never a rounded-up order.

## Charges it counts (NSE cash equity)

| | intraday (MIS) | delivery (CNC) |
| --- | --- | --- |
| STT | 0.025% on the sell | 0.1% on buy and sell |
| stamp duty (buy) | 0.003% | 0.015% |
| exchange transaction | 0.00297% both sides | same |
| SEBI fee | 0.0001% both sides | same |
| brokerage | min(0.03%, Rs 20) an order (config) | Rs 0 (discount brokers) |
| GST | 18% on brokerage + exchange + SEBI | same |

Full-service brokers charge more; set `brokerage_per_order_inr` and
`brokerage_pct_intraday`. `charges` prints the arithmetic for any trade.

## The order it writes

A broker-neutral bracket in `trader/orders.json`:
`symbol`, `exchange`, `side BUY`, `product`, `qty`, `order_type LIMIT`,
`price` (entry + one tick), `bracket.stop_trigger` (the stop),
`bracket.stop_limit` (three ticks under it, for an SL order),
`bracket.target`, `loss_at_stop_inr`, `gain_at_target_inr`,
`reward_risk_after_charges`, `limits`, `decided_by`, `paper`.
/nse-broker turns it into an Angel One ROBO order, Upstox or Groww orders,
or a paper fill.

## Indian-market notes

- **Leverage.** Brokers offer up to 5x intraday margin on MIS (SEBI peak
  margin: 20% of VAR+ELM). The pack sizes from the *cash* budget, not the
  leveraged buying power, because the loss is paid in cash. `allow_margin`
  in config stays false by default and /nse-risk blocks a notional above
  the budget.
- **Delivery (CNC)** needs full cash on T, settles T+1. Sizing a swing from
  cash is the honest number.
- A target under about 0.3% from the entry is a loss after charges on an
  intraday trade; `reward_risk_after_charges` shows it.
