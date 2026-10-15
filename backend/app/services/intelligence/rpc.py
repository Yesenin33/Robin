"""Synchronous JSON-RPC client (stdlib). `transport` is injectable for tests."""
from collections.abc import Callable
from typing import Any

from app.services.intelligence.http import post_json

ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

Transport = Callable[[list[dict]], list[dict]]


class RpcError(RuntimeError):
    pass


def to_int(h: str | None) -> int:
    return int(h, 16) if h and h != "0x" else 0


def topic_addr(t: str) -> str:
    return "0x" + t[-40:].lower()


def pad_addr(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


class Rpc:
    def __init__(self, url: str, transport: Transport | None = None):
        """`url` may be a comma-separated list: tried in order, the first that answers wins."""
        self.urls = [u.strip() for u in url.split(",") if u.strip()]
        self.url = self.urls[0] if self.urls else url
        self._transport = transport or self._failover
        self._id = 0

    def _failover(self, payload: list[dict]) -> list[dict]:
        last: Exception | None = None
        for i, u in enumerate(self.urls):
            try:
                resp = post_json(u, payload)
                if i:  # promote the node that answered
                    self.urls.insert(0, self.urls.pop(i))
                return resp
            except Exception as e:  # noqa: BLE001
                last = e
        raise RpcError(f"all RPC endpoints failed: {last}")

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
