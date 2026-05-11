"""Mapping from signal name → z-column → regime column → position text.

The six Today-View cards are driven by ``CARD_SPECS`` (order = display order).
``position_text(signal, z)`` returns the free-form trade text shown
on a card given the signal and its current z-score.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalSpec:
    name: str           # display name
    z_col: str          # column in features.parquet holding the z-score
    regime_col: str     # column in features.parquet holding the regime label
    family: str         # "directional" or "rv"
    long_text: str      # action when z < -entry
    short_text: str     # action when z > +entry


CARD_SPECS: tuple[SignalSpec, ...] = (
    SignalSpec("hy_spread",       "hy_spread_z63",       "equity_credit_lag",
               "directional", "Long HYG (cheap)",          "Short HYG (rich)"),
    SignalSpec("ig_spread",       "ig_spread_z63",       "equity_credit_lag",
               "directional", "Long LQD (cheap)",          "Short LQD (rich)"),
    SignalSpec("hy_ig",           "hy_ig_z63",           "equity_credit_lag",
               "directional", "Long HYG / Short LQD",      "Short HYG / Long LQD"),
    SignalSpec("rv_hy_ig",        "z_rv_hy_ig",          "equity_credit_lag",
               "rv",          "Buy residual (long HY, short β·IG)",  "Sell residual (short HY, long β·IG)"),
    SignalSpec("rv_credit_rates", "z_rv_credit_rates",   "equity_credit_lag",
               "rv",          "Buy residual (long HY, short β·rates)", "Sell residual (short HY, long β·rates)"),
    SignalSpec("rv_xterm",        "z_rv_xterm",          "equity_credit_lag",
               "rv",          "Buy residual (long HY-IG, short β·slope)", "Sell residual (short HY-IG, long β·slope)"),
)


def position_text(spec: SignalSpec, z: float | None, entry: float = 2.0) -> str:
    if z is None or (isinstance(z, float) and z != z):  # NaN check
        return "—"
    if z >= entry:
        return spec.short_text
    if z <= -entry:
        return spec.long_text
    return "No trade"
