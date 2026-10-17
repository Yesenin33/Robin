"""Shared RPC endpoint policy for the sync client (scans, jobs) and the async client (portfolio).

State is process-wide, so a node that just answered 429/500 is left alone by EVERY caller for a while
instead of being re-hit by each retry loop (that amplification is what turned one throttle into a storm).

  * classify_response(): what an answer means - accept it, or fail this endpoint and why.
  * EndpointPool: per-URL cooldowns (429 honours Retry-After, repeated failures back off exponentially with
    jitter), learned eth_getLogs range caps, and `plan()` which says which endpoints may be used right now.
"""
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# Max eth_getLogs block range per endpoint host (None = wide ranges). Measured on Robinhood Chain.
# Unknown hosts are assumed wide; a range error teaches the pool the real cap (see learn_cap).
LOG_RANGE_CAPS: dict[str, int | None] = {
    "rpc.mainnet.chain.robinhood.com": None,  # official node: wide ranges, free
    "alchemy.com": 10,  # free tier: 10-block ranges -> unusable for log scans
}
COOLDOWN_CAP = 300.0  # seconds: even a dead node is probed again every 5 minutes
RATE_LIMIT_CAP = 60.0

_RANGE = re.compile(r"block range|range|blocks?\b|archive|too many results|more than \d+ results|query returned", re.I)
_RATE = re.compile(r"rate|too many requests|429|throttl|capacity|exceeded.*(quota|limit)|quota", re.I)
_NODE_CODES = {-32005, -32600, -32601, -32603, -32004, -32001, 429}
_NODE_TEXT = re.compile(r"limit|tier|plan|exceed|not supported|unsupported|does not exist|not available|unavailable|"
                        r"timeout|timed out|internal error|overloaded|temporar", re.I)


class EndpointFailure(Exception):
    """This endpoint's answer must not be used. kind: rate_limited | server | capability | node."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def host_of(url: str) -> str:
    return (urlparse(url).hostname or url).lower()


def log_range_cap(url: str) -> int | None:
    host = host_of(url)
    for key, cap in LOG_RANGE_CAPS.items():
        if host == key or host.endswith("." + key) or host.endswith(key):
            return cap
    return None


def requested_log_range(payload: list[dict]) -> int:
    """Largest eth_getLogs block span in the payload (0 = no getLogs)."""
    span = 0
    for c in payload:
        if c.get("method") == "eth_getLogs":
            f = (c.get("params") or [{}])[0]
            lo, hi = f.get("fromBlock"), f.get("toBlock")
            if isinstance(lo, str) and isinstance(hi, str) and lo.startswith("0x") and hi.startswith("0x"):
                span = max(span, int(hi, 16) - int(lo, 16) + 1)
            else:
                span = max(span, 10**9)  # "latest"/"earliest": assume wide
    return span


def is_node_error(err: Any) -> bool:
    """True when a JSON-RPC error means "this node can't serve this" (try another), not a contract result."""
    if not isinstance(err, dict):
        return True
    msg = str(err.get("message", ""))
    if "revert" in msg.lower():
        return False  # execution result: a genuine answer
    return err.get("code") in _NODE_CODES or bool(_NODE_TEXT.search(msg)) or bool(_RATE.search(msg))


def parse_retry_after(value: str | None) -> float | None:
    try:
        return max(float(value), 0.0) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def classify_response(status: int, body: Any, retry_after: float | None = None, is_logs: bool = False) -> None:
    """Return None when the answer is usable; raise EndpointFailure otherwise.

    Covers HTTP 429/5xx/network (status 0), 4xx without a JSON-RPC body, a single JSON-RPC error object and
    batches that contain node errors. Execution reverts are answers, not failures."""
    if status == 429:
        raise EndpointFailure("rate_limited", "HTTP 429", retry_after)
    if status == 0 or status >= 500:
        raise EndpointFailure("server", f"HTTP {status}" if status else "network error")
    errors: list[dict] = []
    if isinstance(body, dict) and "error" in body and "result" not in body:
        errors = [body["error"]]
    elif isinstance(body, list):
        errors = [r["error"] for r in body if isinstance(r, dict) and "error" in r]
    node_errors = [e for e in errors if is_node_error(e)]
    if node_errors:
        msg = str(node_errors[0].get("message", node_errors[0]) if isinstance(node_errors[0], dict) else node_errors[0])
        if _RATE.search(msg) and not (is_logs and _RANGE.search(msg)):
            raise EndpointFailure("rate_limited", f"rpc error {msg}", retry_after)
        if is_logs and _RANGE.search(msg):
            raise EndpointFailure("capability", f"rpc error {msg}")
        raise EndpointFailure("node", f"rpc error {msg}")
    if status >= 400 and not errors:
        raise EndpointFailure("server", f"HTTP {status}")
    if not isinstance(body, (dict, list)):
        raise EndpointFailure("server", "malformed response")


@dataclass
class _State:
    fails: int = 0  # consecutive failures
    until: float = 0.0  # cooling until (monotonic)
    cap: int | None = None  # learned eth_getLogs range cap (smaller than the static one)
    last: str = ""


class EndpointPool:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 jitter: Callable[[], float] = lambda: random.uniform(0.75, 1.25)):
        self._lock = threading.Lock()
        self._clock, self._jitter = clock, jitter
        self._s: dict[str, _State] = {}

    def _st(self, url: str) -> _State:
        return self._s.setdefault(url, _State())

    def reset(self) -> None:
        with self._lock:
            self._s.clear()

    def effective_cap(self, url: str) -> int | None:
        static, learned = log_range_cap(url), self._st(url).cap
        caps = [c for c in (static, learned) if c is not None]
        return min(caps) if caps else None

    def plan(self, urls: list[str], span: int = 0) -> tuple[list[str], float | None]:
        """(endpoints usable right now, in order; seconds until the earliest cooling-but-capable one is back).

        A getLogs span larger than an endpoint's cap excludes it. If nothing is usable only because every
        capable endpoint is cooling, `wait` says how long until the first returns (None = nothing will)."""
        now = self._clock()
        usable, wait = [], None
        with self._lock:
            for u in urls:
                cap = self.effective_cap(u)
                if span and cap is not None and span > cap:
                    continue
                left = self._st(u).until - now
                if left <= 0:
                    usable.append(u)
                else:
                    wait = left if wait is None else min(wait, left)
        return usable, (None if usable else wait)

    def success(self, url: str) -> None:
        with self._lock:
            st = self._st(url)
            st.fails, st.until, st.last = 0, 0.0, ""

    def failure(self, url: str, failure: EndpointFailure, span: int = 0) -> float:
        """Record a failed attempt; returns the cooldown applied (0 = none)."""
        with self._lock:
            st = self._st(url)
            st.last = str(failure)
            if failure.kind == "capability":
                # the node works, just not for a range this wide: remember a smaller cap, don't bench it
                cap = _parse_cap(str(failure)) or (max(span // 2, 1) if span else None)
                if cap is not None:
                    st.cap = cap if st.cap is None else min(st.cap, cap)
                return 0.0
            st.fails += 1
            if failure.kind == "rate_limited":
                base = min(2.0 * 2 ** min(st.fails - 1, 5), RATE_LIMIT_CAP)
                cool = max(failure.retry_after or 0.0, base) * self._jitter()
            elif st.fails == 1:
                cool = 0.0  # one blip is a retry, not an outage
            else:
                cool = min(15.0 * 2 ** (st.fails - 2), COOLDOWN_CAP) * self._jitter()
            st.until = self._clock() + cool if cool else st.until
            return cool

    def snapshot(self, url: str) -> dict:
        with self._lock:
            st, now = self._st(url), self._clock()
            return {"fails": st.fails, "cooling_s": max(round(st.until - now, 1), 0.0),
                    "log_range_cap": self.effective_cap(url), "last_error": st.last or None}


def _parse_cap(message: str) -> int | None:
    m = re.search(r"up to (?:a )?(\d[\d,]*) block", message, re.I) or re.search(r"(\d[\d,]*)[- ]block", message, re.I)
    return int(m.group(1).replace(",", "")) if m else None


POOL = EndpointPool()
