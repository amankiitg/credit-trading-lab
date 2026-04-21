"""Numpy dtype aliases for pycredit structured arrays."""

import numpy as np

# Bond pricing result dtype — matches pycredit.price_bonds output.
bond_dtype = np.dtype([
    ("price",   np.float64),
    ("dv01",    np.float64),
    ("dv01_fd", np.float64),
    ("accrued", np.float64),
    ("ytm",     np.float64),
])

# CDS pricing result dtype — matches pycredit.price_cds output.
cds_dtype = np.dtype([
    ("mtm",        np.float64),
    ("par_spread", np.float64),
    ("cs01",       np.float64),
    ("rpv01",      np.float64),
])
