"""The apartment decision model: which criteria exist, how a listing is measured on each,
what weights the user starts from. Qualitative attributes get explicit point scales, so
the matrix stays numeric and the scale is visible to the user (Assignment 4.1)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from dss.problem import BENEFIT, COST

Listing = Dict[str, object]
Point = Optional[Tuple[float, float]]


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


# ---- point scales for text attributes -------------------------------------------------

CONDITION_SCALE = [            # (substring, points); the first match wins
    ("свеж", 5), ("евро", 5), ("дизайн", 5),
    ("аккурат", 4),
    ("средн", 3),
    ("требует", 2),
    ("предчистов", 1.5), ("white", 1.5),
    ("чернов", 1), ("свободн", 1),
]
HOUSE_SCALE = [("кирпич", 5), ("монолит", 4), ("панел", 3), ("каркас", 1), ("камыш", 1), ("ино", 2)]
FURNITURE_SCALE = {"полностью": 3, "частично": 2, "без мебели": 1}


def _scale(text: object, scale) -> Optional[float]:
    if not text:
        return None
    t = str(text).lower()
    for sub, pts in scale:
        if sub in t:
            return float(pts)
    return None


def condition_points(x: Listing) -> Optional[float]:
    return _scale(x.get("condition"), CONDITION_SCALE)


def house_points(x: Listing) -> Optional[float]:
    return _scale(x.get("house_type"), HOUSE_SCALE)


def floor_points(x: Listing) -> Optional[float]:
    """first floor 1 (noise, safety), last floor 2 (roof, heat), any middle floor 3"""
    f, n = x.get("floor"), x.get("floors")
    if not f:
        return None
    if f == 1:
        return 1.0
    if n and f >= n:
        return 2.0
    return 3.0 if n else None


def furniture_points(x: Listing) -> Optional[float]:
    if x.get("furniture") in FURNITURE_SCALE:
        return float(FURNITURE_SCALE[x["furniture"]])
    items = x.get("furniture_items")
    if items:
        return 3.0 if items >= 6 else 2.0
    return None


def _num(key: str) -> Callable[[Listing, Point], Optional[float]]:
    def get(x: Listing, _p: Point) -> Optional[float]:
        v = x.get(key)
        return float(v) if isinstance(v, (int, float)) and v > 0 else None
    return get


def _distance(x: Listing, p: Point) -> Optional[float]:
    if not p or not x.get("lat") or not x.get("lon"):
        return None
    return round(haversine_km(p, (float(x["lat"]), float(x["lon"]))), 2)


def _price_m2(x: Listing, _p: Point) -> Optional[float]:
    if x.get("price") and x.get("area"):
        return round(float(x["price"]) / float(x["area"]))
    return None


@dataclass(frozen=True)
class CriterionSpec:
    key: str
    name: str
    kind: str
    unit: str
    how: str                                   # how the value is obtained (shown in 4.1)
    get: Callable[[Listing, Point], Optional[float]]
    importance: Dict[str, int] = field(default_factory=dict)   # default 0..10 per mode; 0 = off
    modes: Tuple[str, ...] = ("sale", "rent")
    scale: str = ""                            # point scale for qualitative criteria
    fmt: str = "{:,.0f}"


CRITERIA: List[CriterionSpec] = [
    CriterionSpec("price", "Цена", COST, "₸", "Цена из объявления (для аренды — за месяц)",
                  _num("price"), {"sale": 9, "rent": 9}),
    CriterionSpec("price_m2", "Цена за м²", COST, "₸/м²", "Цена / общая площадь", _price_m2,
                  {"sale": 0, "rent": 0}),
    CriterionSpec("area", "Общая площадь", BENEFIT, "м²", "Поле «Площадь» объявления", _num("area"),
                  {"sale": 6, "rent": 4}, fmt="{:,.1f}"),
    CriterionSpec("kitchen", "Площадь кухни", BENEFIT, "м²", "«Площадь кухни» в карточке объявления",
                  _num("kitchen"), {"sale": 0, "rent": 0}, fmt="{:,.1f}"),
    CriterionSpec("distance", "Расстояние до точки", COST, "км",
                  "По прямой от координат объявления до точки, выбранной на карте (работа, учёба)",
                  _distance, {"sale": 6, "rent": 7}, fmt="{:,.1f}"),
    CriterionSpec("year", "Год постройки", BENEFIT, "год", "«Год постройки» дома", _num("year"),
                  {"sale": 4, "rent": 0}, fmt="{:.0f}"),
    CriterionSpec("condition", "Состояние (ремонт)", BENEFIT, "балл 1–5", "«Состояние квартиры» по шкале",
                  lambda x, p: condition_points(x), {"sale": 5, "rent": 0},
                  scale="свежий/евроремонт — 5, аккуратный — 4, среднее — 3, требует ремонта — 2, "
                        "предчистовая — 1.5, черновая / свободная планировка — 1", fmt="{:.1f}"),
    CriterionSpec("house", "Тип дома", BENEFIT, "балл 1–5", "«Тип дома» по шкале", lambda x, p: house_points(x),
                  {"sale": 3, "rent": 0},
                  scale="кирпичный — 5, монолитный — 4, панельный — 3, иной — 2, каркасно-камышитовый — 1",
                  fmt="{:.0f}"),
    CriterionSpec("floor", "Этаж", BENEFIT, "балл 1–3", "Этаж и этажность дома", lambda x, p: floor_points(x),
                  {"sale": 3, "rent": 3}, scale="первый — 1, последний — 2, средний — 3", fmt="{:.0f}"),
    CriterionSpec("ceiling", "Высота потолков", BENEFIT, "м", "«Высота потолков»", _num("ceiling"),
                  {"sale": 0, "rent": 0}, fmt="{:.2f}"),
    CriterionSpec("furniture", "Мебель", BENEFIT, "балл 1–3", "«Квартира меблирована» или список мебели",
                  lambda x, p: furniture_points(x), {"sale": 0, "rent": 4},
                  scale="полностью — 3, частично — 2, без мебели — 1", fmt="{:.0f}"),
    CriterionSpec("facilities", "Техника и удобства", BENEFIT, "шт.",
                  "Число пунктов в «Удобства» (интернет, стиральная машина, холодильник…)",
                  _num("facilities"), {"rent": 0}, modes=("rent",), fmt="{:.0f}"),
    CriterionSpec("rooms", "Число комнат", BENEFIT, "шт.", "Из заголовка объявления", _num("rooms"),
                  {"sale": 0, "rent": 0}, fmt="{:.0f}"),
]
BY_KEY = {c.key: c for c in CRITERIA}

# importance 0..10, normalized to weights that sum to 1. Rent presets lean on what rent search
# cards actually carry (price, area, floor, furniture, rooms); condition, year and amenities
# appear there rarely and come mostly from listing pages.
PRESETS = {
    "balanced": {"title": "Сбалансированно", "sale": {c.key: c.importance.get("sale", 0) for c in CRITERIA},
                 "rent": {c.key: c.importance.get("rent", 0) for c in CRITERIA}},
    "budget": {"title": "Экономно",
               "sale": {"price": 10, "price_m2": 6, "area": 4, "distance": 3, "condition": 3, "floor": 2},
               "rent": {"price": 10, "price_m2": 5, "area": 3, "distance": 4, "furniture": 3, "floor": 2}},
    "commute": {"title": "Ближе к работе",
                "sale": {"price": 6, "area": 4, "distance": 10, "condition": 4, "year": 2, "floor": 2},
                "rent": {"price": 6, "distance": 10, "area": 3, "furniture": 3, "floor": 2}},
    "comfort": {"title": "Комфорт",
                "sale": {"price": 4, "area": 7, "condition": 8, "house": 6, "year": 6, "ceiling": 4,
                         "floor": 4, "distance": 4},
                "rent": {"price": 4, "area": 7, "furniture": 7, "floor": 4, "distance": 5}},
    "family": {"title": "Для семьи",
               "sale": {"price": 6, "area": 8, "rooms": 6, "floor": 4, "condition": 5, "house": 4,
                        "year": 3, "distance": 3},
               "rent": {"price": 6, "area": 8, "rooms": 6, "furniture": 4, "floor": 3, "distance": 3}},
}

# a few reference points in Astana for the "distance" criterion; any map click works too
PLACES = [
    {"name": "Байтерек", "lat": 51.12825, "lon": 71.43063},
    {"name": "Хан Шатыр", "lat": 51.13240, "lon": 71.40350},
    {"name": "EXPO", "lat": 51.08960, "lon": 71.41570},
    {"name": "Назарбаев Университет", "lat": 51.09040, "lon": 71.39800},
    {"name": "Вокзал Нурлы Жол", "lat": 51.11270, "lon": 71.53270},
    {"name": "Вокзал Астана-1", "lat": 51.19590, "lon": 71.40890},
    {"name": "Аэропорт", "lat": 51.02220, "lon": 71.46690},
]
CITY_CENTER = (51.1282, 71.4306)


def specs_for(mode: str) -> List[CriterionSpec]:
    return [c for c in CRITERIA if mode in c.modes]


def describe(mode: str) -> List[dict]:
    return [{"key": c.key, "name": c.name, "kind": c.kind, "unit": c.unit, "how": c.how, "scale": c.scale,
             "importance": c.importance.get(mode, 0)} for c in specs_for(mode)]
