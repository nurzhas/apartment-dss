"""Search on krisha.kz: build the filter URL, walk the result pages, then open each listing
for coordinates and the full parameter table."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode

from .client import Blocked, FetchError, KrishaClient
from .geocode import Geocoder
from .parser import parse_detail, parse_search

SECTIONS = {"sale": "/prodazha/kvartiry/astana/", "rent": "/arenda/kvartiry/astana/"}
SEARCH_TTL = 30 * 60          # result pages change fast
DETAIL_TTL = 7 * 24 * 3600    # a listing page changes rarely; the price comes fresh from the search card


@dataclass
class SearchQuery:
    mode: str = "sale"                       # sale | rent
    rooms: List[int] = field(default_factory=list)   # 5 means "5 and more"
    price_from: Optional[int] = None
    price_to: Optional[int] = None
    area_from: Optional[float] = None
    year_from: Optional[int] = None
    not_first: bool = False
    not_last: bool = False
    with_photo: bool = True
    pages: int = 2
    details: bool = False                    # open listing pages (exact coordinates, more parameters)
    max_details: int = 40
    only_flats: bool = True                  # skip developer offers "от N ₸" without a concrete flat

    def path(self, page: int = 1) -> str:
        q: List[tuple] = []
        for r in sorted(set(self.rooms)):
            q.append(("das[live.rooms][]", "5.100" if r >= 5 else str(r)))
        if self.price_from:
            q.append(("das[price][from]", int(self.price_from)))
        if self.price_to:
            q.append(("das[price][to]", int(self.price_to)))
        if self.area_from:
            q.append(("das[live.square][from]", int(self.area_from)))
        if self.year_from and self.mode == "sale":
            q.append(("das[house.year][from]", int(self.year_from)))
        if self.not_first:
            q.append(("das[floor_not_first]", 1))
        if self.not_last:
            q.append(("das[floor_not_last]", 1))
        if self.with_photo:
            q.append(("das[_sys.hasphoto]", 1))
        if page > 1:
            q.append(("page", page))
        qs = urlencode(q, safe="[]")
        return SECTIONS[self.mode] + ("?" + qs if qs else "")


Progress = Callable[[Dict[str, object]], None]


def collect(client: KrishaClient, query: SearchQuery, progress: Progress = lambda p: None,
            cancel: Optional[threading.Event] = None, geocoder: Optional[Geocoder] = None) -> Dict[str, object]:
    cancel = cancel or threading.Event()
    listings: Dict[str, Dict[str, object]] = {}
    skipped = {"layout": 0, "period": 0}
    total = None
    problems: List[str] = []

    state = {"stage": "search", "done": 0, "total": query.pages}

    def waiting(pause: float, why: str) -> None:
        progress({**state, "message": f"Krisha.kz просит реже ({why}) — пауза {pause:.0f} с"})

    for page in range(1, max(1, query.pages) + 1):
        if cancel.is_set():
            break
        state.update(stage="search", done=page - 1, total=query.pages)
        progress({**state, "message": f"Страница поиска {page} из {query.pages}"})
        try:
            res = parse_search(client.get(query.path(page), SEARCH_TTL, waiting, cancel))
        except FetchError as e:
            problems.append(str(e))
            break
        total = res["total"] if total is None else total
        for c in res["cards"]:
            if query.only_flats and c["is_layout"]:
                skipped["layout"] += 1
                continue
            if query.mode == "rent" and c.get("price_period") not in ("month", None):
                skipped["period"] += 1          # daily / hourly rent is a different market
                continue
            c["mode"] = query.mode
            listings.setdefault(str(c["id"]), c)
        if not res["has_next"]:
            break

    items = list(listings.values())
    # listing pages: always from the cache when there; from the site only on request, and only
    # until the bot check answers
    fetch = query.details
    for i, item in enumerate(items):
        if cancel.is_set():
            break
        path = _detail_path(item["id"])
        body = client.cached(path, DETAIL_TTL)
        if body is None and fetch and i < query.max_details:
            state.update(stage="details", done=i, total=min(len(items), query.max_details))
            progress({**state, "message": f"Страница объявления {i + 1} из {state['total']}"})
            try:
                body = client.get(path, DETAIL_TTL, waiting, cancel)
            except Blocked as e:
                problems.append(str(e))
                fetch = False
            except FetchError as e:
                item["detail_error"] = str(e)
        if body:
            detail = parse_detail(body)
            item.update({k: v for k, v in detail.items() if v is not None})
            item["detail_loaded"] = True
            if detail.get("lat"):
                item["geo"] = "krisha"
            if not item.get("photo") and item.get("photo_detail"):
                item["photo"] = item["photo_detail"]

    # the rest get coordinates from the address on the card
    need = [x for x in items if not x.get("lat")]
    if need and geocoder is not None:
        for i, item in enumerate(need):
            if cancel.is_set():
                break
            state.update(stage="geocode", done=i, total=len(need))
            progress({**state, "message": f"Координаты по адресу: {i + 1} из {len(need)} (OpenStreetMap)"})
            pt, precision, _ = geocoder.locate(item)
            if pt:
                item["lat"], item["lon"], item["geo"] = pt[0], pt[1], precision

    geo = {}
    for x in items:
        geo[x.get("geo") or "none"] = geo.get(x.get("geo") or "none", 0) + 1
    progress({"stage": "done", "done": 1, "total": 1, "message": "Готово"})
    return {"listings": items, "total_on_site": total, "skipped": skipped, "problems": problems, "geo": geo,
            "query": query.__dict__, "source_url": "https://krisha.kz" + query.path(1)}


def _detail_path(aid) -> str:
    return f"/a/show/{aid}"
