"""Turn krisha.kz HTML into plain dicts. Text values (house type, condition...) are kept as
written on the site; converting them to scores is the decision model's job (app/criteria.py)."""
from __future__ import annotations

import html as htmllib
import json
import re
from typing import Dict, List, Optional

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(fragment: Optional[str]) -> str:
    if fragment is None:
        return ""
    return _WS.sub(" ", htmllib.unescape(_TAG.sub(" ", fragment))).strip()


def _num(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"\d[\d\s ]*(?:[.,]\d+)?", s)
    if not m:
        return None
    return float(re.sub(r"[\s ]", "", m.group(0)).replace(",", "."))


def parse_title(title: str) -> Dict[str, object]:
    """'2-комнатная квартира · 66.4 м² · 1/9 этаж' -> rooms, area, floor, floors."""
    out: Dict[str, object] = {}
    m = re.search(r"(\d+)-комнатная", title)
    if m:
        out["rooms"] = int(m.group(1))
    m = re.search(r"([\d.]+)\s*м²", title)
    if m:
        out["area"] = float(m.group(1))
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*этаж", title)
    if m:
        out["floor"], out["floors"] = int(m.group(1)), int(m.group(2))
    return out


def parse_attrs(preview: str) -> Dict[str, object]:
    """The structured head of a card preview: 'жил. комплекс X, монолитный дом, 2015 г.п.,
    состояние: ..., потолки 2.7м., ..., меблирована полностью, <free text>'.
    The free text is dropped on purpose - it may contain names and phone numbers."""
    out: Dict[str, object] = {}
    m = re.search(r"жил\. комплекс ([^,]+),", preview)
    if m:
        out["complex"] = m.group(1).strip()
    m = re.search(r"(\S+(?:-\S+)?) дом,", preview)
    if m:
        out["house_type"] = m.group(1).lower()
    m = re.search(r"(\d{4}) г\.п\.", preview)
    if m:
        out["year"] = int(m.group(1))
    m = re.search(r"состояние: (не новый, но аккуратный ремонт|[^,]+),", preview)
    if m:
        out["condition"] = m.group(1).strip()
    m = re.search(r"потолки ([\d.]+)\s*м", preview)
    if m:
        out["ceiling"] = float(m.group(1).rstrip("."))
    m = re.search(r"кухня ([\d.]+) м²", preview)
    if m:
        out["kitchen"] = float(m.group(1))
    m = re.search(r"меблирована (полностью|частично)|(без мебели)", preview)
    if m:
        out["furniture"] = m.group(1) or "без мебели"
    return out


_CARD_START = re.compile(r'<div[^>]*?\bclass="a-card a-storage-live[^"]*"', re.S)


def parse_search(html: str) -> Dict[str, object]:
    """Cards of a search page and whether there is a next page."""
    starts = [m.start() for m in _CARD_START.finditer(html)]
    cards: List[Dict[str, object]] = []
    seen = set()
    for i, s in enumerate(starts):
        chunk = html[s: starts[i + 1] if i + 1 < len(starts) else s + 20000]
        head = chunk[:chunk.find(">") + 1]
        m = re.search(r'data-id="(\d+)"', head)
        if not m or m.group(1) in seen:
            continue
        aid = m.group(1)
        seen.add(aid)

        def grab(pattern: str) -> Optional[str]:
            x = re.search(pattern, chunk, re.S)
            return x.group(1) if x else None

        title = _text(grab(r'class="a-card__title[^"]*"[^>]*>(.*?)</a>'))
        price_text = _text(grab(r'class="a-card__price[^"]*">(.*?)</div>'))
        preview = _text(grab(r'class="a-card__text-preview[^"]*">(.*?)</div>'))
        subtitle = _text(grab(r'class="a-card__subtitle[^"]*">(.*?)</div>'))
        photo = grab(r'<img[^>]*?src="(https://[^"]+?-400x300\.(?:jpg|webp))"')
        card: Dict[str, object] = {
            "id": aid,
            "url": f"https://krisha.kz/a/show/{aid}",
            "title": title,
            "price": _num(price_text),
            "price_from": price_text.startswith("от "),
            "price_period": "month" if "за месяц" in price_text else
                            "day" if "за сутки" in price_text else "hour" if "за час" in price_text else None,
            "address": subtitle,
            "district": subtitle.split(",")[0].strip() if "р-н" in subtitle.split(",")[0] else None,
            "photo": photo,
            "is_layout": 'data-is-layout="1"' in head,
        }
        card.update(parse_title(title))
        card.update(parse_attrs(preview + ","))
        cards.append(card)
    search = _window_data(html).get("search") or {}
    total = ((search.get("analytics") or {}).get("default") or {}).get("count") or _num(str(search.get("nbTotal") or ""))
    return {"cards": cards, "has_next": "paginator__btn--next" in html, "total": total}


def _window_data(html: str) -> dict:
    m = re.search(r"window\.data\s*=\s*(\{.*?\});\s*\n", html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def parse_detail(html: str) -> Dict[str, object]:
    """Listing page: coordinates and the parameter table."""
    adv = _window_data(html).get("advert") or {}
    out: Dict[str, object] = {}
    if adv.get("price"):
        out["price"] = float(adv["price"])
    if adv.get("rooms"):
        out["rooms"] = int(adv["rooms"])
    if adv.get("square"):
        out["area"] = float(adv["square"])
    if adv.get("title"):
        out["title"] = adv["title"]
        out.update({k: v for k, v in parse_title(adv["title"]).items() if k in ("floor", "floors")})
    if adv.get("addressTitle"):
        out["address_full"] = adv["addressTitle"]
    geo = adv.get("map") or {}
    if geo.get("lat") and geo.get("lon"):
        out["lat"], out["lon"] = float(geo["lat"]), float(geo["lon"])
    photos = adv.get("photos") or []
    if photos and photos[0].get("src"):
        out["photo_detail"] = re.sub(r"-full\.(jpg|webp)$", r"-400x300.\1", photos[0]["src"])
    out["photos_count"] = len(photos)

    params: Dict[str, str] = {}
    for m in re.finditer(r'data-name="([\w.]+)"[^>]*>\s*<div class="offer__info-title">.*?</div>.*?'
                         r'class="offer__advert-short-info">(.*?)</div>', html, re.S):
        params.setdefault(m.group(1), _text(m.group(2)))
    for m in re.finditer(r'<dt[^>]*data-name="([\w.]+)"[^>]*>.*?</dt>\s*<dd[^>]*>(.*?)</dd>', html, re.S):
        params.setdefault(m.group(1), _text(m.group(2)))

    if "flat.building" in params:
        out["house_type"] = params["flat.building"].lower()
    if _num(params.get("house.year")):
        out["year"] = int(_num(params["house.year"]))
    fl = re.search(r"(\d+)\s*из\s*(\d+)", params.get("flat.floor", ""))
    if fl:
        out["floor"], out["floors"] = int(fl.group(1)), int(fl.group(2))
    elif _num(params.get("flat.floor")):
        out["floor"] = int(_num(params["flat.floor"]))
    sq = params.get("live.square", "")
    if _num(sq):
        out["area"] = _num(sq)
    k = re.search(r"кухни\s*—\s*([\d.]+)", sq)
    if k:
        out["kitchen"] = float(k.group(1))
    condition = params.get("flat.renovation") or params.get("flat.rent_renovation")   # sale | rent pages
    if condition:
        out["condition"] = condition
    if _num(params.get("ceiling")):
        out["ceiling"] = _num(params["ceiling"])
    furn = params.get("live.furniture", "").lower()
    if furn:
        out["furniture"] = "полностью" if "полност" in furn else "частично" if "частич" in furn else "без мебели"
    # rent pages list the furniture and appliances item by item
    for key, name in (("flat.furniture", "furniture_items"), ("flat.facilities", "facilities")):
        if params.get(key):
            out[name] = len([x for x in params[key].split(",") if x.strip()])
    for key, name in (("flat.toilet", "toilet"), ("separated_toilet", "toilet"), ("flat.balcony", "balcony"),
                      ("flat.parking", "parking"), ("map.complex", "complex")):
        if params.get(key) and name not in out:
            out[name] = params[key]
    return out
