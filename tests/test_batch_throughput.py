"""V9 throughput benchmarks — C16 criterion.

Release build required for meaningful timings.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

# Ensure pycredit is importable from the source tree.
_CREDIT_DIR = Path(__file__).resolve().parent.parent / "python" / "credit"
if str(_CREDIT_DIR) not in sys.path:
    sys.path.insert(0, str(_CREDIT_DIR))

import pycredit  # noqa: E402


def _bootstrap_discount():
    """Bootstrap the FRED DGS 2025-01-02 discount curve."""
    tenors = np.array([1, 2, 3, 5, 7, 10, 20, 30], dtype=np.float64)
    yields = np.array(
        [0.0417, 0.0425, 0.0429, 0.0438, 0.0447, 0.0457, 0.0486, 0.0479],
        dtype=np.float64,
    )
    return pycredit.bootstrap_discount(tenors, yields)


def test_bond_10k_per_sec():
    """C16: 10,000 bonds priced in ≤ 1.00 s (Release build)."""
    disc = _bootstrap_discount()

    rng = np.random.default_rng(42)
    n = 10_000
    coupons = rng.uniform(0.01, 0.10, size=n).astype(np.float64)
    frequencies = rng.choice([1, 2], size=n).astype(np.int32)
    maturity_years = rng.uniform(1.0, 30.0, size=n).astype(np.float64)
    day_count_codes = np.full(n, 2, dtype=np.int32)  # 30/360

    # Warm-up (JIT, cache, etc.)
    _ = pycredit.price_bonds(disc, coupons[:10], frequencies[:10],
                              maturity_years[:10], day_count_codes[:10])

    t0 = time.perf_counter()
    result = pycredit.price_bonds(disc, coupons, frequencies,
                                   maturity_years, day_count_codes)
    elapsed = time.perf_counter() - t0

    # Sanity: correct shape and dtype.
    assert len(result) == n
    assert result["price"].dtype == np.float64
    assert np.all(np.isfinite(result["price"]))
    assert np.all(result["price"] > 0)

    print(f"\n  Bond 10k: {elapsed:.3f}s ({n / elapsed:.0f} bonds/sec)")
    assert elapsed <= 1.0, f"bond batch took {elapsed:.3f}s, limit is 1.00s"


def test_cds_10k():
    """C16: 10,000 CDS priced in ≤ 2.00 s (Release build)."""
    disc = _bootstrap_discount()

    # Bootstrap a survival curve from flat 100 bps hazard.
    surv_tenors = np.array([0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0],
                           dtype=np.float64)
    surv_spreads = np.array([0.006] * 7, dtype=np.float64)
    surv = pycredit.bootstrap_survival(surv_tenors, surv_spreads, 0.4, disc)

    rng = np.random.default_rng(42)
    n = 10_000
    maturity_years = rng.uniform(0.5, 10.0, size=n).astype(np.float64)
    coupons = rng.uniform(0.003, 0.012, size=n).astype(np.float64)
    recoveries = np.full(n, 0.4, dtype=np.float64)
    notionals = np.full(n, 10_000_000.0, dtype=np.float64)

    # Warm-up.
    _ = pycredit.price_cds(surv, disc, maturity_years[:10], coupons[:10],
                            recoveries[:10], notionals[:10])

    t0 = time.perf_counter()
    result = pycredit.price_cds(surv, disc, maturity_years, coupons,
                                 recoveries, notionals)
    elapsed = time.perf_counter() - t0

    assert len(result) == n
    assert result["mtm"].dtype == np.float64
    assert np.all(np.isfinite(result["mtm"]))

    print(f"\n  CDS 10k: {elapsed:.3f}s ({n / elapsed:.0f} CDS/sec)")
    assert elapsed <= 2.0, f"CDS batch took {elapsed:.3f}s, limit is 2.00s"
