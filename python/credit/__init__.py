"""Python facade over the libcredit C++ pricing core."""

from __future__ import annotations

import os as _os
import sys as _sys

_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _PKG_DIR not in _sys.path:
    _sys.path.insert(0, _PKG_DIR)

import pycredit  # noqa: E402,F401  (re-exported)
from ._types import bond_dtype, cds_dtype  # noqa: E402,F401

__all__ = ["pycredit", "bond_dtype", "cds_dtype"]
