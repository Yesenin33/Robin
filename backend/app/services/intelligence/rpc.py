"""Synchronous JSON-RPC client (stdlib) with endpoint failover.

`url` may be a comma-separated list. For every call the endpoints are tried in order:
  * an endpoint that caps eth_getLogs below the requested range is skipped for that call;
  * HTTP 429 / 5xx / network errors are retried (0.5 s, 1.5 s) within a per-call time cap;
  * a JSON-RPC `error` that means "this node can't serve this" (range/tier limits, method not
    supported, rate limited) fails the attempt and moves on; execution reverts are real answers;
  * an endpoint is promoted to the front only after a clean answer.
`transport` replaces all of this (tests); `poster` replaces only the HTTP layer (failover tests).
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

Transport = Callable[[list[dict]], list[dict]]
Poster = Callable[[str, list[dict], float], tuple[int, Any]]  # (url, payload, timeout) -> (http status, body)

log = logging.getLogger("intelligence.rpc")

# Max eth_getLogs block range per endpoint host (None = wide ranges). Measured on Robinhood Chain.
LOG_RANGE_CAPS: dict[str, int | None] = {
    "rpc.mainnet.chain.robinhood.com": None,  # official node: wide ranges, free
    "robinhood.drpc.org": 10_000,
    "alchemy.com": 10,  # free tier: 10-block ranges -> unusable for log scans
}
RETRY_BACKOFF = (0.5, 1.5)
CALL_TIME_CAP = 45.0  # seconds for one call across all retries and endpoints
REQUEST_TIMEOUT = 15.0
UA = {"User-Agent": "chainfolio-intelligence/1.0", "Content-Type": "application/json"}
_NODE_ERROR = re.compile(r"range|limit|tier|plan|rate|too many|exceed|not supported|unsupported|"
                         r"does not exist|not available|unavailable|timeout|timed out|capacity|archive", re.I)
_NODE_CODES = {-32005, -32600, -32601, -32603, -32004, -32001, 429}


class RpcError(RuntimeError):
    pass


class _Retryable(Exception):
    pass


def to_int(h: str | None) -> int:
    return int(h, 16) if h and h != "0x" else 0


def topic_addr(t: str) -> str:
    return "0x" + t[-40:].lower()


def pad_addr(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


def log_range_cap(url: str) -> int | None:
    host = (urlparse(url).hostname or "").lower()
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
    """True when the error means this endpoint can't serve the request (try another one)."""
    if not isinstance(err, dict):
        return True
    msg = str(err.get("message", ""))
    if "revert" in msg.lower():
        return False  # contract execution result: a genuine answer
    return err.get("code") in _NODE_CODES or bool(_NODE_ERROR.search(msg))


def _http_post(url: str, payload: list[dict], timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:  # noqa: BLE001
            body = None
        return e.code, body


class Rpc:
    def __init__(self, url: str, transport: Transport | None = None, poster: Poster | None = None):
        self.urls = [u.strip() for u in url.split(",") if u.strip()]
        self.url = self.urls[0] if self.urls else url
        self._poster = poster or _http_post
        self._transport = transport or self._failover
        self._id = 0

    def _attempt(self, url: str, payload: list[dict], deadline: float) -> Any:
        """One endpoint, with retries on 429/5xx/network errors. Raises on failure."""
        last = ""
        for i in range(len(RETRY_BACKOFF) + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RpcError(f"time cap reached ({last or 'no answer'})")
            try:
                status, body = self._poster(url, payload, min(REQUEST_TIMEOUT, remaining))
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                status, body, last = 0, None, f"{type(e).__name__}: {e}"
            if status == 429 or status >= 500 or status == 0:
                last = last if status == 0 else f"HTTP {status}"
                if i < len(RETRY_BACKOFF) and deadline - time.monotonic() > RETRY_BACKOFF[i]:
                    time.sleep(RETRY_BACKOFF[i])
                    continue
                raise RpcError(last)
            if status >= 400 and body is None:
                raise RpcError(f"HTTP {status}")
            # JSON-RPC level: single error object, or a batch containing node errors
            if isinstance(body, dict) and "error" in body and "result" not in body:
                if is_node_error(body["error"]):
                    raise RpcError(f"rpc error {body['error']}")
            elif isinstance(body, list):
                bad = [r["error"] for r in body if isinstance(r, dict) and "error" in r and is_node_error(r["error"])]
                if bad:
                    raise RpcError(f"rpc error {bad[0]}")
            elif not isinstance(body, dict):
                raise RpcError("malformed response")
            if status >= 400:
                raise RpcError(f"HTTP {status}")
            return body
        raise RpcError(last)

    def _failover(self, payload: list[dict]) -> Any:
        deadline = time.monotonic() + CALL_TIME_CAP
        span = requested_log_range(payload)
        errors: list[str] = []
        for url in list(self.urls):
            cap = log_range_cap(url)
            if span and cap is not None and span > cap:
                errors.append(f"{urlparse(url).hostname}: skipped (log range {span} > cap {cap})")
                continue
            try:
                body = self._attempt(url, payload, deadline)
            except RpcError as e:
                errors.append(f"{urlparse(url).hostname}: {e}")
                continue
            if span:
                log.debug("eth_getLogs served by %s (range %d)", urlparse(url).hostname, span)
            if self.urls[0] != url:  # promote only after a clean answer
                self.urls.remove(url)
                self.urls.insert(0, url)
            return body
        raise RpcError("all RPC endpoints failed: " + "; ".join(errors))

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
