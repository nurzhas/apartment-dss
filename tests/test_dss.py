"""The core must reproduce the worked example of Lecture 7.1 (suppliers A-D)."""
import numpy as np
import pandas as pd
import pytest

from dss import (Criterion, DecisionProblem, ahp_weights, compare_rankings, dominance, sensitivity,
                 topsis, validate, wpm, wsm)
from dss.examples import lecture_suppliers


@pytest.fixture
def p():
    return lecture_suppliers()


def test_validation_passes(p):
    r = validate(p)
    assert r.ok, r.errors


def test_wsm_matches_lecture(p):
    res = wsm(p)["result"]
    assert list(res.index) == ["D", "B", "A", "C"]
    assert res.loc["D", "score"] == pytest.approx(0.9035, abs=1e-4)
    assert res.loc["B", "score"] == pytest.approx(0.8542, abs=1e-4)
    assert res.loc["A", "score"] == pytest.approx(0.8472, abs=1e-4)
    assert res.loc["C", "score"] == pytest.approx(0.8295, abs=1e-4)


def test_wsm_normalization(p):
    n = wsm(p)["normalized"]
    assert n.loc["B", "price"] == pytest.approx(1.0)            # cheapest, min/x
    assert n.loc["C", "quality"] == pytest.approx(1.0)          # best quality, x/max
    assert n.loc["A", "term"] == pytest.approx(3 / 5)


def test_topsis_matches_lecture(p):
    out = topsis(p)
    res = out["result"]
    assert list(res.index) == ["D", "B", "A", "C"]
    for alt, c, dp, dm in [("A", 0.4397, 0.05752, 0.04514), ("B", 0.5834, 0.04385, 0.06141),
                           ("C", 0.3384, 0.08472, 0.04334), ("D", 0.7100, 0.03451, 0.08451)]:
        assert res.loc[alt, "score"] == pytest.approx(c, abs=1e-4)
        assert res.loc[alt, "d_plus"] == pytest.approx(dp, abs=1e-5)
        assert res.loc[alt, "d_minus"] == pytest.approx(dm, abs=1e-5)
    # vector normalization denominators from the lecture
    m = p.matrix
    for key, denom in [("price", 198.0530), ("quality", 16.0624), ("term", 9.2736), ("reliability", 16.5831)]:
        assert m.loc["A", key] / out["normalized"].loc["A", key] == pytest.approx(denom, abs=1e-3)


def test_wpm_matches_lecture(p):
    res = wpm(p)["result"]
    assert list(res.index) == ["D", "B", "A", "C"]
    assert res.loc["D", "score"] == pytest.approx(0.8996, abs=1e-4)
    assert res.loc["C", "score"] == pytest.approx(0.7998, abs=1e-4)


def test_ahp_matches_lecture(p):
    third, half = 1 / 3, 1 / 2
    a = ahp_weights(p.keys, [[1, third, 1, half], [3, 1, 3, 2], [1, third, 1, half], [2, half, 2, 1]])
    w = a["weights"]
    assert w["price"] == pytest.approx(0.1411, abs=1e-4)
    assert w["quality"] == pytest.approx(0.4550, abs=1e-4)
    assert w["reliability"] == pytest.approx(0.2627, abs=1e-4)
    assert a["consistent"] and a["cr"] < 0.10
    # with AHP weights the quality leader C wins the WSM
    res = wsm(p.with_weights(w))["result"]
    assert list(res.index) == ["C", "D", "A", "B"]
    assert res.loc["C", "score"] == pytest.approx(0.9038, abs=1e-4)


def test_sensitivity_threshold(p):
    s = sensitivity(p, "reliability", "wsm", steps=21)
    assert len(s["thresholds"]) == 1
    th = s["thresholds"][0]
    assert (th["from"], th["to"]) == ("D", "A")
    assert th["weight"] == pytest.approx(0.4016, abs=5e-4)
    assert s["scores"].loc[0.55, "A"] == pytest.approx(0.9083, abs=1e-4)


def test_compare_same_winner(p):
    c = compare_rankings(wsm(p)["result"], topsis(p)["result"])
    assert c["same_winner"] and c["identical"]
    assert c["spearman"] == pytest.approx(1.0)


def test_dominance_found():
    crit = [Criterion("price", "Цена", "cost"), Criterion("area", "Площадь", "benefit")]
    p = DecisionProblem.from_lists(["X", "Y", "Z"], crit, [[100, 50], [120, 45], [90, 40]],
                                   {"price": 0.5, "area": 0.5})
    assert dominance(p) == [("Y", "X")]          # Y is dearer and smaller than X


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.matrix.__setitem__("price", [100, np.nan, 110, 95]), "нет значений"),
    (lambda p: p.weights.__setitem__("price", 0.5), "Сумма весов"),
    (lambda p: p.matrix.__setitem__("quality", ["8", "7", "9", "x"]), "Нечисловые"),
])
def test_validation_errors(p, mutate, message):
    p.matrix = p.matrix.copy()
    mutate(p)
    r = validate(p)
    assert not r.ok
    assert any(message in e for e in r.errors)


def test_validation_missing_criterion(p):
    p.matrix = p.matrix.drop(columns=["term"])
    r = validate(p)
    assert not r.ok and "term" in r.errors[0]


def test_wpm_factors_multiply_to_score(p):
    out = wpm(p)
    prod = out["factors"].prod(axis=1)
    assert prod["D"] == pytest.approx(out["result"].loc["D", "score"])
    assert out["factors"].loc["B", "price"] == pytest.approx(1.0)        # the cheapest: r = 1, so r^w = 1


def test_compare_three_methods(p):
    from dss import compare_methods
    c = compare_methods({"wsm": wsm(p)["result"], "topsis": topsis(p)["result"], "wpm": wpm(p)["result"]})
    assert c["same_leader"] and c["leader_codes"]["wpm"] == ["D"]
    assert c["spearman"]["wsm~wpm"] == pytest.approx(1.0)
