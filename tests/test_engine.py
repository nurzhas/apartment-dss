"""The apartment pipeline on synthetic listings: constraints, gaps, weights, output shape."""
import pytest

from app.criteria import condition_points, floor_points, house_points, haversine_km
from app.engine import RankRequest, fill_missing, normalize_weights, rank
from krisha.parser import parse_attrs, parse_title

CENTER = (51.1282, 71.4306)


def listing(code, price, area, lat, lon, **kw):
    x = {"code": code, "id": code, "url": f"https://krisha.kz/a/show/{code}", "title": f"{code}", "price": price,
         "area": area, "lat": lat, "lon": lon, "rooms": 2, "floor": 3, "floors": 9, "year": 2015,
         "condition": "свежий ремонт", "house_type": "монолитный"}
    x.update(kw)
    return x


@pytest.fixture
def ds():
    return {"id": "t", "mode": "sale", "listings": [
        listing("A1", 30e6, 60, 51.13, 71.43),
        listing("A2", 25e6, 45, 51.20, 71.50, condition="требует ремонта", floor=1),
        listing("A3", 40e6, 80, 51.09, 71.41, house_type="кирпичный"),
        listing("A4", 28e6, 55, 51.15, 71.45, year=None),
        listing("A5", 35e6, 70, 51.10, 71.42, floor=9),
        listing("A6", 50e6, 75, 51.12, 71.44),
    ]}


WEIGHTS = {"price": 9, "area": 6, "distance": 6, "year": 4, "condition": 5, "house": 3, "floor": 3}


def test_rank_shape(ds):
    r = rank(ds, RankRequest(weights=WEIGHTS, point=CENTER))
    assert r["validation"]["ok"], r["validation"]
    assert len(r["listings"]) == 6
    assert sum(c["weight"] for c in r["criteria"]) == pytest.approx(1.0, abs=1e-9)
    ranks = sorted(x["wsm_rank"] for x in r["listings"])
    assert ranks[0] == 1
    assert {"scores", "contrib", "ranks", "heatmap", "sensitivity"} <= set(r["charts"])
    assert r["formalization"]["task_type"]["value"] == "Полуструктурированная"
    assert len(r["comparison"]["answers"]) == 3


def test_constraints_exclude_before_ranking(ds):
    r = rank(ds, RankRequest(weights=WEIGHTS, point=CENTER, constraints={"price_max": 36e6, "not_first": True}))
    codes = {x["code"] for x in r["listings"]}
    assert codes == {"A1", "A4", "A5"}
    stats = {c["label"]: c for c in r["constraints"]}
    assert stats["Бюджет"]["excluded"] == 2 and stats["Не первый этаж"]["excluded"] == 1
    assert any("5" in w for w in r["validation"]["warnings"])        # fewer than five alternatives left


def test_missing_worst_is_reported(ds):
    r = rank(ds, RankRequest(weights=WEIGHTS, point=CENTER))
    filled = r["missing"]["filled"]
    assert filled == [{"code": "A4", "key": "year", "criterion": "Год постройки", "value": 2015.0}]
    a4 = next(x for x in r["listings"] if x["code"] == "A4")
    assert a4["imputed"] == ["year"]


def test_missing_drop(ds):
    r = rank(ds, RankRequest(weights=WEIGHTS, point=CENTER, missing="drop"))
    assert "A4" not in {x["code"] for x in r["listings"]}


def test_dominated_listing_found(ds):
    # A6 is the dearest and not better than A3 anywhere except distance; make it strictly worse
    ds["listings"][5].update(lat=51.09, lon=71.41, area=70, house_type="монолитный")
    r = rank(ds, RankRequest(weights=WEIGHTS, point=CENTER))
    assert any(d["code"] == "A6" for d in r["dominance"])


def test_only_price_weight_ranks_by_price(ds):
    r = rank(ds, RankRequest(weights={"price": 1}, point=CENTER))
    order = [x["code"] for x in sorted(r["listings"], key=lambda x: x["wsm_rank"])]
    assert order[:3] == ["A2", "A4", "A1"]


def test_normalize_weights_sums_to_one():
    w = normalize_weights({"a": 3, "b": 3, "c": 3}, ["a", "b", "c"])
    assert sum(w.values()) == 1.0


def test_scales():
    assert condition_points({"condition": "не новый, но аккуратный ремонт"}) == 4
    assert condition_points({"condition": "черновая отделка"}) == 1
    assert house_points({"house_type": "кирпичный"}) == 5
    assert floor_points({"floor": 1, "floors": 9}) == 1
    assert floor_points({"floor": 9, "floors": 9}) == 2
    assert floor_points({"floor": 4, "floors": 9}) == 3
    assert haversine_km((51.1282, 71.4306), (51.0896, 71.4157)) == pytest.approx(4.4, abs=0.2)


def test_card_parsing():
    assert parse_title("2-комнатная квартира · 66.4 м² · 1/9 этаж") == {"rooms": 2, "area": 66.4, "floor": 1, "floors": 9}
    a = parse_attrs("жил. комплекс Магистральный, монолитный дом, 2015 г.п., состояние: не новый, но аккуратный "
                    "ремонт, потолки 2.7м., санузел совмещенный, меблирована полностью, Продаю, звоните 8 777 ...,")
    assert a == {"complex": "Магистральный", "house_type": "монолитный", "year": 2015,
                 "condition": "не новый, но аккуратный ремонт", "ceiling": 2.7, "furniture": "полностью"}


def test_address_queries():
    from krisha.geocode import address_queries, district_point
    q = address_queries({"address": "Алматы р-н, Б. Момышулы 15/2 — К.Сатпаева", "complex": "Магистральный"})
    assert q == [("Момышулы 15/2, Астана", "house"), ("Момышулы, Астана", "street"),
                 ("Магистральный, Астана", "complex")]
    assert address_queries({"address": "Нура р-н, Е 797 2 — Гринлайн"})[0] == ("Е-797 2, Астана", "house")
    assert district_point({"district": "Есильский р-н"}) is not None
