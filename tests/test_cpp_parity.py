"""V9 parity tests — Python/C++ cross-check.

Prices 20 bonds and 20 CDS via pycredit and asserts numbers match
the C++ Catch2 output (parity_dump.csv) within 1e-10.
"""

import csv
import sys
from pathlib import Path

import numpy as np
import pytest

_CREDIT_DIR = Path(__file__).resolve().parent.parent / "python" / "credit"
if str(_CREDIT_DIR) not in sys.path:
    sys.path.insert(0, str(_CREDIT_DIR))

import pycredit  # noqa: E402

REF_DIR = Path(__file__).resolve().parent.parent / "cpp" / "tests" / "ref"


def _bootstrap_discount():
    """Bootstrap the same FRED DGS curve used by the C++ parity dump."""
    csv_path = REF_DIR / "discount_curve_knots.csv"
    tenors, yields = [], []
    with open(csv_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("t"):
                continue
            parts = line.split(",")
            tenors.append(float(parts[0]))
            yields.append(float(parts[1]) / 100.0)
    return pycredit.bootstrap_discount(
        np.array(tenors, dtype=np.float64),
        np.array(yields, dtype=np.float64),
    )


def _load_parity_dump():
    """Parse parity_dump.csv into bond rows, CDS rows, and survival bootstrap inputs."""
    bonds, cdses = [], []
    surv_tenors, surv_spreads, surv_recovery = None, None, 0.4
    path = REF_DIR / "parity_dump.csv"
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("# surv_bootstrap_tenors:"):
                vals = line.split(":", 1)[1]
                surv_tenors = [float(x) for x in vals.split(",")]
            elif line.startswith("# surv_bootstrap_spreads:"):
                vals = line.split(":", 1)[1]
                surv_spreads = [float(x) for x in vals.split(",")]
            elif line.startswith("# surv_recovery:"):
                surv_recovery = float(line.split(":", 1)[1])
            elif line.startswith("#"):
                continue
            else:
                parts = line.split(",")
                if parts[0] == "bond":
                    bonds.append({
                        "coupon": float(parts[1]),
                        "frequency": int(parts[2]),
                        "maturity_years": int(parts[3]),
                        "daycount_code": int(parts[4]),
                        "price": float(parts[5]),
                        "dv01": float(parts[6]),
                        "dv01_fd": float(parts[7]),
                        "accrued": float(parts[8]),
                        "ytm": float(parts[9]),
                    })
                elif parts[0] == "cds":
                    cdses.append({
                        "maturity_years": float(parts[1]),
                        "coupon": float(parts[2]),
                        "recovery": float(parts[3]),
                        "notional": float(parts[4]),
                        "mtm": float(parts[5]),
                        "par_spread": float(parts[6]),
                        "cs01": float(parts[7]),
                        "rpv01": float(parts[8]),
                    })
    surv_info = {
        "tenors": surv_tenors,
        "spreads": surv_spreads,
        "recovery": surv_recovery,
    }
    return bonds, cdses, surv_info


def test_bond_parity():
    """20 bonds: Python vs C++ within 1e-10."""
    disc = _bootstrap_discount()
    bonds, _, _ = _load_parity_dump()
    assert len(bonds) == 20

    coupons = np.array([b["coupon"] for b in bonds], dtype=np.float64)
    freqs = np.array([b["frequency"] for b in bonds], dtype=np.int32)
    mats = np.array([b["maturity_years"] for b in bonds], dtype=np.float64)
    dccs = np.array([b["daycount_code"] for b in bonds], dtype=np.int32)

    result = pycredit.price_bonds(disc, coupons, freqs, mats, dccs)

    for i, b in enumerate(bonds):
        for field in ["price", "dv01", "dv01_fd", "accrued", "ytm"]:
            cpp_val = b[field]
            py_val = float(result[field][i])
            err = abs(py_val - cpp_val)
            tol = max(1e-10, abs(cpp_val) * 1e-12)
            assert err < tol, (
                f"bond {i} {field}: C++={cpp_val} Python={py_val} err={err}"
            )


def test_cds_parity():
    """20 CDS: Python vs C++ within 1e-10."""
    disc = _bootstrap_discount()
    _, cdses, surv_info = _load_parity_dump()
    assert len(cdses) == 20

    # Bootstrap using the exact same tenors/spreads/recovery as C++.
    surv_tenors = np.array(surv_info["tenors"], dtype=np.float64)
    surv_spreads = np.array(surv_info["spreads"], dtype=np.float64)
    surv = pycredit.bootstrap_survival(
        surv_tenors, surv_spreads, surv_info["recovery"], disc)

    mats = np.array([c["maturity_years"] for c in cdses], dtype=np.float64)
    cpns = np.array([c["coupon"] for c in cdses], dtype=np.float64)
    recs = np.array([c["recovery"] for c in cdses], dtype=np.float64)
    ntls = np.array([c["notional"] for c in cdses], dtype=np.float64)

    result = pycredit.price_cds(surv, disc, mats, cpns, recs, ntls)

    for i, c in enumerate(cdses):
        for field in ["mtm", "par_spread", "cs01", "rpv01"]:
            cpp_val = c[field]
            py_val = float(result[field][i])
            err = abs(py_val - cpp_val)
            tol = max(1e-10, abs(cpp_val) * 1e-12)
            assert err < tol, (
                f"cds {i} {field}: C++={cpp_val} Python={py_val} err={err}"
            )
