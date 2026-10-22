"""HTTP client with an SQLite page cache and a minimum pause between real requests.

When requests come too often krisha.kz answers HTTP 468 with a bot-check page. That is
read as "stop": the client raises Blocked and does not request that kind of page for a while."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx

BASE = "https://krisha.kz"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/129.0.0.0 Safari/537.36")
# robots.txt of krisha.kz: we never go there
FORBIDDEN = ("/ajax/", "/a/show-map/", "/a/comments", "/comment", "raion=", "/claim", "/sendmess", "/ms/")
SLOW_DOWN = {429, 468, 503}
BLOCK_PAUSE = 30 * 60
BLOCKED_MSG = ("Krisha.kz включил проверку на бота (HTTP 468) — страницы объявлений временно не открываем. "
               "Координаты взяты по адресу из карточки.")


class FetchError(RuntimeError):
    pass


class Blocked(FetchError):
    """The site keeps asking to slow down - stop for now."""


Waiting = Callable[[float, str], None]


class KrishaClient:
    def __init__(self, cache_path: Path, delay: float = 3.0, timeout: float = 20.0):
        self.delay = delay
        self.blocked_until: Dict[str, float] = {}
        self._last = 0.0
        self._lock = threading.Lock()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(cache_path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, fetched REAL, body TEXT)")
        self._db.commit()
        self._http = httpx.Client(base_url=BASE, timeout=timeout, follow_redirects=True, headers={
            "User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})

    def close(self) -> None:
        self._http.close()
        self._db.close()

    def cached(self, url: str, ttl: float) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT fetched, body FROM pages WHERE url = ?", (url,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return row[1]
        return None

    def get(self, url: str, ttl: float, waiting: Optional[Waiting] = None,
            cancel: Optional[threading.Event] = None) -> str:
        """url is a path with query, e.g. /a/show/123. Served from the cache while younger than ttl seconds."""
        if any(f in url for f in FORBIDDEN):
            raise FetchError(f"robots.txt запрещает {url}")
        body = self.cached(url, ttl)
        if body is not None:
            return body
        kind = "detail" if url.startswith("/a/show/") else "search"
        if time.time() < self.blocked_until.get(kind, 0):
            raise Blocked(BLOCKED_MSG)
        err = ""
        for attempt in range(2):
            with self._lock:
                wait = self._last + self.delay - time.time()
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = self._http.get(url)
                except httpx.HTTPError as e:
                    r, err = None, str(e) or type(e).__name__
                self._last = time.time()
            if r is not None and r.status_code == 200:
                with self._lock:
                    self._db.execute("REPLACE INTO pages VALUES (?, ?, ?)", (url, time.time(), r.text))
                    self._db.commit()
                return r.text
            if r is not None and r.status_code == 404:
                raise FetchError(f"{url}: объявление снято (404)")
            if r is not None and r.status_code in SLOW_DOWN:
                # the bot check (SafeLine) answered instead of the page: we do not try to get past it,
                # this kind of page is left alone for a while
                self.blocked_until[kind] = time.time() + BLOCK_PAUSE
                raise Blocked(BLOCKED_MSG)
            err = f"HTTP {r.status_code}" if r is not None else err
            if attempt == 0:
                if waiting:
                    waiting(3.0, err)
                if (cancel.wait(3.0) if cancel is not None else time.sleep(3.0)):
                    raise FetchError("остановлено")
        raise FetchError(f"{url}: {err}")
