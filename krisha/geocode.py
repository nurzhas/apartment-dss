"""Coordinates for a listing from its address (OpenStreetMap Nominatim).

Listing pages of krisha.kz carry exact coordinates but sit behind a bot check, so the
address from the search card is geocoded instead: house -> street -> residential complex
-> district centre. Every listing keeps the precision it got, and the interface shows it.
Nominatim policy is respected: one request per second, an application User-Agent, a
permanent cache."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "apartment-dss/1.0 (university course project: apartment selection in Astana)"
ASTANA_BOX = (71.15, 51.30, 71.75, 50.95)            # left, top, right, bottom
# boundary centroids of the city districts (Nominatim, 2026)
DISTRICTS = {
    "есиль": (51.0592, 71.4106), "алматы": (51.1551, 71.5473), "сарыарка": (51.1988, 71.3227),
    "байконур": (51.2265, 71.4581), "нура": (51.1009, 71.3160), "сарайшык": (51.1127, 71.5659),
}
PRECISION = {"house": "дом", "street": "улица", "complex": "ЖК", "district": "район", "krisha": "точно"}
# OSM address types accepted for each kind of query
HOUSE_TYPES = {"building", "house", "construction", "apartments"}
STREET_TYPES = {"road", "residential", "living_street", "tertiary", "secondary", "primary", "unclassified",
                "pedestrian", "service"}
AREA_TYPES = {"residential", "neighbourhood", "quarter", "suburb"}


def _clean_street(s: str) -> str:
    s = s.split(" — ")[0].strip()
    s = re.sub(r"^(ул\.|улица|пр\.|пр-т|проспект|мкр\.?|микрорайон)\s*", "", s, flags=re.I)
    s = re.sub(r"^(?:[А-ЯA-Z]\.\s*){1,2}(?=[А-ЯA-Z])", "", s)          # "Б. Момышулы" -> "Момышулы"
    s = re.sub(r"^Е\s?-?\s?(\d+)", r"Е-\1", s)                         # "Е 797 2" -> "Е-797 2"
    return re.sub(r"\s+", " ", s).strip(" ,")


def address_queries(x: Dict[str, object]) -> List[Tuple[str, str]]:
    """Queries from the most to the least precise, with the precision each one would give."""
    parts = [p.strip() for p in str(x.get("address") or "").split(",") if p.strip()]
    if parts and "р-н" in parts[0]:
        parts = parts[1:]
    if len(parts) >= 2 and re.fullmatch(r"\d+[\w/.-]*", parts[-1]):     # "Бухар жырау, 31"
        parts = parts[:-2] + [f"{parts[-2]} {parts[-1]}"]
    out: List[Tuple[str, str]] = []
    if parts:
        street = _clean_street(parts[-1])
        if re.search(r"\d", street):
            out.append((f"{street}, Астана", "house"))
        street_only = re.sub(r"\s+\d+[\w/.-]*$", "", street).strip()
        if street_only and len(street_only) > 3 and street_only != street:
            out.append((f"{street_only}, Астана", "street"))
    if x.get("complex"):
        out.append((f"{x['complex']}, Астана", "complex"))
    return out


def district_point(x: Dict[str, object]) -> Optional[Tuple[float, float]]:
    d = str(x.get("district") or x.get("address") or "").lower()
    for key, pt in DISTRICTS.items():
        if key in d:
            return pt
    return None


class Geocoder:
    def __init__(self, cache_path: Path, delay: float = 1.1):
        self.delay = delay
        self._last = 0.0
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(cache_path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS geocode (query TEXT PRIMARY KEY, lat REAL, lon REAL, "
                         "kind TEXT, fetched REAL)")
        self._db.commit()
        self._http = httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"})

    def close(self) -> None:
        self._http.close()
        self._db.close()

    def lookup(self, query: str) -> Tuple[Optional[Tuple[float, float]], Optional[str], bool]:
        """(point, OSM address type, from cache). Misses are cached too, so a failed address is asked once."""
        with self._lock:
            row = self._db.execute("SELECT lat, lon, kind FROM geocode WHERE query = ?", (query,)).fetchone()
        if row:
            return ((row[0], row[1]) if row[0] is not None else None), row[2], True
        with self._lock:
            wait = self._last + self.delay - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                r = self._http.get(NOMINATIM, params={
                    "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "kz",
                    "viewbox": ",".join(map(str, ASTANA_BOX)), "bounded": 1})
                res = r.json() if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                res = None
            self._last = time.time()
        if res is None:                       # network trouble: do not cache, try again next time
            return None, None, False
        pt, kind = None, None
        if res:
            pt, kind = (float(res[0]["lat"]), float(res[0]["lon"])), res[0].get("addresstype")
        with self._lock:
            self._db.execute("REPLACE INTO geocode VALUES (?, ?, ?, ?, ?)",
                             (query, pt[0] if pt else None, pt[1] if pt else None, kind, time.time()))
            self._db.commit()
        return pt, kind, False

    def locate(self, x: Dict[str, object]) -> Tuple[Optional[Tuple[float, float]], Optional[str], int]:
        """Best point for a listing, its precision key and how many network requests it took."""
        asked = 0
        for query, precision in address_queries(x):
            pt, kind, cached = self.lookup(query)
            asked += 0 if cached else 1
            if not pt:
                continue
            # Nominatim falls back to the city itself, a highway or a shop with a similar name - not an address
            if kind in HOUSE_TYPES and precision in ("house", "complex"):
                return pt, precision, asked
            if kind in STREET_TYPES and precision in ("house", "street"):
                return pt, "street", asked
            if kind in AREA_TYPES and precision == "complex":
                return pt, precision, asked
        pt = district_point(x)
        return (pt, "district", asked) if pt else (None, None, asked)
