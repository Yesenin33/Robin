"""Synchronous JSON-RPC client (stdlib) with endpoint failover.

`url` may be a comma-separated list. Every call walks the endpoints in order, skipping the ones the shared
EndpointPool says are cooling down or too narrow for the requested eth_getLogs range (see endpoints.py):
  * HTTP 429 / a rate-limit error: that endpoint cools down (Retry-After honoured, exponential + jitter),
    we move on at once - never a hot retry against the node that is throttling us;
  * HTTP 5xx / network error: one quick jittered retry, then the next endpoint; repeated failures bench the
    endpoint for a growing time, so a node that is simply broken stops being hit at all;
  * a JSON-RPC `error` the node can't serve (tier/range limits, unsupported method, internal error) fails
    that attempt; a range error also teaches the pool a smaller cap; execution reverts are real answers;
  * if every capable endpoint is cooling, wait for the first one to return (bounded) instead of failing;
  * an endpoint moves to the front only after a clean answer;
  * eth_getLogs bursts are limited process-wide (RPC_LOGS_CONCURRENCY, RPC_LOGS_MIN_INTERVAL_MS) and can
    prefer their own endpoint list (RPC_LOGS_URLS) - the official node throttles log bursts first.
`transport` replaces all of this (tests); `poster` replaces only the HTTP layer (failover tests).
"""
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from app.services.intelligence import config as _cfg
from app.services.intelligence.endpoints import (  # noqa: F401 — re-exported for callers and tests
    LOG_RANGE_CAPS, POOL, EndpointFailure, classify_response, host_of, is_node_error, log_range_cap,
    parse_retry_after, requested_log_range,
)

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

Transport = Callable[[list[dict]], list[dict]]
# (url, payload, timeout) -> (http status, body) or (status, body, Retry-After header)
Poster = Callable[[str, list[dict], float], tuple]

log = logging.getLogger("intelligence.rpc")

CALL_TIME_CAP = 45.0  # seconds for one call across all retries, waits and endpoints
MAX_COOLDOWN_WAIT = 20.0  # longest we sleep for a cooling endpoint inside one call
REQUEST_TIMEOUT = 15.0
UA = {"User-Agent": "chainfolio-intelligence/1.0", "Content-Type": "application/json"}


class RpcError(RuntimeError):
    pass


def to_int(h: str | None) -> int:
    return int(h, 16) if h and h != "0x" else 0


def topic_addr(t: str) -> str:
    return "0x" + t[-40:].lower()


def pad_addr(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


def _http_post(url: str, payload: list[dict], timeout: float) -> tuple[int, Any, str | None]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:  # noqa: BLE001
            body = None
        return e.code, body, (e.headers.get("Retry-After") if e.headers else None)


class _LogsGate:
    """Process-wide limit on concurrent eth_getLogs and a minimum spacing between them.

    Scans, discovery, ranking and the copy collector all read logs from worker threads at once; unthrottled
    they burst the node past its limit and every one of them then fails together."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sem: threading.BoundedSemaphore | None = None
        self._next = 0.0

    def reset(self) -> None:
        with self._lock:
            self._sem, self._next = None, 0.0

    def __enter__(self) -> "_LogsGate":
        with self._lock:
            if self._sem is None:
                self._sem = threading.BoundedSemaphore(max(1, _cfg.logs_concurrency()))
            sem = self._sem
        sem.acquire()
        self._held = sem
        interval = _cfg.logs_min_interval()
        if interval > 0:
            with self._lock:
                now = time.monotonic()
                slot = max(now, self._next)
                self._next = slot + interval
            if slot > now:
                time.sleep(slot - now)
        return self

    def __exit__(self, *exc: object) -> None:
        self._held.release()


LOGS_GATE = _LogsGate()


class _NullGate:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


class Rpc:
    def __init__(self, url: str, transport: Transport | None = None, poster: Poster | None = None,
                 logs_urls: list[str] | None = None):
        self.urls = [u.strip() for u in url.split(",") if u.strip()]
        self.url = self.urls[0] if self.urls else url
        self.logs_urls = _cfg.logs_urls() if logs_urls is None else logs_urls
        self._poster = poster or _http_post
        self._transport = transport or self._failover
        self._id = 0

    def _attempt(self, url: str, payload: list[dict], deadline: float, is_logs: bool) -> Any:
        """One HTTP request to one endpoint. Raises EndpointFailure unless the answer is usable."""
        timeout = min(REQUEST_TIMEOUT, max(deadline - time.monotonic(), 1.0))
        retry_after = None
        try:
            res = self._poster(url, payload, timeout)
            status, body = res[0], res[1]
            retry_after = parse_retry_after(res[2]) if len(res) > 2 else None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            status, body = 0, None
        classify_response(status, body, retry_after, is_logs)
        return body

    def _order(self, is_logs: bool) -> list[str]:
        if not is_logs or not self.logs_urls:
            return list(self.urls)
        return [*self.logs_urls, *[u for u in self.urls if u not in self.logs_urls]]

    def _failover(self, payload: list[dict]) -> Any:
        span = requested_log_range(payload)
        is_logs = span > 0
        with (LOGS_GATE if is_logs else _NullGate()):
            return self._walk(payload, span, is_logs)

    def _walk(self, payload: list[dict], span: int, is_logs: bool) -> Any:
        deadline = time.monotonic() + CALL_TIME_CAP
        urls = self._order(is_logs)
        errors: dict[str, str] = {}
        for _ in range(3):  # initial pass + at most two more after waiting for a cooling endpoint
            usable, wait = POOL.plan(urls, span)
            for url in usable:
                for quick_retry in (True, False):
                    try:
                        body = self._attempt(url, payload, deadline, is_logs)
                    except EndpointFailure as f:
                        benched = POOL.failure(url, f, span) > 0
                        errors[host_of(url)] = str(f)
                        # one blip on a healthy endpoint gets a single jittered retry; a benched one never does
                        if f.kind == "server" and quick_retry and not benched and time.monotonic() < deadline - 1:
                            time.sleep(random.uniform(0.3, 0.7))
                            continue
                        break
                    POOL.success(url)
                    if is_logs:
                        log.debug("eth_getLogs served by %s (range %d)", host_of(url), span)
                    if url in self.urls and self.urls[0] != url:  # promote only after a clean answer
                        self.urls.remove(url)
                        self.urls.insert(0, url)
                    return body
            usable, wait = POOL.plan(urls, span)
            if usable:
                continue  # a failure carried no cooldown (a single blip): go around once more
            if wait is None or wait > min(MAX_COOLDOWN_WAIT, deadline - time.monotonic()):
                break
            time.sleep(wait)  # every capable endpoint is cooling: wait for the first one, don't hot-loop
        skipped = [host_of(u) for u in urls if span and (cap := POOL.effective_cap(u)) is not None and span > cap]
        detail = "; ".join(f"{h}: {m}" for h, m in errors.items()) or "no capable endpoint"
        if skipped:
            detail += f"; skipped (log range {span} too wide): {', '.join(skipped)}"
        raise RpcError("all RPC endpoints failed: " + detail)

    def batch(self, calls: list[tuple[str, list[Any]]], size: int = 100) -> list[Any]:
        """Results in order; a failed sub-call yields None (doesn't fail the batch)."""
        out: list[Any] = []
        for i in range(0, len(calls), size):
            payload = []
            for method, params in calls[i : i + size]:
                self._id += 1
                payload.append({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
            resp = self._transport(payload)
            if isinstance(resp, dict):  # some nodes answer a batch with a single error object
                raise RpcError(str(resp.get("error", resp)))
            by_id = {r.get("id"): r for r in resp}
            out += [by_id.get(p["id"], {}).get("result") for p in payload]
        return out

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        resp = self._transport([{"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}])
        r = resp[0] if isinstance(resp, list) and resp else resp
        if "error" in r:
            raise RpcError(f"{method}: {r['error']}")
        return r.get("result")

    # --- helpers ---
    def block_number(self) -> int:
        return to_int(self.call("eth_blockNumber", []))

    def get_code(self, addr: str) -> str:
        return self.call("eth_getCode", [addr, "latest"]) or "0x"

    def get_storage(self, addr: str, slot: str) -> str:
        return self.call("eth_getStorageAt", [addr, slot, "latest"]) or "0x"

    def eth_call(self, to: str, data: str) -> str | None:
        try:
            return self.call("eth_call", [{"to": to, "data": data}, "latest"])
        except RpcError:
            return None

    def codes(self, addrs: list[str]) -> dict[str, str]:
        res = self.batch([("eth_getCode", [a, "latest"]) for a in addrs])
        return {a: (c or "0x") for a, c in zip(addrs, res)}

    def balances_of(self, token: str, owners: list[str]) -> dict[str, int]:
        res = self.batch([("eth_call", [{"to": token, "data": "0x70a08231" + pad_addr(o)}, "latest"]) for o in owners])
        return {o: to_int(r) for o, r in zip(owners, res)}

    def transfer_logs(self, token: str, from_block: int, to_block: int, chunk: int) -> list[dict]:
        """eth_getLogs in chunks; halves the chunk when the node rejects the range."""
        logs: list[dict] = []
        start = from_block
        while start <= to_block:
            end = min(start + chunk - 1, to_block)
            try:
                logs += self.call("eth_getLogs", [{
                    "address": token, "topics": [TRANSFER_TOPIC], "fromBlock": hex(start), "toBlock": hex(end),
                }]) or []
                start = end + 1
            except RpcError:
                if chunk <= 100:
                    raise
                chunk //= 2
        return logs
