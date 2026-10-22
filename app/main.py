"""Web server: search jobs on Krisha.kz, ranking API, the single-page interface."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import plotly
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dss.ahp import ahp_weights
from krisha import Geocoder, KrishaClient, SearchQuery, collect

from . import agent, criteria
from .engine import RankRequest, rank

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATASETS = DATA / "datasets"
SAMPLES = DATA / "samples"
WEB = Path(__file__).resolve().parent / "web"
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"

app = FastAPI(title="Подбор квартиры — СППР")
app.add_middleware(GZipMiddleware, minimum_size=1024)      # plotly.js 4.8 MB -> ~1.4 MB over a tunnel
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

_client: Optional[KrishaClient] = None
_geocoder: Optional[Geocoder] = None
_jobs: Dict[str, dict] = {}
_datasets: Dict[str, dict] = {}
_lock = threading.Lock()


def client() -> KrishaClient:
    global _client
    if _client is None:
        _client = KrishaClient(DATA / "cache.sqlite")
    return _client


def geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder(DATA / "cache.sqlite")
    return _geocoder


# ---- datasets ---------------------------------------------------------------------------

def _summary(d: dict) -> dict:
    return {"id": d["id"], "mode": d["mode"], "count": len(d["listings"]), "created": d["created"],
            "total_on_site": d.get("total_on_site"), "source_url": d.get("source_url"),
            "query": d.get("query"), "sample": d.get("sample", False), "skipped": d.get("skipped"),
            "with_coords": sum(1 for x in d["listings"] if x.get("lat")), "geo": d.get("geo", {}),
            "problems": d.get("problems", [])}


def _store(d: dict) -> None:
    for i, x in enumerate(d["listings"], 1):
        x["code"] = f"A{i}"              # stable names for the matrix: A1..An in listing order
    with _lock:
        _datasets[d["id"]] = d
    if not d.get("sample"):
        DATASETS.mkdir(parents=True, exist_ok=True)
        path = DATASETS / f"{d['id']}.json"
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        d["_file"] = str(path)
        for old in sorted(DATASETS.glob("*.json"))[:-12]:   # keep the last dozen searches
            old.unlink()


def _load_saved() -> None:
    known = {d.get("_file") for d in _datasets.values()}
    for folder, sample in ((SAMPLES, True), (DATASETS, False)):
        for f in sorted(folder.glob("*.json")) if folder.exists() else []:
            if str(f) in known:
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            d["sample"], d["_file"] = sample, str(f)
            _datasets[d["id"]] = d


_load_saved()


def dataset(did: str) -> dict:
    d = _datasets.get(did)
    if not d:
        raise HTTPException(404, "Набор данных не найден — запустите поиск заново.")
    return d


# ---- API --------------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/vendor/plotly.min.js")
def plotly_js():
    return FileResponse(PLOTLY_JS, media_type="application/javascript")


@app.get("/api/config")
def config():
    _load_saved()                      # pick up demo sets saved while the server runs
    return {
        "criteria": {m: criteria.describe(m) for m in ("sale", "rent")},
        "presets": {k: {"title": v["title"], "sale": v["sale"], "rent": v["rent"]} for k, v in criteria.PRESETS.items()},
        "places": criteria.PLACES,
        "center": {"lat": criteria.CITY_CENTER[0], "lon": criteria.CITY_CENTER[1]},
        "ai": agent.enabled(),
        "datasets": sorted((_summary(d) for d in _datasets.values()), key=lambda s: s["created"], reverse=True),
    }


class SearchIn(BaseModel):
    mode: str = Field("sale", pattern="^(sale|rent)$")
    rooms: List[int] = []
    price_from: Optional[int] = None
    price_to: Optional[int] = None
    area_from: Optional[float] = None
    year_from: Optional[int] = None
    not_first: bool = False
    not_last: bool = False
    pages: int = Field(2, ge=1, le=5)
    details: bool = False
    max_details: int = Field(40, ge=0, le=100)


def _run_job(jid: str, q: SearchQuery) -> None:
    job = _jobs[jid]

    def progress(p):
        job["progress"] = p

    try:
        res = collect(client(), q, progress, job["cancel"], geocoder())
        if not res["listings"]:
            job.update(status="error", error="Ничего не найдено. Ослабьте фильтры поиска."
                       + (" " + "; ".join(res["problems"]) if res["problems"] else ""))
            return
        d = {"id": time.strftime("%Y%m%d-%H%M%S") + f"-{q.mode}", "mode": q.mode, "created": time.time(), **res}
        _store(d)
        job.update(status="cancelled" if job["cancel"].is_set() else "done", dataset=_summary(d))
    except Exception as e:                      # surface the reason in the interface
        job.update(status="error", error=f"Не удалось загрузить данные с Krisha.kz: {e}")


@app.post("/api/search")
def start_search(body: SearchIn):
    q = SearchQuery(mode=body.mode, rooms=body.rooms, price_from=body.price_from, price_to=body.price_to,
                    area_from=body.area_from, year_from=body.year_from, not_first=body.not_first,
                    not_last=body.not_last, pages=body.pages, details=body.details,
                    max_details=body.max_details)
    jid = uuid.uuid4().hex[:10]
    _jobs[jid] = {"status": "running", "progress": {"stage": "start", "done": 0, "total": 1, "message": "Старт"},
                  "cancel": threading.Event(), "url": "https://krisha.kz" + q.path(1)}
    threading.Thread(target=_run_job, args=(jid, q), daemon=True).start()
    return {"job": jid, "url": _jobs[jid]["url"]}


@app.get("/api/search/{jid}")
def search_status(jid: str):
    job = _jobs.get(jid)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return {k: v for k, v in job.items() if k != "cancel"}


@app.delete("/api/search/{jid}")
def cancel_search(jid: str):
    job = _jobs.get(jid)
    if job:
        job["cancel"].set()
    return {"ok": True}


class RankIn(BaseModel):
    dataset: str
    weights: Dict[str, float]
    point: Optional[Dict[str, float]] = None
    point_name: str = ""
    constraints: Dict[str, object] = {}
    missing: str = Field("worst", pattern="^(worst|median|drop)$")
    sens_key: Optional[str] = None
    sens_method: str = Field("wsm", pattern="^(wsm|topsis)$")
    top: int = Field(15, ge=5, le=40)


@app.post("/api/rank")
def rank_api(body: RankIn):
    d = dataset(body.dataset)
    point = (body.point["lat"], body.point["lon"]) if body.point else None
    req = RankRequest(weights=body.weights, point=point, point_name=body.point_name,
                      constraints=body.constraints, missing=body.missing, sens_key=body.sens_key,
                      sens_method=body.sens_method, top=body.top)
    res = rank(d, req)
    res["dataset"] = _summary(d)
    return res


class ChatIn(BaseModel):
    chat_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=agent.MAX_MESSAGE)
    state: Dict[str, Any]                 # the same body as /api/rank plus "sort" - what is on the screen
    tab: str = ""


@app.post("/api/chat")
def chat_api(body: ChatIn, request: Request):
    who = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if not agent.allow(who):
        raise HTTPException(429, "Слишком много вопросов подряд — подождите несколько минут.")
    d = dataset(str(body.state.get("dataset") or ""))
    s = agent.session(body.chat_id)

    def events():
        if not s.lock.acquire(blocking=False):
            yield _sse({"type": "error", "message": "Предыдущий ответ ещё пишется."})
            return
        try:
            for ev in agent.chat(d, s, body.state, body.message.strip(), body.tab):
                yield _sse(ev)
        finally:
            s.lock.release()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(ev: dict) -> str:
    return "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"


class AhpIn(BaseModel):
    keys: List[str]
    matrix: List[List[float]]


@app.post("/api/ahp")
def ahp_api(body: AhpIn):
    try:
        return ahp_weights(body.keys, body.matrix)
    except ValueError as e:
        raise HTTPException(400, str(e))
