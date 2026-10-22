"""Step 6 and beyond: compare methods, find dominated alternatives, test how robust the leader is."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .methods import topsis, wsm
from .normalization import normalize_linear
from .problem import DecisionProblem


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 2:
        return 1.0
    ra, rb = a.rank(), b.rank()
    if ra.std() == 0 or rb.std() == 0:
        return 1.0
    return float(np.corrcoef(ra, rb)[0, 1])


def compare_rankings(wsm_result: pd.DataFrame, topsis_result: pd.DataFrame,
                     labels: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """WSM vs TOPSIS: the table of places and the three questions of the assignment."""
    label = (lambda a: labels.get(a, a)) if labels else (lambda a: a)
    t = pd.DataFrame({"wsm_score": wsm_result["score"], "wsm_rank": wsm_result["rank"],
                      "topsis_score": topsis_result["score"], "topsis_rank": topsis_result["rank"]})
    t["shift"] = t["topsis_rank"] - t["wsm_rank"]
    t = t.sort_values(["wsm_rank", "topsis_rank"])
    w_win = list(t.index[t["wsm_rank"] == 1])
    t_win = list(t.index[t["topsis_rank"] == 1])
    same_winner = bool(set(w_win) & set(t_win))
    rho = _spearman(t["wsm_rank"], t["topsis_rank"])
    moved = t[t["shift"] != 0]
    identical = moved.empty
    biggest = moved.reindex(moved["shift"].abs().sort_values(ascending=False).index).head(3)

    answers = [
        ("Одинаковый ли победитель?",
         f"Да: {label(w_win[0])} первый по обоим методам." if same_winner else
         f"Нет: по WSM лучший — {', '.join(map(label, w_win))}, по TOPSIS — {', '.join(map(label, t_win))}."),
        ("Отличаются ли рейтинги?",
         "Нет, порядок альтернатив полностью совпадает." if identical else
         f"Да, у {len(moved)} из {len(t)} альтернатив разные места; согласованность рангов "
         f"(коэффициент Спирмена) ρ = {rho:.2f}. Сильнее всего сдвинулись: "
         + "; ".join(f"{label(a)} — {int(r.wsm_rank)} → {int(r.topsis_rank)}" for a, r in biggest.iterrows()) + "."),
        ("Почему методы могут давать разные результаты?",
         "WSM складывает взвешенные оценки, нормализованные относительно лучшего значения "
         "(x/max, min/x), — сильная сторона полностью компенсирует слабую. TOPSIS использует "
         "векторную нормализацию и евклидовы расстояния до идеальной и наихудшей точек: "
         "он сильнее наказывает альтернативы, которые сильно отстают хотя бы по одному критерию, "
         "и поощряет «сбалансированные». Кроме того, при разной нормализации разброс значений "
         "критерия по-разному влияет на итог, поэтому близкие альтернативы могут поменяться местами."),
    ]
    return {"table": t, "same_winner": same_winner, "spearman": rho, "identical": identical,
            "answers": answers}


def compare_methods(results: Dict[str, pd.DataFrame], labels: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """Any number of methods side by side: places, leaders, pairwise Spearman rho."""
    label = (lambda a: labels.get(a, a)) if labels else (lambda a: a)
    names = list(results)
    t = pd.DataFrame({f"{m}_rank": r["rank"] for m, r in results.items()})
    t = t.assign(**{f"{m}_score": r["score"] for m, r in results.items()})
    t["spread"] = t[[f"{m}_rank" for m in names]].max(axis=1) - t[[f"{m}_rank" for m in names]].min(axis=1)
    t = t.sort_values([f"{m}_rank" for m in names])
    leaders = {m: list(t.index[t[f"{m}_rank"] == 1]) for m in names}
    rho = {f"{a}~{b}": _spearman(t[f"{a}_rank"], t[f"{b}_rank"])
           for i, a in enumerate(names) for b in names[i + 1:]}
    same = len({tuple(v) for v in leaders.values()}) == 1
    return {"table": t, "leaders": {m: [label(a) for a in v] for m, v in leaders.items()},
            "leader_codes": leaders, "spearman": rho, "same_leader": same}


def dominance(p: DecisionProblem) -> List[Tuple[str, str]]:
    """Pairs (dominated, dominating): not better on any criterion and strictly worse on at least one.
    A dominated alternative cannot win under any weights - Lecture 7.1, rule of alternatives."""
    norm = normalize_linear(p.matrix, p.kinds)[p.keys]     # "more is better" for every column
    vals = norm.to_numpy(dtype=float)
    idx = list(norm.index)
    out = []
    for i in range(len(idx)):
        for j in range(len(idx)):
            if i != j and np.all(vals[j] >= vals[i] - 1e-12) and np.any(vals[j] > vals[i] + 1e-12):
                out.append((idx[i], idx[j]))
                break                               # one dominator is enough to report
    return out


def _weights_at(p: DecisionProblem, key: str, t: float) -> Dict[str, float]:
    """Weight of `key` set to t, the others share 1-t in their original proportions
    (equal original weights -> the equal split used in Lecture 7.1)."""
    rest = {k: v for k, v in p.weights.items() if k != key}
    total = sum(rest.values())
    if total <= 0:
        share = {k: (1 - t) / len(rest) for k in rest} if rest else {}
    else:
        share = {k: v / total * (1 - t) for k, v in rest.items()}
    return {**share, key: t}


def sensitivity(p: DecisionProblem, key: str, method: str = "wsm", steps: int = 101,
                top: int = 6) -> Dict[str, object]:
    """Scores of the leading alternatives while the weight of one criterion goes from 0 to 1,
    and the weights at which the leader changes (thresholds)."""
    run = wsm if method == "wsm" else topsis
    grid = np.linspace(0.0, 1.0, steps)
    rows = {}
    leaders = []
    for t in grid:
        s = run(p.with_weights(_weights_at(p, key, float(t))))["result"]["score"]
        rows[round(float(t), 6)] = s
        tied = list(s.index[s >= s.max() - 1e-12])
        # on a tie the previous leader keeps the place, so equal scores do not produce fake switches
        leaders.append(leaders[-1] if leaders and leaders[-1] in tied else tied[0])
    scores = pd.DataFrame(rows).T                      # index: weight t, columns: alternatives
    current = run(p)["result"]
    keep = list(dict.fromkeys(list(current.index[:top]) + leaders))
    thresholds = []
    for i in range(1, len(grid)):
        if leaders[i] != leaders[i - 1]:
            a, b = leaders[i - 1], leaders[i]
            lo, hi = grid[i - 1], grid[i]
            for _ in range(40):                        # bisection on the score difference a - b
                mid = (lo + hi) / 2
                s = run(p.with_weights(_weights_at(p, key, mid)))["result"]["score"]
                if s[a] > s[b]:
                    lo = mid
                else:
                    hi = mid
            thresholds.append({"weight": (lo + hi) / 2, "from": a, "to": b})
    return {"criterion": key, "method": method, "scores": scores[keep], "leaders": leaders,
            "thresholds": thresholds, "current_weight": float(p.weights[key])}
