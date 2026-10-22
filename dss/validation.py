"""Step 2 of the pipeline: is the decision matrix fit for ranking at all?"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

from .problem import BENEFIT, COST, DecisionProblem

WEIGHT_TOLERANCE = 1e-6


@dataclass
class ValidationReport:
    errors: List[str] = field(default_factory=list)      # ranking is impossible
    warnings: List[str] = field(default_factory=list)    # ranking is possible, but read it with care

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def validate(p: DecisionProblem) -> ValidationReport:
    r = ValidationReport()
    m = p.matrix
    keys = p.keys

    # criteria and weights describe the same set
    if len(set(keys)) != len(keys):
        r.errors.append("Ключи критериев повторяются.")
    missing_cols = [k for k in keys if k not in m.columns]
    if missing_cols:
        r.errors.append("В матрице нет столбцов для критериев: " + ", ".join(missing_cols))
    extra = [c for c in m.columns if c not in keys]
    if extra:
        r.warnings.append("Столбцы без описания критерия не участвуют в расчёте: " + ", ".join(map(str, extra)))
    bad_kind = [c.key for c in p.criteria if c.kind not in (BENEFIT, COST)]
    if bad_kind:
        r.errors.append("Тип критерия должен быть benefit или cost: " + ", ".join(bad_kind))
    no_weight = [k for k in keys if k not in p.weights]
    if no_weight:
        r.errors.append("Нет весов для критериев: " + ", ".join(no_weight))
    if r.errors:
        return r

    # alternatives
    if len(m.index) < 2:
        r.errors.append("Нужно минимум две альтернативы — иначе выбирать не из чего.")
    if m.index.duplicated().any():
        r.errors.append("Названия альтернатив повторяются: " + ", ".join(map(str, m.index[m.index.duplicated()])))

    # values: numeric, complete
    sub = m[keys]
    non_numeric = [k for k in keys if not pd.api.types.is_numeric_dtype(sub[k])]
    if non_numeric:
        r.errors.append("Нечисловые значения в критериях: " + ", ".join(non_numeric))
    else:
        nan = sub.isna()
        if nan.values.any():
            for alt in sub.index[nan.any(axis=1)]:
                cols = [k for k in keys if nan.at[alt, k]]
                r.errors.append(f"У альтернативы «{alt}» нет значений: " + ", ".join(cols))
        if np.isinf(sub.to_numpy(dtype=float)).any():
            r.errors.append("В матрице есть бесконечные значения.")
        for c in p.criteria:
            col = sub[c.key]
            if col.nunique(dropna=True) == 1:
                r.warnings.append(f"«{c.name}» одинаков у всех альтернатив и не влияет на рейтинг.")
            if c.kind == COST and (col <= 0).any():
                r.warnings.append(f"«{c.name}»: есть нулевые или отрицательные значения — "
                                  "для нормализации min/x используется min-max.")

    # weights
    w = p.weight_vector()
    if (w < 0).any():
        r.errors.append("Веса не могут быть отрицательными.")
    total = float(w.sum())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        r.errors.append(f"Сумма весов должна быть равна 1, сейчас {total:.4f}.")
    if (w == 0).any():
        r.warnings.append("Критерии с нулевым весом не влияют на рейтинг: "
                          + ", ".join(p.criterion(k).name for k in w.index[w == 0]))
    return r
