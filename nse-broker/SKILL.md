---
name: nse-broker
description: >-
  Order Executor for NSE and BSE. Sends approved orders to your broker,
  paper trading by default, never without your yes. Shows each order as a
  plain ticket (shares, limit, stop-loss trigger and limit, target, the max
  loss at the stop, MIS or CNC, paper or live) plus the exact payload for
  Angel One SmartAPI (ROBO bracket), Upstox API v2 or Groww, records your
  yes word for word, then Claude's own Allow once / Deny card asks again.
  Paper fills are logged to trader/trades_log.json and trader/holdings.json
  so /nse-risk can hold the daily limit and /nse-alerts can watch the stop.
  Builds the sell that flattens a position before the MIS square-off,
  records real fills, shows positions and the day's P&L. Credentials live
  in environment variables, never in chat. Use when the user says "place
  the order", "buy it", "execute", "send it to my broker", "close my
  position", "square off", "get me out", "connect Angel One", "connect
  Upstox", "connect Groww", "set up paper trading", "order laga do", or
  after /nse-risk passes.
argument-hint: "[ticket | place --yes \"<user's words>\" | close SYMBOL | fill ORDER_ID --price P | status]"
---

# nse-broker

The Order Executor is the only skill in this pack that can lead to an order,
and it cannot do it alone. Three locks, every order, every time:

1. **A risk PASS** for this exact order in `trader/risk.json`, checked by
   /nse-risk within the last 20 minutes.
2. **The user's yes**, typed in the chat and passed word for word in
   `--yes`. "Looks good" is not yes. If the user says anything other than a
   clear yes, do not run `place`.
3. **Claude's own Allow once / Deny card** on the `place` command. Never ask
   the user to choose "Always allow" for it.

**Not financial advice.** The author is not a SEBI-registered adviser. Most
individual intraday traders lose money (SEBI's 2024 study: 7 in 10). Paper
first, and stay on paper until you have watched it work for weeks.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" ticket                         # show what passed, with the broker payload
python3 "${CLAUDE_SKILL_DIR}/ticket.py" place --yes "yes, place it"    # only after the user typed that
python3 "${CLAUDE_SKILL_DIR}/ticket.py" close RELIANCE                 # write the flattening SELL (then /nse-risk, then place --yes)
python3 "${CLAUDE_SKILL_DIR}/ticket.py" close RELIANCE --yes "yes"     # send it
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill 3f9a1c2b7d4e --price 2986.10
python3 "${CLAUDE_SKILL_DIR}/ticket.py" status
```

## The flow

`/nse-budget size` writes the order; `/nse-risk` writes the verdict;
`ticket` shows it in plain words; the user says yes; `place --yes "..."`
sends it (paper: an instant simulated fill at the limit; a paper buy in a
symbol already held is refused). After a real fill, `fill ORDER_ID --price
P` records the price the broker reported so the position, the stop and the
P&L are right. `close SYMBOL` only *writes* the SELL to `trader/orders.json`;
run /nse-risk on it (a close is never blocked by the daily limit, and a SELL
larger than the position or with no position is BLOCKed), get the yes, then
`place --yes` sends it - the same three locks as a buy, no shortcut.

On paper, a stop or target that prints during `/nse-alerts watch` or
`/nse-picks exits` is booked at that level automatically, so the daily loss
tally in /nse-risk is real.

Before `flat_by` (15:10 IST default), /nse-alerts rings; the user decides;
`close` flattens. Brokers auto-square MIS around 15:15-15:20 with a charge
and at market; do not rely on it.

## Paper trading (default)

`"paper": true` in `trader/config.json`. A `place` records a fill at the
limit price, adds the position to `trader/holdings.json`, logs the trade.
No account, no API key, no network. Switch to live only when the user asks
for it in so many words: `/nse-budget config --live --broker angelone`.

## Live brokers

| broker | env vars | how the bracket works |
| --- | --- | --- |
| Angel One SmartAPI | `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET` | one ROBO order carries entry, stop-loss and target; the tool logs in with a TOTP it computes from the secret |
| Upstox API v2 | `UPSTOX_ACCESS_TOKEN` (daily token from the OAuth flow), optional `ISIN_<SYMBOL>` | entry LIMIT first; the SL and target legs are returned as `pending_legs` and placed right after the fill (Upstox has no native bracket for equities) |
| Groww Trade API | `GROWW_ACCESS_TOKEN` | entry first; SL and target as `pending_legs` after the fill |

The live adapters were written from each broker's public API docs and tested
only to the point the docs allow without a funded account; the payload
shapes are printed by `ticket` so the user can compare them with their
broker's documentation before the first live order. Keys never go in the
chat. If one is pasted by mistake: delete the message, revoke the key, make
a new one.

Set the broker's own app-side safeguards too: Angel One and Upstox let the
user cap daily order count and value from their dashboards.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | from /nse-budget; consumed on `place` |
| `trader/risk.json` | verdicts from /nse-risk, matched by `order_id` |
| `trader/trades_log.json` | every order: placed / open / closed / rejected, with the yes, fills, P&L |
| `trader/holdings.json` | open positions (`positions[]` with `symbol`, `qty`, `avg_price`, `stop`, `target`, `product`) |

## Indian-market notes

- **Products.** MIS = intraday, auto-squared by the broker; CNC = delivery,
  needs full cash, settles T+1. The ticket names the product.
- **Stop orders.** An SL order on NSE needs a trigger and a limit; the pack
  writes the limit at least three ticks and at least 0.4% under the trigger
  so it fills in a fast move. SL-M (market) is not accepted on many cash
  stocks. Across a gap or a circuit no SL fills; size for that.
- **Upstox / Groww legs.** Neither has a native equity bracket; the entry
  goes first and the SL and target legs are returned as `pending_legs` to
  place right after the fill. The ticket says so in capitals. Upstox's
  order API has moved between v2 and v3 in 2025; check the payload against
  the current docs before the first live order.
- **Circuits.** A stock at its upper circuit has no sellers; at the lower,
  no buyers. A stop does not fill through a circuit. /nse-risk checks the
  distance before the entry.
- **Charges** are already inside `loss_at_stop_inr` (/nse-budget).
