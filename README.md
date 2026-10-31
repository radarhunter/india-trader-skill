# india-trader-skill

**Twelve Claude skills for the Indian stock market (NSE and BSE). Free, open
source, paper by default. Look before you risk a rupee.**

They scan the Nifty 500 and every F&O stock for setups, read NSE filings and
the news, time the entry and plan the exit in IST, replay 10,000 sessions
before you trade, size the position in rupees with STT and charges counted,
gate every order on NSE's own circuit and surveillance data, execute paper
first (Angel One, Upstox, Groww when you ask for live), ring when a stop or
target hits, read quarterly results and insider filings, and every evening
hand you a shortlist of five swing and five intraday names with the exit
list for whatever you hold.

> **Not financial advice.** This is software that reads public data and does
> arithmetic. It cannot see the future. The author is an advocate, not a
> SEBI-registered investment adviser or research analyst, and nothing here is
> a recommendation. SEBI's own study found 7 in 10 individual intraday traders
> lose money. Stay on paper until you have watched it work for weeks.

## Install

**Method A - as a plugin (recommended):**

```
/plugin marketplace add <you>/india-trader-skill
/plugin install india-trader-skill@india-trader-skill
```
(skills then carry a prefix: `/india-trader-skill:nse-scanner`)

**Method B - clone and copy:**

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/<you>/india-trader-skill.git
cp -r india-trader-skill/nse-* ~/.claude/skills/
```
`nse-common/` must be copied along with the skills; every tool imports it.

**Method C - one project only:** copy the same `nse-*` folders into
`.claude/skills/` inside your project.

Replace `<you>` with the GitHub owner (this repo: `radarhunter`).

Needs Python 3.8+ (`python3 --version`); on Python 3.8 or a machine without
tzdata the tools fall back to a fixed +05:30 offset, which is correct for
India. Nothing to `pip install`. No signup, no API key - until you connect a
broker, and even then paper first. Desktop notifications work on macOS and
Linux; on Windows the alert prints and beeps in the terminal.

## Ten minutes to the first paper trade

```
/nse-budget set my rules                  # trader/config.json, in rupees, paper on
/nse-scanner universe                     # Nifty 500 + F&O, ASM/GSM tagged
/nse-picks tomorrow                       # after 16:00 IST: 5 swing, 5 intraday, exits
```
Next morning:
```
/nse-news                                 # what moved the watchlist, results due
/nse-scanner orb --at 10:00               # which ones closed above the opening range
/nse-entry RELIANCE --watch               # the bar that triggers
/nse-exit                                 # stop at the range midpoint, target 2R
/nse-timemachine                          # 10,000 replays: coin flip or edge?
/nse-budget size                          # how many shares, charges counted
/nse-risk                                 # PASS or BLOCK, with reasons
/nse-broker ticket                        # the plain ticket; say yes; paper fill
/nse-alerts plan && /nse-alerts watch     # rings on the stop, the target, 15:10 (books paper stops/targets)
/nse-picks exits --watch                  # same-day "get out" alerts on every holding
/nse-broker close RELIANCE                # writes the SELL -> /nse-risk -> say yes -> place
```

Rehearsing when the market is shut: set `TRADER_NOW="2026-11-03 10:20"` in
the environment to pin every tool's clock, and pass `--allow-closed` to
/nse-risk.

## The twelve

| skill | job | Indian-market specifics |
| --- | --- | --- |
| `/nse-scanner` | Chart Scanner: universe, daily setups, opening-range breakouts | Nifty 500 + F&O from NSE's lists; ASM/GSM tagged and skipped; 09:15-09:30 range; IST |
| `/nse-news` | News Monitor: headlines, NSE corporate announcements, board meetings | SEBI LODR Reg. 30 filings tagged (results, order, pledge, QIP, rating, regulatory, halt...) |
| `/nse-entry` | Entry Timer: first 5-min close above the range or a level | never fires on a forming bar; entry window from config |
| `/nse-exit` | Exit Planner: stop and target before the entry | NSE price-band tick rounding; flat by 15:10 ahead of MIS square-off; swing mode (CNC) |
| `/nse-timemachine` | 10,000 replays from the stock's own 5-minute bars + every past session the rule fired | time-of-day aware; swing mode counts gap-opens through the stop |
| `/nse-budget` | Budget Manager: shares that fit four limits; one bracket order | STT, stamp duty, exchange, SEBI, brokerage, GST on the round trip; "set my rules" |
| `/nse-risk` | Risk Manager: PASS / BLOCK on every order | NSE live `upperCP` circuit distance, ASM/GSM, results-day block, daily loss, trades a day, window, market open |
| `/nse-broker` | Order Executor: paper by default; Angel One ROBO, Upstox v2, Groww | three locks: risk PASS + typed yes + Claude's Allow once; keys in env vars only |
| `/nse-alerts` | Price Alerts: stop, target, flat-by, bell, levels, % moves | NSE hours and 2026-27 holidays with live refresh; desktop notification + bell |
| `/nse-results` | Results Analyst: quarterly numbers QoQ and YoY, margins, TTM | NSE financial-results feed (Reg. 33), board-meeting dates |
| `/nse-insider` | Insider Tracker: promoter/director trades, bulk and block deals, FII/DII flows | SEBI PIT Reg 7(2) filings; days-to-disclose; `*` on your symbols |
| `/nse-picks` | Tomorrow's shortlist: 5 swing + 5 intraday, the exit list, live exit watch | relative strength vs Nifty, turnover, ATR, results due; at most two per industry |

Each one reads what the last one wrote. Everything lands in a `trader/`
folder in your project: the universe, the watchlist, the plan, the order,
the verdicts, the holdings, the log. You talk to Claude; Claude runs the
Python.

## Your rules, once

`trader/config.json` (example in `nse-budget/config.example.json`):

| key | default | what |
| --- | --- | --- |
| `budget_inr` | 1,00,000 | only money you can afford to lose |
| `max_loss_per_trade_inr` | 1,000 | decides how many shares (1% rule) |
| `max_daily_loss_inr` | 3,000 | no new trades after it |
| `max_trades_per_day` | 3 | then entries are blocked |
| `no_entries_before` / `no_entries_after` | 09:30 / 14:45 | IST |
| `flat_by` | 15:10 | ahead of the broker's MIS square-off (~15:15) |
| `product` | MIS | or CNC for delivery / swing |
| `paper` | true | keep it on |
| `broker` | paper | angelone / upstox / groww when you go live |
| `avoid_asm_gsm`, `avoid_results_day` | true | surveillance stocks and results days skipped |
| `max_circuit_distance_pct` | 2.0 | no buy within 2% of the upper circuit |
| `allow_fno`, `allow_margin`, `allow_short`, `allow_bse` | false | off unless you turn them on |
| `swing_stop_pct`, `swing_max_hold_days` | 3.0 / 5 | the swing plan |

## Data, and its limits

- **Prices**: Yahoo Finance `.NS` / `.BO` charts. Free, unofficial, delayed
  up to 15 minutes, occasionally a missing bar, rate-limited. Anything that
  matters is checked with the broker.
- **Filings, results, insider trades, deals, flows, circuit bands,
  surveillance**: nseindia.com's site APIs, which need a cookie handshake
  and refuse requests now and then. Every tool says when it fell back.
- **Universe**: NSE's Nifty 500 and F&O lists; a bundled fallback when NSE
  will not answer.
- **Holidays**: bundled for 2026-27; `/nse-alerts holidays --refresh` pulls
  NSE's own.

## Safety, in one place

- No skill places an order except `/nse-broker place`, and only with a
  fresh `/nse-risk` PASS (checked within 20 minutes, never future-dated),
  the user's typed yes, and Claude's own Allow once card. A close goes
  through the same gate; a SELL without a held position is refused.
- Paper positions are closed by the same path, or booked automatically at
  the stop or target by `/nse-alerts watch` and `/nse-picks exits`.
- `"paper": true` ships in the config. Live needs `--live` in so many words.
- Broker keys live in environment variables, never in the chat or in files.
- A stop is a plan, not a promise: gaps, circuits and fast markets fill
  past it. Size for the gap.
- `/nse-picks` is a ranking by rules, not a tip sheet. The SKILL.md tells
  Claude how to say so every time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
Offline fixtures only; the live feeds are exercised by hand before each
release (see `tests/LIVE_CHECKS.md`).

## Credits

The shape of this pack - one job per skill, each reading what the last one
wrote, paper first, never an order without a yes - follows Jake Schincariol's
US-market [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(MIT). Everything here is rewritten for NSE/BSE, SEBI rules, IST and rupees.

MIT License.
