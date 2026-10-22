"""Reference problems with known answers."""
from .problem import BENEFIT, COST, Criterion, DecisionProblem


def lecture_suppliers() -> DecisionProblem:
    """Lecture 7.1: choosing a supplier, four criteria with equal weights.
    Known answers: WSM D 0.9035, TOPSIS D 0.7100."""
    criteria = [
        Criterion("price", "Цена", COST, "тыс. ₸"),
        Criterion("quality", "Качество", BENEFIT, "балл 1–10"),
        Criterion("term", "Срок поставки", COST, "дни"),
        Criterion("reliability", "Надёжность", BENEFIT, "балл 1–10"),
    ]
    values = [[100, 8, 5, 9], [90, 7, 4, 8], [110, 9, 6, 9], [95, 8, 3, 7]]
    return DecisionProblem.from_lists(["A", "B", "C", "D"], criteria, values,
                                      {c.key: 0.25 for c in criteria})
