---
name: nse-results
description: >-
  Results Analyst for NSE-listed companies. Reads a company's reported
  quarterly numbers straight from NSE's results-comparison feed (the figures
  filed under SEBI LODR Regulation 33: net sales / total income, profit
  before tax, tax, net profit, diluted EPS, exceptional items, audited or
  not) and sums up what changed: each quarter against the previous one and
  against the same quarter a year earlier, net margin and its change, the
  trailing four quarters, and the next board meeting for results. Reads a
  concall transcript when the user pastes one. Use when the user says "how
  did Reliance's results look", "analyze the results", "what changed this
  quarter", "is revenue growing", "check the quarterly numbers", "compare
  margins", "sum up the concall", "when are the results", "result kaisa
  tha", or before trading a stock around its results.
argument-hint: "SYMBOL [--standalone] [--json]"
---

# nse-results

The Results Analyst reads the numbers the company filed with the exchange
and compares them. It does not value the company and it does not say
whether the stock is cheap. It says what grew, what shrank, and by how much.

**Not financial advice.** The author is not a SEBI-registered research
analyst. Numbers are as filed; a one-off item (an exceptional gain, a tax
write-back, a deferred-tax hit) can swing a quarter, and the tool does not
strip those out. Read the filing's notes.

`${CLAUDE_SKILL_DIR}` is this skill's folder; `nse-common/` must sit next to it.

```bash
python3 "${CLAUDE_SKILL_DIR}/results.py" RELIANCE
python3 "${CLAUDE_SKILL_DIR}/results.py" TCS --standalone
python3 "${CLAUDE_SKILL_DIR}/results.py" INFY --json
```

Exit 0 when it read something. Output is saved to
`trader/results_<SYMBOL>.json`.

## What it shows

For up to nine quarters, newest first: revenue, PBT, net profit, EPS, net
margin, revenue YoY, net profit YoY and QoQ; the trailing-twelve-month
totals; a one-line summary of the latest quarter; and the next board
meeting whose purpose mentions results.

Consolidated figures are preferred when at least two consolidated quarters
exist, else standalone; `--standalone` forces it. Banks and NBFCs report
"total income" rather than revenue from operations; the tool uses what the
feed gives and labels the basis.

**Units.** NSE's feed reports most companies in lakhs (divide by 100 for
crore), a few in crores. Compare growth rates and margins, which are
unit-free; treat absolute figures as "as filed". The feed usually carries
the last five quarters; the `notes` field holds the company's own ratio
table when it filed one.

## Reading a concall or results presentation

If the user pastes a transcript or the investor presentation text, read it
yourself and summarise in this order: guidance given or withdrawn, volume
versus pricing, margin commentary, capex and debt, order book, one-offs,
and the questions management dodged. Quote at most one short phrase; the
rest in your own words.

## Indian-market notes

- Results season: within 45 days of each quarter end (by mid-May, mid-Aug,
  mid-Nov, mid-Feb; the full year audited within 60 days of 31 March).
- The board meeting date is public on NSE in advance (`/nse-news` flags it).
  Most results land after 15:30 or on a weekend; the gap is at the next
  open. /nse-risk blocks intraday entries on a results day by default.
- Watch for "exceptional items", "other income" carrying the profit, tax
  rate changes, and a consolidated/standalone switch between quarters.
