"""Step 3: bring criteria measured in tenge, m², km and points to one comparable scale.

Two schemes, as in Lecture 7.1:
  linear (for WSM and WPM)   benefit: x / max(x)          cost: min(x) / x
                             -> every column becomes "more is better", the best value is 1;
  vector (for TOPSIS)        x / sqrt(sum(x^2))           directions are kept, the ideal
                             point later takes max for benefit and min for cost.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from .problem import BENEFIT


def _minmax(col: pd.Series, benefit: bool) -> pd.Series:
    lo, hi = col.min(), col.max()
    if hi == lo:
        return pd.Series(1.0, index=col.index)
    return (col - lo) / (hi - lo) if benefit else (hi - col) / (hi - lo)


def normalize_linear(matrix: pd.DataFrame, kinds: Dict[str, str]) -> pd.DataFrame:
    out = {}
    for key, kind in kinds.items():
        col = matrix[key].astype(float)
        if kind == BENEFIT:
            mx = col.max()
            out[key] = col / mx if mx > 0 else _minmax(col, True)
        else:
            # min/x needs strictly positive values (a price or a distance); otherwise fall back to min-max
            out[key] = col.min() / col if (col > 0).all() else _minmax(col, False)
    return pd.DataFrame(out, index=matrix.index)


def normalize_vector(matrix: pd.DataFrame, keys) -> pd.DataFrame:
    out = {}
    for key in keys:
        col = matrix[key].astype(float)
        norm = float(np.sqrt((col ** 2).sum()))
        out[key] = col / norm if norm > 0 else col * 0.0
    return pd.DataFrame(out, index=matrix.index)
