# Sprint v6 — Tasks

Eight atomic tasks (T1–T8). All work on **Strategy A (RV1_A / rv_hy_ig)** only.
No new backtest runs, no engine changes, no parameter changes.

Status starts `[ ]`; flip to `[x]` as each task lands.

**Dependency order:** T1 (enrich) → T2 (decompose) → T3 (regime) → T4 (hold) →
T5 (beta audit) → T6 (plots) → T7 (notebook) → T8 (summary + close).

---

- [ ] **Task T1: Load and enrich trade ledger**
  - Build the attribution table: run `build_strategy(features, residuals,
    StrategySpec('rv_hy_ig','ols',gated=False))` to get the 94-trade ledger.
    Join `features.parquet` on `entry_fill_date` and `exit_fill_date` to attach:
    `hy_spread` and `ig_spread` at both dates (→ `Δhy`, `Δig`),
    `vol_regime`, `equity_regime`, `equity_credit_lag`, `HYG_vol_21` at entry date.
    Save the enriched table to `sprints/v6/attribution_table.csv`.
  - Acceptance: CSV has 94 rows. Columns include `hy_spread_entry`,
    `hy_spread_exit`, `ig_spread_entry`, `ig_spread_exit`, `delta_hy`,
    `delta_ig`, `vol_regime`, `equity_regime`, `equity_credit_lag`. No NaNs in
    any column (assert explicitly). Print shape and first 5 rows.
  - Files: `sprints/v6/attribution_table.csv`, `notebooks/06_factor_attribution.ipynb`
  - Validation: fails if any NaN in delta_hy or delta_ig (would silently zero out
    decomposition); fails if row count ≠ 94.

- [ ] **Task T2: Per-trade P&L decomposition — FA1, FA2**
  - Compute per-trade:
    ```
    hy_leg_pnl   = side × (−delta_hy) × 1_000_000
    ig_hedge_pnl = side × hedge_ratio_entry × delta_ig × 1_000_000
    gross_check  = hy_leg_pnl + ig_hedge_pnl
    ```
    Verify: `max(|gross_check − gross_pnl|) < $100` (rounding tolerance).
    Compute `hy_share = hy_leg_pnl / gross_pnl` per trade (exclude trades where
    `|gross_pnl| < $500` from share computation to avoid division noise).
    Report: mean hy_share, mean ig_hedge_pnl, mean gross_pnl.
    Evaluate FA1 (mean hy_share ≥ 50%) and FA2 (|mean ig_hedge_pnl| < 20% of
    mean gross_pnl).
  - Acceptance: gross_check residual printed and ≤ $100. FA1 and FA2 verdict
    explicitly printed. Scatter plot of hy_leg_pnl vs ig_hedge_pnl (one dot per
    trade, coloured by side) saved to `sprints/v6/plots/decomp_scatter.png`.
  - Files: `sprints/v6/plots/decomp_scatter.png`, notebook
  - Validation: fails if gross_check > $100 (sign error in formula); fails if
    FA1/FA2 verdicts are not stated; fails if hedge_ratio_exit is used instead
    of hedge_ratio_entry.

- [ ] **Task T3: Regime breakdown — FA3**
  - Slice trades by `vol_regime` (high/low), `equity_regime` (bull/bear), and
    `equity_credit_lag` (equity_first/credit_first/neither) based on value at
    entry date. For each slice report: n_trades, mean net_pnl, hit rate,
    mean holding_days, mean hy_share.
    Evaluate FA3: both vol_regime=high and vol_regime=low must have mean
    net_pnl > 0.
    Note if any single bucket contains >70% of trades (concentration flag).
  - Acceptance: regime summary table printed (3 dimensions × buckets).
    FA3 verdict explicitly stated. Bar chart of mean net_pnl by vol_regime
    and equity_regime saved to `sprints/v6/plots/regime_breakdown.png`.
    Log any concentration flag in `sprints/v6/notes.md`.
  - Files: `sprints/v6/plots/regime_breakdown.png`, `sprints/v6/notes.md`
  - Validation: fails if vol_regime breakdown is not computed; fails if FA3
    verdict is missing; fails if a bucket with n_trades < 5 reports hit rate
    without a "too few to conclude" caveat.

- [ ] **Task T4: Holding-period concentration — FA4**
  - Bucket trades by holding_days:
    - very_short: < 5d
    - short: 5–14d
    - medium: 15–30d
    - long: > 30d
    For each bucket: n_trades, mean net_pnl, hit rate, cumulative net_pnl.
    Evaluate FA4: short (<15d) and long (>20d) groups both must have
    mean net_pnl > 0. (If very_short has < 5 trades: "too few to conclude".)
    Scatter plot: holding_days vs net_pnl (one dot per trade, with horizontal
    zero line and vertical lines at the bucket boundaries) saved to
    `sprints/v6/plots/hold_vs_pnl.png`.
  - Acceptance: bucket table printed. FA4 verdict stated. Plot saved.
    Compute Pearson correlation between holding_days and net_pnl — a
    significantly negative correlation would suggest mean reversion decays
    (longer holds = worse).
  - Files: `sprints/v6/plots/hold_vs_pnl.png`, notebook
  - Validation: fails if bucket thresholds differ from PRD; fails if
    correlation between holding_days and net_pnl is not reported.

- [ ] **Task T5: Net credit beta audit**
  - Check whether Strategy A runs systematic directional credit exposure.
    For each calendar date in the backtest, compute the net HY spread sensitivity
    of all open positions: `net_hy_beta(t) = Σ side_i × 1` for all open trades.
    (Each open trade has 1 unit of HY exposure per $1M notional.)
    Plot `net_hy_beta` over time. If the strategy has on average 0 open positions
    (alternates long and short), the mean should be near zero. If it is
    persistently +1 or −1, the strategy is running directional credit exposure.
    Report: mean net_hy_beta, fraction of days with a net long HY position, and
    fraction of days with a net short HY position.
    Save to `sprints/v6/plots/net_beta_over_time.png`.
  - Acceptance: time-series plot saved. Mean net_hy_beta printed and flagged if
    |mean| > 0.3 (more than 30% net long/short on average). Log result in
    `sprints/v6/notes.md`.
  - Files: `sprints/v6/plots/net_beta_over_time.png`, `sprints/v6/notes.md`
  - Validation: fails if the daily net exposure is computed from the trade ledger
    rather than reconstructed from daily position states; fails if only entry
    dates are used (must be all active holding days).

- [ ] **Task T6: Cumulative attribution curves**
  - Build the time-series view of attribution:
    (a) For each trade, assign the P&L components (hy_leg_pnl, ig_hedge_pnl,
        cost, net_pnl) to a single date (exit_fill_date — when the P&L is realised).
    (b) Compute cumulative sums of each component over time.
    (c) Plot four cumulative lines on the same axes: hy_leg cumulative,
        ig_hedge cumulative, cost cumulative, net_pnl cumulative.
        Save to `sprints/v6/plots/cumulative_attribution.png`.
  - Acceptance: four-line plot saved with labelled axes and title including date
    range and n_trades. The net_pnl curve should match the strategy equity curve
    from v5 (final value ~$760k). If it diverges by >$5k, investigate.
  - Files: `sprints/v6/plots/cumulative_attribution.png`, notebook
  - Validation: fails if net_pnl cumulative final value doesn't match v5 within
    $5k; fails if axes are unlabelled.

- [ ] **Task T7: Notebook assembly — `06_factor_attribution.ipynb`**
  - Write `scripts/build_attribution.py` (generator) and produce
    `notebooks/06_factor_attribution.ipynb`. Four sections:
    (1) Data enrichment (T1 — show enriched table head, assert no NaNs)
    (2) P&L decomposition (T2 — scatter + FA1/FA2 verdicts)
    (3) Regime + holding-period analysis (T3/T4 — tables + plots)
    (4) Beta audit + cumulative attribution curves (T5/T6)
    Execute via `jupyter nbconvert --execute --inplace` — must complete with
    zero cell errors.
  - Acceptance: notebook exists, all cells execute clean. Print
    `[notebook clean]` as the last cell output. All four plots rendered
    inline. FA1–FA4 verdicts visible in cell output.
  - Files: `notebooks/06_factor_attribution.ipynb`,
    `scripts/build_attribution.py`
  - Validation: fails if any cell has an error output; fails if any FA
    verdict is not printed in the notebook output.

- [ ] **Task T8: Attribution summary + sprint close**
  - Write `sprints/v6/attribution_summary.md`. Required content:
    (a) FA1–FA4 scorecard table with stored numbers for every criterion
    (b) Narrative: what the decomposition says about the source of edge
    (c) Regime table: n_trades, mean net_pnl, hit rate by vol/equity regime
    (d) Holding-period table
    (e) Net beta audit result
    (f) **Extension note:** one paragraph stating that RV2_A and RV3_A are
        admitted (per sprints/v5.6/signal_selection.md) and will have the
        same T1–T7 attribution framework applied in sprint v6.5, using
        `rv_credit_rates` and `rv_xterm` residuals respectively. Note any
        differences expected (e.g. rv_xterm has no hedge_ratio stored —
        T2 will need to handle this; rv_credit_rates uses dgs10 as the
        x-leg which is not a spread, so sign convention for ig_hedge_pnl
        needs care).
    Finalise `sprints/v6/notes.md`. Mark all T1–T8 [x] in TASKS.md.
  - Acceptance: attribution_summary.md exists with all six sections.
    Extension note explicitly names the two signals and one implementation
    difference for each. notes.md updated.
  - Files: `sprints/v6/attribution_summary.md`, `sprints/v6/notes.md`
  - Validation: fails if any FA criterion is listed without a number; fails
    if the extension note is absent or doesn't name RV2_A and RV3_A
    explicitly.
