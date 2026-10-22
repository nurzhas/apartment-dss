"""Multi-criteria decision support core (Assignment 4.2).

Input -> Validation -> Normalization -> WSM / TOPSIS (+ WPM) -> Ranking -> comparison,
dominance and sensitivity analysis. Pure pandas/numpy, no web or scraping code here,
so the same functions serve the web app, the console demo and the tests.
"""
from .problem import Criterion, DecisionProblem
from .validation import ValidationReport, validate
from .normalization import normalize_linear, normalize_vector
from .methods import wsm, wpm, topsis
from .analysis import compare_methods, compare_rankings, dominance, sensitivity
from .ahp import ahp_weights

__all__ = ["Criterion", "DecisionProblem", "ValidationReport", "validate", "normalize_linear",
           "normalize_vector", "wsm", "wpm", "topsis", "compare_rankings", "compare_methods", "dominance", "sensitivity",
           "ahp_weights"]
