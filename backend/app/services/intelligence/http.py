import json
import urllib.error
import urllib.request
from typing import Any

UA = {"User-Agent": "chainfolio-intelligence/1.0", "Accept": "application/json"}


class HttpError(RuntimeError):
    pass


def get_json(url: str, timeout: float = 12) -> Any:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise HttpError(f"GET {url}: {e}") from e


def post_json(url: str, payload: Any, timeout: float = 15) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise HttpError(f"POST {url}: {e}") from e
