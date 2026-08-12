# Sprint v9.1 PRD -- Vol-Scaled Stop Ladder and Live Risk Attribution Panel

**Status:** planning
**Dependency:** v8.6 live loop operational (signal cron -> Panel H approval -> execution
cron -> Supabase). Any unfinished v8.6 checklist gates (T6/T9 documentation) are not
blocked by, and do not block, this sprint.

---

## Overview

After the first days of live paper trading, the book has no defense against a position
bleeding while its 120-day trend signal stays on (GLD is currently down >7% with no
mechanism that reacts before the slow trend flips). v9 tests a **vol-scaled trailing
stop ladder** as an overlay on the v8.2 trend book -- reduce the position at a first
threshold, unwind at a second, restore when the drawdown recovers -- with thresholds
expressed in units of the same 63-day realized vol already used to size the position.
The overlay is backtested against a pre-registered gate **before** it is allowed to
modify live weights; if it fails the gate it ships in advisory-only mode (displayed,
never traded). Independently, v9 adds a **live risk & attribution panel** (Panel M,
rendered directly below Panel H on the Trade page): per-position MCTR/PCTR from the
rolling covariance, per-position P&L contribution, and the four-factor decomposition
(eq / rates / credit / gold) reusing the v8.3 regression machinery. Panel H's proposed
trade table gains a per-ticker risk-status comment so the operator sees *why* a weight
was reduced or zeroed.

---

## Economic Hypothesis

**Stop ladder.** The 120-day trend signal exits losers slowly by construction: a
sustained adverse move must drag the trailing return through the dead zone before the
weight flips. During that transition window the book holds a position whose premise is
already failing. The hypothesis: *large trailing drawdowns on an open position, measured
in units of that position's own recent vol, predict continued underperformance over the
following days-to-weeks* (drawdown momentum / slow signal-decay), so cutting exposure
inside the window saves more in avoided losses than it costs in whipsaw re-entries.
Who is on the other side: the same trend premium counterparties -- the overlay only
re-times exits the trend signal would eventually make, paying extra turnover for speed.

**Honest counter-story (why this can fail):** for vol-targeted trend books, stops are
often redundant or harmful -- vol targeting already shrinks positions when vol rises,
and mean reversion at the daily horizon means stops systematically sell local bottoms.
This is exactly why the gate is pre-registered and backtest-first.

**Attribution panel.** No return hypothesis. This is measurement infrastructure
(Paleologo-style ex-ante risk decomposition and ex-post P&L decomposition). Gates are
engineering-correctness gates only.

---

## Falsification Criteria

Pre-registered, written before any backtest is run.

**Stop ladder (research gates). The overlay at default parameters
(k1=1.5, k2=2.5, h=0.5, reduce multiplier=0.5) is REJECTED if ANY of:**

| Gate | Rejection criterion |
|------|--------------------|
| F1 | Overlay full-period net Sharpe < 0.80 x baseline net Sharpe (baseline = v8.2 book, net of costs, same period) |
| F2 | Overlay improves NEITHER max drawdown (>= 10% relative reduction) NOR Calmar ratio vs baseline |
| F3 | Overlay underperforms baseline net Sharpe in all 3 subperiods (2015-2018, 2019-2022, 2023-present) -- i.e. any apparent full-period win comes from one regime |
| F4 | The default-parameter result is a spike, not a plateau: fewer than half of the neighboring cells in the T7 sensitivity grid show the same qualitative result (DD improvement without F1-level Sharpe loss) |
| F5 | The naive fixed -5%/-10% ladder (sanity baseline) achieves >= 90% of the vol-scaled ladder's DD improvement -- vol scaling adds nothing over the dumb version |

**Contingency (pre-registered):** if REJECTED, the state machine still ships, but in
**advisory mode** -- stop states are computed daily, written to Supabase, and displayed
in Panel H with reason strings, and the multiplier is never applied to live weights.
The dashboard labels the mode explicitly. This is a full, honest sprint outcome.

**Attribution panel (engineering gates). The panel FAILS review if any of:**

| Gate | Criterion |
|------|-----------|
| A1 | PCTR does not sum to 1.0 within 1e-6 on the live book |
| A2 | Factor beta_explained + residual does not reconcile to book P&L exactly (identity by construction; broken alignment = fail) |
| A3 | Any Panel M number changes when recomputed with data through t-1 only vs data through t (lookahead in cov or betas) |
| A4 | Panel M cold render exceeds 5s or re-triggers the OOM the v8.5 caching work fixed |

---

## Signal Definition

### Stop ladder state machine (per ticker i, daily close data)

Anchoring -- a position "episode" starts on the first day the held (post-band) weight
becomes nonzero with a given sign, and resets whenever the signal weight's sign flips or
the signal goes to zero via the dead zone (new episode = new entry anchor, state NORMAL):

```
side_i        = sign(w_held_i) at episode start          (+1 long, -1 short)
r_i(t)        = side_i * (P_i(t) / P_i(entry) - 1)       position cum return, adj close
M_i(t)        = max(0, max_{entry<=s<=t} r_i(s))         running favorable peak
D_i(t)        = r_i(t) - M_i(t)                          trailing drawdown, <= 0
sigma_m,i(t)  = sigma_i(t) * sqrt(21/252)                63d realized vol (SAME series
                                                          used for sizing), scaled to
                                                          a 1-month horizon
z_i(t)        = D_i(t) / sigma_m,i(t)                    drawdown z-score, <= 0
```

States and transitions (evaluated on close(t), effective t+1 via the existing
`shift_to_next_day` convention):

```
NORMAL  (m=1.0) --[z <= -k1]--> REDUCED (m=0.5)
NORMAL/REDUCED  --[z <= -k2]--> STOPPED (m=0.0)
REDUCED --[z >= -k1 + h]--> NORMAL
STOPPED --[z >= -k2 + h]--> REDUCED        (shadow tracking: r_i, M_i, z_i keep
                                            updating from the ORIGINAL anchor while
                                            stopped, so recovery re-enters)
```

Defaults (pre-registered, grid in T7): **k1 = 1.5, k2 = 2.5, h = 0.5** (hysteresis, in
z units), reduce multiplier **0.5**. Worked example: GLD at sigma ~= 15% annualized has
sigma_m ~= 4.3%; reduce fires at ~ -6.5% trailing DD, unwind at ~ -10.8%. IEF at ~6%
gets ~ -2.6% / -4.3%. Same k, per-name dollars -- this is the "based on the historical
data used to determine the position" requirement made precise.

Final target weight:

```
w_final_i(t) = m_i(t) * w_band_i(t)
```

applied AFTER `apply_rebalance_control` -- stop-driven changes are risk trades and must
NOT be suppressed by the 20% rebalance band. Multiplier changes always generate a trade;
band logic continues to govern signal-driven changes only.

### Panel M measures (live book)

Weights: `w_i = signed_notional_i / live_nav` from Supabase positions + settings.

**MCTR/PCTR** -- Sigma = 63-day sample covariance of daily simple returns (same window
as the sizing vol), annualized x252:

```
sigma_p = sqrt(w' Sigma w)
MCTR_i  = (Sigma w)_i / sigma_p          marginal contribution (annualized)
CTR_i   = w_i * MCTR_i                   dollar-free contribution, sums to sigma_p
PCTR_i  = CTR_i / sigma_p^2 * sigma_p = CTR_i / sigma_p     sums to 1.0
```

**P&L contribution** -- per ticker net P&L over 1d / 5d / since-entry from
`live_attribution` + `pnl_log`, shown with each ticker's share of total book P&L.

**Factor attribution** -- reuse `risk.attribution.factor_returns` (eq=SPY, rates=IEF,
credit=HYG-IEF, gold=GLD) and `rolling_factor_regression` (252d window, fits through
t-1) on the *strategy book return series reconstructed over full history* from the
signal weights (not the days-old live series -- too short to regress). Display: latest
betas, beta-explained vs residual cumulated over the live window, rolling R^2.

### Panel H risk-status comment

Each row of the proposed-trade table gains a `risk status` column:
`NORMAL` / `REDUCED -50% (DD -8.2% = -1.9 sigma)` / `STOPPED (DD -11.4% = -2.7 sigma)`.
When any ticker is non-NORMAL, a warning banner above the table summarizes: which
tickers, current z, threshold crossed, and what restores them (z recovery level). In
advisory mode the banner is prefixed `ADVISORY (not traded)`.

---

## Data

- Same universe as the live book (`signals/etf_universe.py::UNIVERSE`): SPY, EFA, EEM,
  TLT, IEF, HYG, LQD, GLD. (Note: the v8.6 PRD listed GDX/SLV; code is truth.)
- Daily adjusted closes from yfinance, parquet cache in `data/raw/`, history from
  ~2010 to present. Backtest evaluation window 2015-01-01 to latest close (63d vol +
  120d trend warmup consumed before 2015).
- Live state from Supabase: `positions`, `settings.live_nav`, `live_attribution`,
  `pnl_log`; new table `stop_states`.
- Known biases: yfinance adjusted closes restate on dividends/splits (accepted, same as
  v8.x); no survivorship issue (fixed ETF universe, all still trading); fills assumed at
  next close in backtest vs ~10:30 ET market orders live (accepted v8.x mismatch --
  noted, not modeled).
- Point-in-time: all overlay quantities derive from close(t) and earlier; weight changes
  effective t+1. The trigger-day loss is always borne (see T5 leakage check).
- Position drift guard (v8.6 incident): live episode anchors are keyed to
  drift-corrected Supabase positions, refreshed against Alpaca truth by the existing
  drift check before each signal run.

---

## Success Metrics

**Stop ladder (must clear all F-gates above; additionally report, no gate):**
- Turnover: overlay annual turnover / baseline annual turnover (expect <= 1.5x; report only)
- Trigger stats: episodes count, % episodes hitting REDUCED, % hitting STOPPED, median
  time-in-state, whipsaw count (REDUCED->NORMAL->REDUCED within 10 days)
- Hit rate of the stop: fraction of REDUCED/STOPPED triggers where the position's next
  21d return (had it been held at full size) was negative -- i.e. did the stop actually
  dodge losses. > 50% supports the drawdown-momentum story; report with binomial CI.

**Attribution panel:** A1-A4 engineering gates; plus a hand-check: 2-asset toy book
MCTR/PCTR match closed-form to 1e-10 in the unit test.

**Panel H comment:** rendered reason strings match the state machine output exactly on
a mocked 3-state fixture (test asserts string content, not just no-crash).

---

## Research Architecture

```
risk/stop_loss.py              NEW  pure state machine: episode detection, z-drawdown,
                                    transitions, multipliers. No I/O, no Supabase.
backtest (existing engine)     runs baseline vs overlay via a post-band weight hook
scripts/backtest_v9_stops.py   NEW  produces metrics table + plots into sprints/v9.1/artifacts/
risk/live_risk.py              NEW  MCTR/PCTR from live weights + cov; pure functions
risk/attribution.py            REUSE factor_returns, rolling_factor_regression
scripts/run_signal.py          MOD  apply multiplier (or advisory flag) after band step;
                                    persist stop_states to Supabase
dashboard/views/operational.py MOD  Panel H risk-status column + banner; Panel M below H
sprints/v9.1/supabase_schema.sql NEW  stop_states table
```

Split: signal construction (trend, unchanged) / overlay (stop_loss.py) / portfolio
construction (band + multiplier) / backtest (engine hook) / measurement (live_risk.py,
attribution reuse) / presentation (operational.py).

`stop_states` schema: `(ticker TEXT, state TEXT, episode_entry_date TEXT,
episode_entry_price NUMERIC, side INT, peak_r NUMERIC, z NUMERIC, multiplier NUMERIC,
updated_at TIMESTAMPTZ, PRIMARY KEY (ticker))` -- one live row per ticker; history in
cron logs.

---

## Risks & Biases

- **Stops on trend books have a bad prior.** Vol targeting already de-risks in high-vol
  regimes; the overlay may only add turnover. Mitigated by pre-registered gates and the
  advisory-mode contingency -- we cannot talk ourselves into shipping a failed overlay.
- **Multiple testing.** The T7 grid is 18 cells; the adoption decision is made at the
  pre-registered defaults ONLY. The grid exists to falsify (F4 plateau check), never to
  pick a better cell. Any re-parameterization requires a new sprint with fresh gates.
- **Lookahead.** Peak M_i(t) and sigma_i(t) use data through t; weights effective t+1.
  T5 is a dedicated leakage test (trigger-day loss must be borne in the backtest P&L).
- **Regime dependence.** F3 guards against a single-regime artifact; 2015-2026 contains
  only ~2 full trend cycles for some sleeves -- acknowledged, not fixable with this data.
- **Short live history.** Factor regression on ~days of live book returns is meaningless;
  the panel regresses the reconstructed full-history book series instead, and this is
  labeled on the panel to avoid overreading.
- **Live-state corruption.** A wrong `stop_states` row (e.g. after the v8.6-style
  position drift) would mis-size real trades. Mitigation: run_signal recomputes the full
  episode from price history + drift-corrected positions each evening; the Supabase row
  is a cache for display, never the source of truth for the multiplier.

---

## Out of Scope

- Diversification stats (effective N, diversification ratio) -- explicitly deselected
- Intraday stop monitoring (daily close only; the cron cadence is the platform)
- Changing trend signal parameters (L, v, w_max, band_pct frozen at v8.2 values)
- Portfolio-level (book) drawdown stop -- per-position only this sprint
- Re-optimizing k1/k2/h beyond the pre-registered grid; adaptive/learned thresholds
- Options or futures hedging; universe changes; live (non-paper) trading
- Backfilling attribution history before v8.6 go-live into Supabase

---

## Dependencies

| Dependency | Source |
|-----------|--------|
| v8.2 signal pipeline + band control | `signals/trend_signal.py` |
| Backtest engine | `backtest/engine.py`, `backtest/multi_asset.py`, `backtest/metrics.py` |
| v8.3 factor machinery | `risk/attribution.py` (factor_returns, rolling_factor_regression, mctr_by_sleeve as reference) |
| Live loop | `scripts/run_signal.py`, `scripts/run_execution.py`, Supabase tables from v8.6 |
| Price history | `data/raw/*.parquet` (yfinance) |
| Supabase project | omnsjnosbaiqkrmnknqw; new `stop_states` table |
| Dashboard | `dashboard/views/operational.py` Panel H + new Panel M |
