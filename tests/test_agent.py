"""The assistant's tools run the same engine as the interface; no API calls here."""
import pytest

from app import agent
from tests.test_engine import WEIGHTS, listing

CENTER = {"lat": 51.1282, "lon": 71.4306}


@pytest.fixture
def setup():
    ds = {"id": "t", "mode": "sale", "listings": [
        listing("A1", 30e6, 60, 51.13, 71.43), listing("A2", 25e6, 45, 51.20, 71.50, condition="требует ремонта"),
        listing("A3", 40e6, 80, 51.09, 71.41, house_type="кирпичный"), listing("A4", 28e6, 55, 51.15, 71.45),
        listing("A5", 35e6, 70, 51.10, 71.42, floor=9)]}
    s = agent.Session("x", req={"dataset": "t", "weights": dict(WEIGHTS), "point": CENTER, "constraints": {},
                                "missing": "worst", "sort": "wsm"})
    events = []
    return agent.Tools(ds, s, events.append), s, events


def test_ranking_and_listing(setup):
    tools, s, _ = setup
    r = tools.get_ranking(top=3)
    assert len(r["rows"]) == 3 and r["rows"][0]["wsm_rank"] == 1
    x = tools.explain_listing("a1")                     # case-insensitive code
    assert x["code"] == "A1"
    contrib = sum(c["wsm_contribution"] for c in x["criteria"])
    assert contrib == pytest.approx(x["wsm"]["score"], abs=1e-3)
    assert {"d_plus", "d_minus", "C"} <= set(x["topsis"])
    assert "error" in tools.explain_listing("A99")


def test_what_if_does_not_touch_screen(setup):
    tools, s, events = setup
    before = dict(s.req["weights"])
    r = tools.what_if(weights={"price": 10, "area": 0})
    assert s.req["weights"] == before and not events
    assert "Общая площадь" not in r["weights_what_if"]
    assert r["change_vs_screen"]["wsm_leader_after"] == r["leaders"]["wsm"][0]["code"]


def test_apply_settings_emits_event(setup):
    tools, s, events = setup
    r = tools.apply_settings(constraints={"price_max": 31e6}, summary="бюджет 31 млн")
    assert r["applied"] and r["alternatives_after_constraints"] == 3
    assert s.req["constraints"] == {"price_max": 31e6}
    assert events and events[0]["type"] == "apply" and events[0]["settings"]["constraints"]["price_max"] == 31e6
    tools.apply_settings(constraints={"price_max": None})
    assert s.req["constraints"] == {}


def test_validation_of_tool_inputs():
    assert agent._validate("explain_listing", {"code": 5})
    assert agent._validate("compare_listings", {"codes": ["A1"]})
    assert agent._validate("what_if", {"weights": {"nonsense": 3}})
    assert agent._validate("what_if", {"constraints": {"price_max": 1, "evil": 2}})
    assert agent._validate("what_if", {"weights": {"price": 3}, "point": {"lat": 51.1, "lon": 71.4}}) is None


def test_lecture_example_and_state(setup):
    tools, s, _ = setup
    ex = tools.lecture_example()
    assert ex["wsm"]["D"]["score"] == pytest.approx(0.9035, abs=1e-4)
    assert ex["topsis"]["D"]["score"] == pytest.approx(0.71, abs=1e-4)
    st = agent.ui_state(tools.res, s.req, "Подбор")
    assert st.startswith("<ui_state>") and "Топ-5 по WSM" in st
    assert tools.sensitivity("price")["scores_at_weights"]
