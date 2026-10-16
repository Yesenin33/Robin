import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from app.services.intelligence.config import (
    browser_token, browser_url, explorer_base, explorer_rpm, proxy_key, proxy_prefix,
)

UA = {"User-Agent": "chainfolio-intelligence/1.0", "Accept": "application/json"}


class HttpError(RuntimeError):
    pass


def _unwrap(text: str) -> Any:
    """Return the JSON payload from a read-proxy (r.jina.ai style) response.

    The proxy may answer either as a raw JSON document wrapped in {data:{content:...}}
    or as a text/markdown envelope; both are reduced to the origin payload here.
    """
    stripped = text.strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        marker = "Markdown Content:"
        if marker in stripped:
            stripped = stripped.split(marker, 1)[1].strip()
        return json.loads(stripped)
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        try:
            return json.loads(data["content"])
        except json.JSONDecodeError:
            pass
    return obj


class Pacer:
    """Process-wide spacing of explorer reads (scans run in threads). Slots are reserved under a lock and
    slept for outside it, so concurrent scans line up instead of bursting the proxy.

    Two lanes: user-requested scans (priority) take the next slot without waiting behind queued bulk work,
    and push bulk back by one slot so the overall rate stays at `rpm`. A 429 delays both lanes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = lambda: random.uniform(1.0, 1.15)):
        self._lock = threading.Lock()
        self._clock, self._sleep, self._jitter = clock, sleep, jitter
        self._next_bulk = self._next_prio = 0.0

    def acquire(self, priority: bool, rpm: int) -> float:
        interval = 60.0 / max(rpm, 1) * self._jitter()
        with self._lock:
            now = self._clock()
            if priority:
                slot = max(now, self._next_prio)
                self._next_prio = slot + interval
                self._next_bulk = max(self._next_bulk, slot) + interval
            else:
                slot = max(now, self._next_bulk, self._next_prio)
                self._next_bulk = slot + interval
        delay = max(slot - now, 0.0)
        if delay:
            self._sleep(delay)
        return delay

    def penalize(self, seconds: float) -> None:
        with self._lock:
            until = self._clock() + seconds
            self._next_bulk, self._next_prio = max(self._next_bulk, until), max(self._next_prio, until)


PACER = Pacer()
_local = threading.local()
BACKOFF_CAP = 30.0


def set_priority(priority: bool) -> None:
    """Mark the current thread's explorer reads as user-requested (called by the scan wrapper)."""
    _local.priority = priority


def is_explorer(url: str) -> bool:
    base = explorer_base()
    return bool(base) and url.startswith(base)


def backoff_seconds(attempt: int, retry_after: str | None, rand: Callable[[], float] = random.random) -> float:
    """Jittered exponential backoff (2, 4, 8 ... capped); a Retry-After from the proxy is a floor."""
    base = min(2.0 * 2**attempt, BACKOFF_CAP)
    if retry_after and retry_after.strip().isdigit():
        base = max(base, min(float(retry_after), BACKOFF_CAP))
    return base * (0.75 + 0.5 * rand())


def _routes(url: str) -> list[str]:
    """How to read this URL, in order. Only the EXPLORER sits behind a Cloudflare challenge: it goes through the
    Chromium sidecar (EXPLORER_BROWSER_URL) and, optionally, the paid read proxy as a fallback
    (EXPLORER_PROXY_PREFIX). Every other URL (DexScreener, GeckoTerminal) is fetched directly - they answer
    200 without any help, and proxying them wastes the proxy's quota."""
    if not is_explorer(url):
        return ["direct"]
    routes = []
    if browser_url():
        routes.append("browser")
    if proxy_prefix():
        routes.append("proxy")
    return routes or ["direct"]


def _build(route: str, url: str) -> tuple[urllib.request.Request, Callable[[str], Any]]:
    if route == "browser":
        headers = {**UA, "Content-Type": "application/json"}
        if token := browser_token():
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps({"url": url}).encode()
        return urllib.request.Request(f"{browser_url()}/internal/fetch", data=body, headers=headers), json.loads
    if route == "proxy":
        proxy = proxy_prefix()
        headers = {**UA, "x-respond-with": "json"}
        if key := proxy_key():
            headers["Authorization"] = f"Bearer {key}"
        return urllib.request.Request(f"{proxy.rstrip('/')}/{url}", headers=headers), _unwrap
    return urllib.request.Request(url, headers=UA), json.loads


def _try_route(route: str, url: str, timeout: float, tries: int) -> Any:
    req, parse = _build(route, url)
    paced = is_explorer(url)
    last: Exception | None = None
    for attempt in range(tries):
        if paced:
            PACER.acquire(getattr(_local, "priority", False), explorer_rpm())
        wait = 1.5 * (attempt + 1)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                raw = r.read().decode("utf-8", "replace")
            return parse(raw)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                break  # 404 and friends won't change on retry
            wait = backoff_seconds(attempt, e.headers.get("Retry-After") if e.headers else None)
            if paced and e.code == 429:
                PACER.penalize(wait)  # everybody backs off, not just this thread
                wait = 0.0  # the next acquire() sleeps until the penalty ends
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            if route == "browser" and isinstance(e, urllib.error.URLError) and attempt >= 1:
                break  # sidecar unreachable: don't sit through the whole backoff before the fallback
        if attempt < tries - 1 and wait:
            time.sleep(wait)
    raise HttpError(f"{route}: {last}") from last


def _read(url: str, timeout: float, tries: int) -> Any:
    errors = []
    for route in _routes(url):
        try:
            return _try_route(route, url, timeout, tries)
        except HttpError as e:
            errors.append(str(e))
    raise HttpError(f"GET {url}: " + "; ".join(errors))


def get_json(url: str, timeout: float = 20, tries: int = 3) -> Any:
    # explorer reads get one more attempt: a 429 burst usually clears within seconds
    return _read(url, timeout, tries + 1 if is_explorer(url) else tries)


def post_json(url: str, payload: Any, timeout: float = 15, tries: int = 2) -> Any:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={**UA, "Content-Type": "application/json"}
    )
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            if attempt < tries - 1:
                time.sleep(1.0 * (attempt + 1))
    raise HttpError(f"POST {url}: {last}") from last
