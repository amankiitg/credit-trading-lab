"""W2 — position state machine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from execution.position import run_state_machine


def _z(values: list[float]) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype="float64")


def test_short_entry_and_exit() -> None:
    # z crosses +entry → short; falls inside exit band → flat
    z = _z([0.0, 2.5, 1.5, 0.3, 0.0])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert pos.tolist() == [0, -1, -1, 0, 0]


def test_long_entry_and_exit() -> None:
    z = _z([0.0, -2.5, -1.0, -0.2, 0.0])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert pos.tolist() == [0, 1, 1, 0, 0]


def test_stop_loss_closes_position() -> None:
    # short entered at 2.5, blows through stop at 4.5 → flat; day 4 z
    # is below entry so the close is isolated from any re-entry.
    z = _z([0.0, 2.5, 3.0, 4.5, 1.0])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert pos.tolist() == [0, -1, -1, 0, 0]


def test_reentry_after_stop_when_signal_still_extreme() -> None:
    """Per the PRD spec there is no post-stop cooldown: if z is still
    beyond entry the bar after a stop, the machine re-enters."""
    z = _z([0.0, 2.5, 3.0, 4.5, 3.0])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert pos.tolist() == [0, -1, -1, 0, -1]


def test_only_one_position_at_a_time() -> None:
    # while short, a deeper +z does not open a second position
    z = _z([0.0, 2.5, 3.5, 3.0, 0.2])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert set(pos.unique()) <= {-1, 0}
    assert (pos == -1).sum() == 3


def test_position_held_across_quiet_days() -> None:
    # entered short, z stays between exit and stop → stays short
    z = _z([0.0, 2.5, 1.8, 1.2, 0.9, 0.6])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert pos.tolist() == [0, -1, -1, -1, -1, -1]


def test_regime_gate_blocks_entry() -> None:
    z = _z([0.0, 2.5, 2.6, 0.3])
    gate = pd.Series([True, False, True, True], index=z.index)
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0, regime_gate=gate)
    # day 1: gate False → no entry; day 2: gate True, z>entry → short
    assert pos.tolist() == [0, 0, -1, 0]


def test_regime_gate_never_blocks_exit() -> None:
    # enter short on a True day, then exit-band day has gate False —
    # the exit must still fire.
    z = _z([2.5, 1.5, 0.2, 0.0])
    gate = pd.Series([True, False, False, False], index=z.index)
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0, regime_gate=gate)
    assert pos.tolist() == [-1, -1, 0, 0]


def test_regime_gate_never_blocks_stop() -> None:
    z = _z([2.5, 3.0, 4.5, 3.0])
    gate = pd.Series([True, False, False, False], index=z.index)
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0, regime_gate=gate)
    assert pos.tolist() == [-1, -1, 0, 0]


def test_nan_z_holds_state() -> None:
    z = _z([0.0, 2.5, np.nan, 1.5, 0.2])
    pos = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    assert pos.tolist() == [0, -1, -1, -1, 0]


def test_trailing_only_no_future_leak() -> None:
    """Position at t must not depend on z after t."""
    z = _z([0.0, 2.5, 1.5, 0.3, 0.0])
    pos_full = run_state_machine(z, entry=2.0, exit_t=0.5, stop=4.0)
    for cut in range(1, len(z) + 1):
        pos_prefix = run_state_machine(z.iloc[:cut], entry=2.0, exit_t=0.5, stop=4.0)
        # the prefix run must agree with the full run on all shared dates
        assert pos_prefix.tolist() == pos_full.iloc[:cut].tolist()


def test_invalid_thresholds_raise() -> None:
    z = _z([0.0, 1.0])
    with pytest.raises(ValueError):
        run_state_machine(z, entry=0.5, exit_t=0.5, stop=4.0)  # entry <= exit
    with pytest.raises(ValueError):
        run_state_machine(z, entry=2.0, exit_t=0.5, stop=2.0)  # stop <= entry
