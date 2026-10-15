import json
import time
import urllib.error
import urllib.request
from typing import Any

from app.services.intelligence.config import proxy_key, proxy_prefix

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


def _read(url: str, timeout: float, tries: int) -> Any:
    proxy = proxy_prefix()
    target = f"{proxy.rstrip('/')}/{url}" if proxy else url
    headers = {**UA, "x-respond-with": "json"} if proxy else UA
    if proxy and (key := proxy_key()):
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(target, headers=headers)
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                raw = r.read().decode("utf-8", "replace")
            return _unwrap(raw) if proxy else json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise HttpError(f"GET {url}: {last}") from last


def get_json(url: str, timeout: float = 20, tries: int = 3) -> Any:
    return _read(url, timeout, tries)


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
