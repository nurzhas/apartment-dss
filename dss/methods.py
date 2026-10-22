"""Steps 4-5: aggregation methods. Each returns every intermediate table, so the
interface can show the calculation, not only the answer."""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from .normalization import normalize_linear, normalize_vector
from .problem import BENEFIT, DecisionProblem


def _rank(score: pd.Series, ascending: bool = False) -> pd.Series:
    # equal scores share a place ("min" method): 1, 2, 2, 4
    return score.rank(ascending=ascending, method="min").astype(int)


def wsm(p: DecisionProblem) -> Dict[str, pd.DataFrame]:
    """Weighted Sum Model: S_i = sum_j w_j * r_ij on the linear normalization."""
    norm = normalize_linear(p.matrix, p.kinds)
    w = p.weight_vector()
    contrib = norm[p.keys].mul(w, axis=1)            # how much each criterion added to the score
    score = contrib.sum(axis=1)
    table = pd.DataFrame({"score": score, "rank": _rank(score)}).sort_values("rank")
    return {"normalized": norm, "contribution": contrib, "result": table}


def wpm(p: DecisionProblem) -> Dict[str, pd.DataFrame]:
    """Weighted Product Model: P_i = prod_j r_ij ^ w_j (a weak value is compensated less than in WSM)."""
    norm = normalize_linear(p.matrix, p.kinds)
    w = p.weight_vector()
    factors = norm[p.keys].clip(lower=1e-12) ** w   # r^w: 1 - no loss on this criterion, below 1 - the score shrinks
    score = factors.prod(axis=1)
    table = pd.DataFrame({"score": score, "rank": _rank(score)}).sort_values("rank")
    return {"normalized": norm, "factors": factors, "result": table}


def topsis(p: DecisionProblem) -> Dict[str, pd.DataFrame]:
    """TOPSIS: closeness C_i = d-_i / (d+_i + d-_i) to the ideal point in the weighted vector-normalized space."""
    norm = normalize_vector(p.matrix, p.keys)
    weighted = norm.mul(p.weight_vector(), axis=1)
    benefit = {k: p.kinds[k] == BENEFIT for k in p.keys}
    ideal = pd.Series({k: weighted[k].max() if benefit[k] else weighted[k].min() for k in p.keys})
    worst = pd.Series({k: weighted[k].min() if benefit[k] else weighted[k].max() for k in p.keys})
    d_plus = np.sqrt(((weighted - ideal) ** 2).sum(axis=1))
    d_minus = np.sqrt(((weighted - worst) ** 2).sum(axis=1))
    denom = d_plus + d_minus
    closeness = (d_minus / denom).where(denom > 0, 0.5)   # all alternatives identical -> undecided
    table = pd.DataFrame({"d_plus": d_plus, "d_minus": d_minus, "score": closeness,
                          "rank": _rank(closeness)}).sort_values("rank")
    return {"normalized": norm, "weighted": weighted,
            "ideal": pd.DataFrame({"ideal_best": ideal, "ideal_worst": worst}).T, "result": table}
