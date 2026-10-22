"""AHP (Saaty): criterion weights from pairwise comparisons, with the consistency check."""
from __future__ import annotations

from typing import Dict, Sequence

import numpy as np

# random consistency index for n = 1..10 (Saaty)
RI = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}


def ahp_weights(keys: Sequence[str], matrix: Sequence[Sequence[float]]) -> Dict[str, object]:
    """matrix[i][j] - how much criterion i is more important than j on the 1..9 scale
    (reciprocals below the diagonal). Weights by the row geometric mean, as in Lecture 7.1."""
    a = np.asarray(matrix, dtype=float)
    n = len(keys)
    if a.shape != (n, n):
        raise ValueError("pairwise matrix must be n x n")
    if not np.allclose(a * a.T, 1.0, atol=1e-6):
        raise ValueError("pairwise matrix must be reciprocal: a[j][i] = 1 / a[i][j]")
    gm = np.prod(a, axis=1) ** (1.0 / n)
    w = gm / gm.sum()
    lam = float(np.mean((a @ w) / w))
    ci = (lam - n) / (n - 1) if n > 2 else 0.0
    ri = RI.get(n, 1.49)
    cr = ci / ri if ri > 0 else 0.0
    return {"weights": dict(zip(keys, map(float, w))), "lambda_max": lam, "ci": ci, "cr": cr,
            "consistent": cr <= 0.10}
