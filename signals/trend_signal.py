"""Time-series trend signal and vol-targeted position sizing, sprints v8.1-v8.2.

No edge claim. This is a mechanical, fully pre-registered rule used to
exercise universe loading, vol targeting, and leverage-capped position
construction -- not a predictive-signal validation
(sprints/v8.1/PRD.md, sprints/v8.2/PRD.md, House Rule 1). No IC test, no
Sharpe claim in the construction layer itself.

    trail_ret_i(t) = close_i(t) / close_i(t-L) - 1             L = 120 trading days
    signal_i(t)    = sign(trail_ret_i(t))  in {-1, 0, +1}        long/short (v8.2)
                   = 1 if trail_ret_i(t) > 0 else 0               long-only/flat (v8.1, long_short=False)
    sigma_i(t)     = std(log_ret_i, window=W) * sqrt(252)        W = 63 trading days
    raw_weight_i(t) = signal_i(t) * min(v / sigma_i(t), w_max)    v = 0.10, w_max = 0.50
    weight_i(t)     = raw_weight_i(t) * scale(t)                  scale caps gross at g_max = 2.0
    net_exposure(t)   = sum_i weight_i(t)        signed
    gross_exposure(t) = sum_i abs(weight_i(t))   unsigned, bounded by g_max

All parameters are pre-registered in the PRD and fixed for this sprint --
not retuned after looking at output (House Rule 5). A cell is left NaN
(rather than 0) until both the L-day and W-day warmup are satisfied, so
a ticker only enters the book once it has enough real history -- no
synthetic backfill (House Rule 4 / gate E5).

v8.2 makes the signal symmetric (signed) by default. The v8.1 long-only/
flat rule is kept available via `long_short=False`, not deleted, so the
two rules can be compared on equal footing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

L_DEFAULT = 120     # trend lookback, trading days
W_DEFAULT = 63       # vol estimation window, trading days
V_DEFAULT = 0.10      # target annualized vol per active name
W_MAX_DEFAULT = 0.50  # per-name weight cap
G_MAX_DEFAULT = 2.0   # gross leverage cap

REBAL_FREQ_DEFAULT = 1    # trading days between recompute-eligible dates (1 = daily)
NO_TRADE_BAND_DEFAULT = 0.0  # only trade a name if |desired - held| exceeds this, in weight units
NO_TRADE_BAND_CANDIDATE = 0.05  # superseded -- see BAND_PCT_DEFAULT and notes.md T2 scope refinement
REBAL_FREQ_CANDIDATE = 5        # superseded as the chosen mechanism -- see notes.md T2 scope refinement

BAND_PCT_DEFAULT = 0.0     # proportional no-trade band, off by default (backward compatible)
BAND_PCT_CANDIDATE = 0.20  # pre-registered T2 default: 20% of the target weight, not tuned

K_DEAD_ZONE_DEFAULT = 0.0    # signal-level hysteresis, off by default (backward compatible)
K_DEAD_ZONE_CANDIDATE = 0.5  # pre-registered T2b default: 0.5x the implied L-day trail_ret std, not tuned


def compute_dead_zone(sigma: pd.DataFrame, L: int, k: float) -> pd.DataFrame:
    """Implied dead-zone half-width for the L-day trailing return.

    Scaled from the already-computed annualized daily vol sigma_i(t)
    (point-in-time correct, W-day trailing window) under a random-walk
    variance assumption: std(L-day return) ~= sigma_i(t) * sqrt(L / 252).
    `k` is the pre-registered multiple of that implied std
    (K_DEAD_ZONE_CANDIDATE = 0.5). This reuses sigma_i(t), already computed
    for vol-targeting, instead of a separate rolling std of trail_ret --
    "an equivalent sensible unit" per the T2b PRD note, not a new
    free-floating window parameter.
    """
    return k * sigma * np.sqrt(L / TRADING_DAYS)


def _hysteresis_signal(
    trail_ret: pd.DataFrame,
    dead_zone: pd.DataFrame,
    defined: pd.DataFrame,
    long_short: bool,
) -> pd.DataFrame:
    """Stateful sign decision with a dead zone around zero (T2b).

    Go long when trail_ret is clearly positive (above the dead zone), go
    short (long_short=True) or flat (long_short=False) when clearly
    negative (below the symmetric negative dead zone); inside the dead
    zone, hold the previously held state rather than flipping. A name's
    first-ever valid date seeds the state with the plain (unbuffered) sign
    -- there is no prior state to hold before that.

    Uses only `trail_ret.loc[t]`, `dead_zone.loc[t]` (both already known at
    t, computed point-in-time by the caller) and the state carried from
    `t-1` (already determined) -- no future information enters the
    decision, so this cannot introduce look-ahead on top of already-correct
    inputs.
    """
    cols = list(trail_ret.columns)
    tr = trail_ret.to_numpy(dtype="float64")
    dz = dead_zone.to_numpy(dtype="float64")
    d = defined.to_numpy()
    n, k = tr.shape
    out = np.full((n, k), np.nan)
    state = np.full(k, np.nan)

    for i in range(n):
        row = tr[i]
        band = dz[i]
        today_defined = d[i]
        first_time = np.isnan(state) & today_defined

        if long_short:
            plain = np.sign(row)
            clearly_pos = today_defined & (row > band)
            clearly_neg = today_defined & (row < -band)
            new_state = np.where(first_time, plain, state)
            carry_eligible = today_defined & ~first_time
            new_state = np.where(carry_eligible & clearly_pos, 1.0, new_state)
            new_state = np.where(carry_eligible & clearly_neg, -1.0, new_state)
        else:
            plain = (row > 0).astype(float)
            clearly_pos = today_defined & (row > band)
            clearly_neg = today_defined & (row < -band)
            new_state = np.where(first_time, plain, state)
            carry_eligible = today_defined & ~first_time
            new_state = np.where(carry_eligible & clearly_pos, 1.0, new_state)
            new_state = np.where(carry_eligible & clearly_neg, 0.0, new_state)

        out[i] = new_state
        state = new_state

    signal = pd.DataFrame(out, index=trail_ret.index, columns=cols)
    return signal.where(defined)


def compute_trend(
    close: pd.DataFrame,
    L: int = L_DEFAULT,
    W: int = W_DEFAULT,
    v: float = V_DEFAULT,
    w_max: float = W_MAX_DEFAULT,
    g_max: float = G_MAX_DEFAULT,
    long_short: bool = True,
    k_dead_zone: float = K_DEAD_ZONE_DEFAULT,
) -> pd.DataFrame:
    """Build the tidy (long) target-position frame from a close matrix.

    `close` is a date x ticker DataFrame (ascending, unique date index),
    leading NaN per column allowed for staggered inception.

    `long_short=True` (default, v8.2): signal_i(t) = sign(trail_ret_i(t))
    in {-1, 0, +1}. `long_short=False` (v8.1 rule, kept for comparison):
    signal_i(t) = 1 if trail_ret_i(t) > 0 else 0, long-only/flat.

    `k_dead_zone=0` (default, backward compatible): plain sign decision,
    no hysteresis. `k_dead_zone > 0` (T2b): the sign decision is buffered
    by a dead zone (`compute_dead_zone`, scaled to that name's own implied
    L-day trail_ret noise) around zero -- inside the dead zone the
    previously held sign is carried forward instead of flipping. This
    addresses sign-flip turnover, which `apply_rebalance_control`'s
    magnitude-based no-trade band (T2) structurally cannot touch (see
    sprints/v8.2/notes.md, T2b).

    Returns one row per (date, ticker) with columns:
    date, ticker, adj_close, trail_ret, signal, sigma, raw_weight,
    gross_exposure, net_exposure, scale, weight.

    `weight` is the position computed from data through that row's date.
    Use `shift_to_next_day` to label it as the target for the following
    trading day, per the PRD's `target_position_vector(t+1)` convention.
    """
    if k_dead_zone < 0:
        raise ValueError(f"k_dead_zone must be >= 0, got {k_dead_zone}")

    log_ret = np.log(close).diff()
    trail_ret = close / close.shift(L) - 1.0
    sigma = log_ret.rolling(W, min_periods=W).std() * np.sqrt(TRADING_DAYS)

    defined = trail_ret.notna() & sigma.notna()
    if k_dead_zone > 0:
        dead_zone = compute_dead_zone(sigma, L, k_dead_zone)
        signal = _hysteresis_signal(trail_ret, dead_zone, defined, long_short)
    elif long_short:
        signal = np.sign(trail_ret).where(defined)
    else:
        signal = (trail_ret > 0).astype(float).where(defined)

    raw_weight = signal * (v / sigma).clip(upper=w_max)

    gross_raw = raw_weight.abs().sum(axis=1, skipna=True)
    scale = (g_max / gross_raw).clip(upper=1.0)
    scale = scale.where(gross_raw > 0, 1.0)

    weight = raw_weight.mul(scale, axis=0).where(defined)
    net_exposure = weight.sum(axis=1, skipna=True)
    gross_exposure = weight.abs().sum(axis=1, skipna=True)

    frames = []
    for t in close.columns:
        frames.append(
            pd.DataFrame(
                {
                    "date": close.index,
                    "ticker": t,
                    "adj_close": close[t].to_numpy(),
                    "trail_ret": trail_ret[t].to_numpy(),
                    "signal": signal[t].to_numpy(),
                    "sigma": sigma[t].to_numpy(),
                    "raw_weight": raw_weight[t].to_numpy(),
                    "gross_exposure": gross_exposure.to_numpy(),
                    "net_exposure": net_exposure.to_numpy(),
                    "scale": scale.to_numpy(),
                    "weight": weight[t].to_numpy(),
                }
            )
        )
    tidy = pd.concat(frames, ignore_index=True)
    return tidy.sort_values(["date", "ticker"]).reset_index(drop=True)


def to_position_matrix(tidy: pd.DataFrame) -> pd.DataFrame:
    """Pivot the tidy frame to a date x ticker weight matrix."""
    return tidy.pivot(index="date", columns="ticker", values="weight").sort_index()


def to_exposure_series(tidy: pd.DataFrame) -> pd.DataFrame:
    """Date-indexed gross_exposure / net_exposure frame.

    Both columns are already date-level aggregates replicated across
    every ticker row in `tidy` -- this just deduplicates to one row per
    date.
    """
    out = (
        tidy[["date", "gross_exposure", "net_exposure"]]
        .drop_duplicates(subset="date")
        .set_index("date")
        .sort_index()
    )
    return out


def apply_rebalance_control(
    desired: pd.DataFrame,
    rebal_freq: int = REBAL_FREQ_DEFAULT,
    no_trade_band: float = NO_TRADE_BAND_DEFAULT,
    band_pct: float = BAND_PCT_DEFAULT,
    g_max: float = G_MAX_DEFAULT,
) -> pd.DataFrame:
    """Reduce turnover via a discrete recompute schedule and a no-trade band.

    `desired` is the as-of-close(t) weight matrix from `compute_trend`
    (already point-in-time correct; no look-ahead in its own construction).
    This function only ever uses `desired.loc[t]` (already known at t) and
    the previously held value at `t-1` (already determined) -- no future
    information enters the hold logic, so it cannot introduce look-ahead
    on top of an already-correct `desired` input.

    Two band mechanisms, combined additively (`threshold = no_trade_band +
    band_pct * abs(desired)`), so either can be used alone:
    - `no_trade_band`: a flat, absolute threshold in weight units.
    - `band_pct`: a threshold proportional to that day's *desired* weight
      magnitude. This is the chosen T2 mechanism (`BAND_PCT_CANDIDATE =
      0.20`) -- vol-targeted weights vary widely across the universe (a
      high-vol name like EEM carries a much smaller weight than a
      low-vol name like IEF), so a single flat band is relatively far
      tighter for small-weight names and looser for large-weight ones. A
      proportional band keeps the tolerance scaled to each name's own
      position size instead.

    On a non-recompute day (row index not a multiple of `rebal_freq` since
    the start of the frame), the previously held weight is carried forward
    unchanged for every name -- the T2 default is `rebal_freq=1` (checked
    every day; the band alone, not a discrete schedule, is what suppresses
    trades). On a recompute-eligible day, a name's held weight updates to
    its newly desired value -- in full, not partway to the band edge -- only
    if the desired value differs from what is currently held by more than
    the threshold; otherwise it is also carried forward. A name's first-ever
    appearance (point-in-time entry, gate E5) always takes effect
    immediately, regardless of recompute phase or band -- a ticker is never
    held at a stale pre-entry value (there isn't one).

    Re-caps gross exposure after the hold logic (`sum(abs(held)) <= g_max`
    on every date). This is necessary, not redundant with `compute_trend`'s
    own cap: that cap is enforced jointly across all names assuming full
    synchronization at each as-of date. Once names can go stale on
    independent schedules, the held combination (some names current, some
    stale) is no longer guaranteed to satisfy the joint cap by the same
    proof -- it is re-verified and re-applied here explicitly.
    """
    if rebal_freq < 1:
        raise ValueError(f"rebal_freq must be >= 1, got {rebal_freq}")
    if no_trade_band < 0:
        raise ValueError(f"no_trade_band must be >= 0, got {no_trade_band}")
    if band_pct < 0:
        raise ValueError(f"band_pct must be >= 0, got {band_pct}")

    cols = list(desired.columns)
    arr = desired.to_numpy(dtype="float64")
    n, k = arr.shape
    held = np.full((n, k), np.nan)
    prev = np.full(k, np.nan)

    for i in range(n):
        row = arr[i]
        is_recompute_day = (i % rebal_freq == 0)
        newly_entered = np.isnan(prev) & ~np.isnan(row)
        candidate = np.where(newly_entered, row, prev)
        if is_recompute_day:
            still_defined = ~np.isnan(row)
            change = np.abs(row - prev)
            threshold = no_trade_band + band_pct * np.abs(row)
            trade = still_defined & (newly_entered | (change > threshold))
            candidate = np.where(trade, row, candidate)
        held[i] = candidate
        prev = candidate

    held_df = pd.DataFrame(held, index=desired.index, columns=cols)

    # NaN persists exactly until a name's first entry (the loop's own state
    # machine: `prev` starts NaN and is only ever replaced by a defined
    # value, never reverted) -- no extra masking needed here. In particular
    # a temporary data gap in `desired` (gap in the underlying prices, not
    # a true exit) correctly carries forward the last held value rather
    # than dropping to NaN.
    gross = held_df.abs().sum(axis=1, skipna=True)
    scale = (g_max / gross).clip(upper=1.0)
    scale = scale.where(gross > 0, 1.0)
    return held_df.mul(scale, axis=0)


def shift_to_next_day(position_matrix: pd.DataFrame) -> pd.DataFrame:
    """Relabel weights computed from close(t) as the target for t+1.

    `result.loc[date_k] == position_matrix.loc[date_{k-1}]` for every
    row k > 0 -- the weight computed from data through the previous row's
    close becomes the target position for the current row's date.
    """
    out = position_matrix.shift(1)
    out.index.name = "target_date"
    return out
