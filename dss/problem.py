"""The decision problem: alternatives x criteria matrix, criterion types and weights."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import pandas as pd

BENEFIT, COST = "benefit", "cost"


@dataclass(frozen=True)
class Criterion:
    key: str
    name: str
    kind: str                 # "benefit" - more is better, "cost" - less is better
    unit: str = ""
    description: str = ""


@dataclass
class DecisionProblem:
    """matrix: rows are alternatives (index = their names), columns are criterion keys."""
    matrix: pd.DataFrame
    criteria: List[Criterion]
    weights: Dict[str, float]
    labels: Dict[str, str] = field(default_factory=dict)   # alternative id -> human readable name

    @classmethod
    def from_lists(cls, alternatives: Sequence[str], criteria: Sequence[Criterion],
                   values: Sequence[Sequence[float]], weights: Dict[str, float]) -> "DecisionProblem":
        frame = pd.DataFrame(list(values), index=list(alternatives), columns=[c.key for c in criteria])
        return cls(frame, list(criteria), dict(weights))

    @property
    def keys(self) -> List[str]:
        return [c.key for c in self.criteria]

    @property
    def kinds(self) -> Dict[str, str]:
        return {c.key: c.kind for c in self.criteria}

    def weight_vector(self) -> pd.Series:
        return pd.Series({k: float(self.weights[k]) for k in self.keys})

    def with_weights(self, weights: Dict[str, float]) -> "DecisionProblem":
        return DecisionProblem(self.matrix, self.criteria, dict(weights), self.labels)

    def criterion(self, key: str) -> Criterion:
        return next(c for c in self.criteria if c.key == key)
