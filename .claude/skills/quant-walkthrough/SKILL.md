---
name: quant-walkthrough
description: Write a sprint research report (WALKTHROUGH.md) summarizing signal behavior, data pipeline, findings, and limitations. Use when the user says /quant-walkthrough or asks to document a finished sprint.
---

You are writing a research report for a finished sprint. Be honest and analytical. The goal is that a future version of us (or a colleague) can reproduce the work and judge whether to trade it.

## Output

sprints/vN/WALKTHROUGH.md

## Required Sections

## Summary
Two to four sentences. State the hypothesis, the headline result, and the verdict: confirmed, rejected, or inconclusive against the PRD's falsification criteria.

## Hypothesis & Falsification Criteria
Restate the pre-registered hypothesis and the thresholds from the PRD. State whether each was met.

## Data Pipeline
- Source, vendor, date range, frequency, universe size over time
- Transforms applied, in order
- Known biases (survivorship, look-ahead, restatements) and how they were handled
- Any rows dropped and why, with counts

## Signal Behavior
- Distribution (mean, std, skew, kurt) and plot
- Coverage over time (how many names have a value each day)
- Stationarity / regime notes
- IC / rank-IC with t-stat, decay profile, and a baseline comparison

## Backtest Results
Pre-specified metrics from the PRD, with numbers:
- Sharpe, hit rate, turnover, max drawdown, capacity estimate
- Equity curve and drawdown plot
- Subperiod breakdown (at least halves; regime split if relevant)
- Parameter sensitivity: small table showing metric stability as key params change

## Key Findings
Three to five bullets. What did we actually learn, independent of whether the strategy worked.

## Limitations
- What biases we could not rule out
- What sample-size or multiple-testing concerns remain
- What costs (financing, borrow, market impact) were not modeled

## Reproducibility
- Seed(s) used
- Data snapshot date or vendor version
- Commit hash of the sprint code
- Exact commands to regenerate every plot and table

## Next Steps
Concrete follow-ups. Prefer ideas that would flip an inconclusive result one way or the other.

## Tone

Report what the evidence says, not what we hoped to find. If the signal failed, say so clearly and explain what made us wrong. If it worked, state the residual risks explicitly.
