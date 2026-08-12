# Sprint v9.1 — Walkthrough

## Summary

We tested a **vol-scaled trailing stop ladder** as an overlay on the v8.2 trend-following ETF book. The hypothesis was that cutting exposure during large trailing drawdowns (measured in units of each position's own recent vol) would save more in avoided losses than it cost in whipsaw re-entries. **The hypothesis was rejected:** the overlay degraded risk-adjusted returns across 4 of 5 pre-registered gates. The stop ladder ships in **advisory-only mode** — states are computed and displayed but never modify live weights. Independently, v9.1 added a **live risk & attribution panel** (Panel M-A/M-B), a **factor-colored NAV tracker** (Panel K), and an **asset-class portfolio snapshot** (Panel I) to the operational dashboard.

**Verdict: REJECTED.** 4/5 falsification gates failed. Hit rate 41% (worse than coin-flip). Fixed -5%/-10% naive stops outperformed the vol-scaled version.

---

## Hypothesis & Falsification Criteria

### Pre-registered hypothesis (PRD)
> *Large trailing drawdowns on an open position, measured in units of that position's own recent vol, predict continued underperformance over the following days-to-weeks (drawdown momentum / slow signal-decay), so cutting exposure inside the window saves more in avoided losses than it costs in whipsaw re-entries.*

### Honest counter-story (also pre-registered)
> *For vol-targeted trend books, stops are often redundant or harmful — vol targeting already shrinks positions when vol rises, and mean reversion at the daily horizon means stops systematically sell local bottoms.*

### Gate results (pre-registered thresholds, default parameters k1=1.5, k2=2.5, h=0.5, m_reduced=0.5)

| Gate | Criterion | Threshold | Actual | Met? |
|------|-----------|-----------|--------|------|
| F1 | Overlay Sharpe ≥ 0.80 × Baseline | 0.80 | 0.828 | ✅ PASS (barely) |
| F2 | DD reduction ≥ 10% OR Calmar improvement | 10% / any | DD -3.1% (worse), Calmar 0.096 vs 0.130 | ❌ FAIL |
| F3 | Not underperform ALL 3 subperiods | ≥ 1/3 | Underperforms 2/3 (2015-18: -0.07 vs 0.02; 2023-end: 0.22 vs 0.25) | ❌ FAIL |
| F4 | Default is a plateau, not a spike | ≥ 50% neighbors same | 1/15 (6.67%) neighbors share qualitative result | ❌ FAIL |
| F5 | Naive ladder < 90% of vol-ladder DD benefit | < 90% | Naive IMPROVES DD by 9.7pp; vol-ladder makes it WORSE by 1.0pp | ❌ FAIL |

**Contingency invoked:** The state machine still ships, but in advisory mode — states computed daily, displayed in Panel H with reason strings, multiplier never applied to live weights.

---

## Data Pipeline

### Source
- **Prices:** Daily adjusted closes from Yahoo Finance (yfinance), cached as Parquet in `data/raw/`
- **Universe:** 8 liquid ETFs — SPY, EFA, EEM, TLT, IEF, HYG, LQD, GLD
- **Date range:** 2007-04-11 to 2026-06-22 (4,830 trading days)
- **Evaluation window:** 2015-01-01 to 2026-06-22 (2,883 days; 63d vol warmup + 120d trend lookback consumed before 2015)

### Transforms (in order)
1. `compute_trend(L=120, W=63, long_short=True, k_dead_zone=0.5)` — 120d trailing return signal, 63d annualized vol, hysteresis dead zone
2. `apply_rebalance_control(band_pct=0.20)` — 20% proportional no-trade band
3. `compute_episodes(held_weights, close, sigma)` — per-ticker episode detection, z-drawdown, state machine (T1)
4. `multipliers * held_weights` → overlay final weights (T2 backtest only; never applied in production)
5. `shift_to_next_day()` — weights effective t+1
6. `run_multi_asset(notional=1e6, cost_params=v6.5)` — daily P&L with turnover + borrow costs

### Known biases
- **Survivorship:** None. Fixed ETF universe, all tickers still trading.
- **Look-ahead:** Explicitly tested in T3 — trigger-day loss is borne, peak M_i uses no future, sigma matches compute_trend byte-for-byte.
- **Restatements:** yfinance adjusted closes restate on dividends/splits (accepted, same as v8.x).
- **Execution mismatch:** Backtest assumes fills at close; live fills are at ~10:30 ET via market orders. Noted, not modeled.

### Row counts
- Raw close: 4,830 rows × 8 tickers
- Desired weights (after compute_trend): 4,830 rows
- Held weights (after band): 4,830 rows (no drops — NaN persisted until first valid entry)
- Evaluation window: 2,883 rows
- No silent drops at any stage (asserted in `apply_stop_overlay` invariants)

---

## Signal Behavior

### Stop-ladder state machine (T1, `risk/stop_loss.py`)

The overlay operates on **held** (post-band) weights. For each ticker, an "episode" starts when weight becomes non-zero with a given sign and resets on sign flip.

```
r_i(t)  = side_i × (P_i(t) / P_i(entry) − 1)     position cumulative return
M_i(t)  = max(0, max_{entry≤s≤t} r_i(s))          running favorable peak  
D_i(t)  = r_i(t) − M_i(t)                          trailing drawdown (≤ 0)
σ_m,i   = σ_annual,i × √(21/252)                   63d vol → 1-month horizon
z_i(t)  = D_i(t) / σ_m,i(t)                        drawdown z-score (≤ 0)
```

States: NORMAL (m=1.0) → REDUCED (m=0.5) → STOPPED (m=0.0), with hysteresis recovery bands.

### Full-history state distribution (2007-2026, all 8 tickers)

| State | EEM | EFA | GLD | HYG | IEF | LQD | SPY | TLT |
|-------|-----|-----|-----|-----|-----|-----|-----|-----|
| NORMAL | 3,293 | 3,662 | 3,165 | 3,685 | 3,343 | 3,699 | 3,714 | 3,349 |
| REDUCED | 1,114 | 740 | 1,061 | 735 | 1,102 | 770 | 724 | 1,013 |
| STOPPED | 303 | 308 | 484 | 290 | 265 | 241 | 272 | 348 |

- **25.3% of weight-days** have multiplier < 1.0 (significant drawdown episodes)
- **GLD** has most STOPPED days (484), consistent with its secular drawdown
- **SPY** has fewest REDUCED days (724), consistent with equity trend strength
- z-score distribution saved: `sprints/v9.1/artifacts/stops_zscore_distribution.png`

### Hit rate (21d forward return, had position been held at full size)
- 9,770 trigger days analyzed
- 4,017 where next 21d return was negative → **41.12%** (95% CI: 40.14%–42.09%)
- Below 50%: stops systematically sell local bottoms → confirms the PRD's counter-story

---

## Backtest Results

### Full-period metrics (2015-01-02 to 2026-06-22, 2,883 days)

| Metric | Baseline (v8.2) | Vol-Ladder Overlay | Δ |
|--------|-----------------|-------------------|----|
| Net Sharpe | 0.2446 | 0.2025 | **−17.2%** |
| Max DD (return) | −32.84% | −33.86% | **+1.0pp worse** |
| Calmar | 0.1297 | 0.0959 | **−26.1%** |
| Ann Return | 4.26% | 3.25% | −1.01pp |
| Ann Vol | 17.41% | 16.04% | −1.37pp |
| Ann Turnover | 5.75× | 11.30× | **+96.5%** |

### Subperiod Sharpe

| Period | Baseline | Overlay | Notes |
|--------|----------|---------|-------|
| 2015–2018 | 0.0169 | **−0.0650** | Overlay negative Sharpe — destroyed value in low-rate regime |
| 2019–2022 | 0.1596 | 0.2271 | Overlay outperforms — COVID vol regime helped? |
| 2023–2026 | 0.2472 | 0.2194 | Overlay underperforms — trend regime strong, stops interfere |

### Sanity baselines (T4)

| Variant | Net Sharpe | Max DD | Calmar |
|---------|-----------|--------|--------|
| Baseline (v8.2) | 0.2446 | −32.84% | 0.1297 |
| Vol-ladder (default) | 0.2025 | −33.86% | 0.0959 |
| **Naive -5%/-10%** | **0.2298** | **−23.12%** | **0.1397** |
| Placebo (shuffled σ) | 0.2670 | −25.42% | 0.1529 |

- **Naive fixed-percentage stops improve DD by 9.7pp** while vol-ladder makes it worse
- **Placebo outperforms both** — per-name vol mapping is unstable; the exact σ→ticker assignment matters
- The vol-ladder is the *worst* of all four variants tested

### Parameter sensitivity grid (T5, 16 cells)

- Default cell (k1=1.5, k2=2.5, h=0.5) among worst on both Sharpe (0.141) and DD (−33.9%)
- Best cell (k1=1.0, k2=2.0, h=0.25): Sharpe 0.206, DD −26.7%
- Only 1/15 neighbors (6.67%) share the default's qualitative result → **spike, not plateau**
- Full grid: `sprints/v9.1/artifacts/stops_grid.csv`
- Heatmap: `sprints/v9.1/artifacts/stops_grid_heatmap.png`

### Equity curve & drawdown plot
`![Equity & Drawdown](artifacts/stops_equity_drawdown.png)`

### Return distribution
`![Return Distribution](artifacts/stops_return_distribution.png)`

---

## Key Findings

1. **Vol-scaled stops fail on trend-following books.** The overlay underperformed the baseline on net Sharpe, max drawdown, and Calmar. Turnover doubled with no compensating benefit. The hit rate (41%) confirms the PRD's pre-registered counter-story: daily mean reversion causes stops to systematically sell local bottoms.

2. **A dumb fixed-percentage stop is better than a vol-scaled one.** The naive −5%/−10% ladder reduced max DD by 9.7pp (vs baseline) while the vol-scaled version made DD 1.0pp worse. Vol-scaling adds noise without signal — the per-name sigma interaction is unstable (placebo outperforms).

3. **The advisory-mode architecture works.** Even though the stop ladder was rejected as a trading rule, the infrastructure (state machine, Supabase persistence, Panel H display) correctly separates computation from execution. The operator sees drawdown warnings without any automatic trading.

4. **Live dashboard panels are useful independently of the sprint outcome.** Panel I (asset-class portfolio snapshot), Panel K (factor-colored NAV tracker), and Panels M-A/M-B (risk contribution + factor P&L) provide operational visibility that was missing before. These will persist regardless of whether the stop ladder is ever activated.

5. **Pre-registration prevented p-hacking.** The 16-cell parameter grid showed that the default (worst-performing) cell could have been cherry-picked to a better one (k1=1.0, k2=2.0, h=0.25) if we had looked at results first. The pre-registered F4 plateau gate correctly caught this.

---

## Limitations

### Biases not ruled out
- **Single rate cycle.** The 2015–2026 sample contains one secular rate regime (declining then rising). The stop ladder might behave differently in a multi-regime sample — but we can't test what we don't have.
- **ETF universe size.** 8 tickers is small. The state machine scales to any N, but the backtest evidence is thin for per-ticker behavior.
- **Fill assumption.** Backtest assumes fills at close; live execution is at ~10:30 ET. The gap between signal close (4 PM) and fill (next morning) creates an unmodeled overnight gap.

### Sample size concerns
- ~2,883 evaluation days, but only 2 complete trend cycles for some sleeves (e.g., TLT rates cycle)
- 16-cell grid = 16 comparisons; F4 plateau check with 1/15 neighbors is underpowered but honest

### Costs not modeled
- Market impact on stop-driven trades (stops trigger during drawdowns, which may coincide with illiquid conditions)
- Borrow cost for short positions uses v6.5 constant (0.4% annual); actual borrow rates vary
- The overlay doubles turnover (5.75→11.30×); at higher AUM, market impact becomes material

### Dashboard limitations (on Render)
- MCTR/PCTR panel (M-A) requires 63d of close prices; the Render web service has no yfinance cache on disk, so this panel degrades to "close data not available" on the deployed dashboard
- Factor-colored NAV tracker (K) depends on `live_attribution` which is written by the execution cron; historical data accumulates over time

---

## Reproducibility

### Seeds
- Backtest: `np.random.seed(42)`
- Placebo sigma derangement: `np.random.RandomState(123)`

### Data snapshot
- yfinance cache: `data/raw/*.parquet`, last updated 2026-06-22
- Supabase project: `omnsjnosbaiqkrmnknqw`

### Commit hash
`f215dc5` (main) — "Panel K: factor-colored stacked NAV area chart matching Panel I colors"

### Commands to regenerate

```bash
# Full backtest (T2 + T4 + T5):
python scripts/backtest_v9_stops.py
# Outputs: sprints/v9.1/artifacts/stops_metrics.csv
#          sprints/v9.1/artifacts/stops_equity_drawdown.png
#          sprints/v9.1/artifacts/stops_return_distribution.png
#          sprints/v9.1/artifacts/stops_zscore_distribution.png
#          sprints/v9.1/artifacts/stops_grid.csv
#          sprints/v9.1/artifacts/stops_grid_heatmap.png

# All tests (39 total):
python -m pytest tests/test_stop_loss.py tests/test_live_risk.py tests/test_dashboard_stops.py -v

# Dashboard (local):
streamlit run dashboard/app.py --server.port 8501
```

### Supabase schema (run once in SQL Editor)
```sql
-- sprints/v9.1/supabase_schema.sql
CREATE TABLE IF NOT EXISTS stop_states (...);
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS advisory BOOLEAN DEFAULT FALSE;
```

---

## Next Steps

1. **Consider the naive −5%/−10% stop.** It improved DD by 9.7pp with modest Sharpe degradation (−6%). A new sprint could pre-register gates for a fixed-percentage ladder. The vol-scaled version is dead; the simple version might not be.

2. **Investigate why placebo outperforms.** Shuffling sigma across tickers produced the highest Sharpe (0.267). This suggests either (a) the per-name vol mapping is actively harmful, or (b) there's a bug in how sigma interacts with the state machine. Worth a dedicated investigation before any future stop work.

3. **Fix MCTR/PCTR on Render.** The web service needs 63 days of close prices. Options: store daily closes in a Supabase `close_prices` table (written by signal cron), or add a yfinance download step to the web service startup.

4. **Add cumulative factor P&L to Panel M-B.** Currently shows only latest-day per-factor P&L. A cumulative view (last 5d, 20d, since-inception) would make the factor attribution more actionable.

5. **Monitor advisory stop states for 1–2 months.** Even though the ladder is not traded, watching which tickers trigger REDUCED/STOPPED in real-time may reveal patterns not visible in the backtest. If the states consistently flag real problems before the trend signal flips, the gate decision could be revisited with fresh pre-registration.
