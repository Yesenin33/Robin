"""Minimal async JSON-RPC client for Robinhood Chain (EVM)."""
import asyncio
import random
import re
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.intelligence.endpoints import (
    POOL, EndpointFailure, classify_response, host_of, parse_retry_after, requested_log_range,
)
from app.services.intelligence.rpc import CALL_TIME_CAP, MAX_COOLDOWN_WAIT

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SEL_BALANCE_OF = "0x70a08231"
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"
SEL_NAME = "0x06fdde03"
SEL_TOTAL_SUPPLY = "0x18160ddd"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class RPCError(RuntimeError):
    pass


def is_address(value: str) -> bool:
    return bool(ADDRESS_RE.match(value or ""))


def norm(address: str) -> str:
    if not is_address(address):
        raise ValueError(f"invalid address: {address!r}")
    return address.lower()


def _pad(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def decode_uint(hex_value: str | None) -> int:
    if not hex_value or hex_value == "0x":
        return 0
    return int(hex_value, 16)


def decode_string(hex_value: str | None) -> str | None:
    """ABI string (dynamic) with bytes32 fallback."""
    if not hex_value or hex_value == "0x":
        return None
    data = bytes.fromhex(hex_value[2:])
    try:
        if len(data) >= 64:
            length = int.from_bytes(data[32:64], "big")
            return data[64 : 64 + length].decode("utf-8", "ignore") or None
        return data.rstrip(b"\x00").decode("utf-8", "ignore") or None
    except Exception:
        return None


class RPCClient:
    """Async JSON-RPC client (portfolio, position snapshots) on the same endpoint policy as intelligence.rpc.Rpc.

    Big `batch()` calls are split into chunks (RPC_BATCH_SIZE): one request carrying every token in the
    `tokens` table (800+ eth_calls) is rejected or throttled by most public nodes. An endpoint that answers
    429 / 5xx / a JSON-RPC error object is skipped for a while (shared EndpointPool) and the next one is tried,
    so one throttled node can't fail a whole snapshot."""

    def __init__(self, url: str | None = None, client: httpx.AsyncClient | None = None, batch_size: int | None = None):
        s = get_settings()
        if url is None:
            url = ",".join([s.rpc_url, *s.rpc_fallback_urls.split(",")])
        self.urls = [u.strip() for u in url.split(",") if u.strip()]
        self.url = self.urls[0]
        self.batch_size = max(batch_size or s.rpc_batch_size, 1)
        self._client = client or httpx.AsyncClient(timeout=15)
        self._id = 0

    async def call(self, method: str, params: list[Any]) -> Any:
        return (await self.batch([(method, params)]))[0]

    async def batch(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        out: list[Any] = []
        for i in range(0, len(calls), self.batch_size):
            out += await self._chunk(calls[i : i + self.batch_size])
        return out

    async def _chunk(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        payload = []
        for method, params in calls:
            self._id += 1
            payload.append({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        body = await self._send(payload)
        by_id = {r["id"]: r for r in body if isinstance(r, dict) and "id" in r}
        out = []
        for req in payload:
            r = by_id.get(req["id"], {})
            # a per-call error (e.g. a reverted balanceOf) shouldn't kill the batch
            out.append(None if "error" in r else r.get("result"))
        return out

    async def _send(self, payload: list[dict]) -> list[dict]:
        deadline = time.monotonic() + CALL_TIME_CAP
        span = requested_log_range(payload)
        errors: dict[str, str] = {}
        for _ in range(3):
            usable, wait = POOL.plan(self.urls, span)
            for url in usable:
                for quick_retry in (True, False):
                    try:
                        body = await self._post(url, payload, deadline, span > 0)
                    except EndpointFailure as f:
                        benched = POOL.failure(url, f, span) > 0
                        errors[host_of(url)] = str(f)
                        if f.kind == "server" and quick_retry and not benched and time.monotonic() < deadline - 1:
                            await asyncio.sleep(random.uniform(0.3, 0.7))
                            continue
                        break
                    POOL.success(url)
                    return body if isinstance(body, list) else [body]
            usable, wait = POOL.plan(self.urls, span)
            if usable:
                continue
            if wait is None or wait > min(MAX_COOLDOWN_WAIT, deadline - time.monotonic()):
                break
            await asyncio.sleep(wait)
        raise RPCError("all RPC endpoints failed: " + ("; ".join(f"{h}: {m}" for h, m in errors.items()) or "none usable"))

    async def _post(self, url: str, payload: list[dict], deadline: float, is_logs: bool) -> Any:
        try:
            resp = await self._client.post(url, json=payload, timeout=min(15.0, max(deadline - time.monotonic(), 1.0)))
            status, retry_after = resp.status_code, parse_retry_after(resp.headers.get("Retry-After"))
            try:
                body = resp.json()
            except ValueError:
                body = None
        except httpx.HTTPError:
            status, body, retry_after = 0, None, None
        classify_response(status, body, retry_after, is_logs)
        return body

    # helpers
    async def native_balance(self, address: str) -> int:
        return decode_uint(await self.call("eth_getBalance", [address, "latest"]))

    async def erc20_balances(self, owner: str, tokens: list[str]) -> list[int]:
        calls = [("eth_call", [{"to": t, "data": SEL_BALANCE_OF + _pad(owner)}, "latest"]) for t in tokens]
        return [decode_uint(r) for r in await self.batch(calls)]

    async def held_erc20_balances(self, owner: str) -> dict[str, int] | None:
        """Every NON-ZERO ERC-20 balance of a wallet in ONE call — {address: raw amount}, or None when no
        configured endpoint offers it (the caller then falls back).

        `alchemy_getTokenBalances` is an Alchemy extension. The portfolio used to send one balanceOf per
        known token instead — 2 900 of them here, ~58 batch requests per page load — which blew the free
        tier's compute-units-per-second budget and turned /portfolio into a 502 for every wallet, including
        wallets that hold nothing.
        """
        for url in list(self.urls):
            try:
                resp = await self._client.post(
                    url, json=[{"jsonrpc": "2.0", "id": 1, "method": "alchemy_getTokenBalances",
                                "params": [owner, "erc20"]}], timeout=20.0,
                )
                body = resp.json()
            except (httpx.HTTPError, ValueError):
                continue
            first = body[0] if isinstance(body, list) and body else (body if isinstance(body, dict) else None)
            if resp.status_code != 200 or not isinstance(first, dict) or "result" not in first:
                continue  # the method isn't supported here (or the node refused): try the next endpoint
            out: dict[str, int] = {}
            for item in (first.get("result") or {}).get("tokenBalances") or []:
                try:
                    raw = int(str(item.get("tokenBalance") or "0x0"), 16)
                except (TypeError, ValueError):
                    continue
                addr = str(item.get("contractAddress") or "").lower()
                if raw > 0 and is_address(addr):
                    out[addr] = raw
            return out
        return None

    async def erc20_meta(self, token: str) -> dict:
        sels = [SEL_SYMBOL, SEL_NAME, SEL_DECIMALS, SEL_TOTAL_SUPPLY]
        res = await self.batch([("eth_call", [{"to": token, "data": s}, "latest"]) for s in sels])
        return {
            "symbol": decode_string(res[0]),
            "name": decode_string(res[1]),
            "decimals": decode_uint(res[2]) if res[2] else 18,
            "total_supply": decode_uint(res[3]),
        }

    async def erc20_metas(self, tokens: list[str]) -> dict[str, dict]:
        """symbol / name / decimals for many tokens in ONE batch (3 eth_calls each, chunked by the batch size).
        A token whose calls revert gets None fields; nothing here raises for a single odd token."""
        calls = [("eth_call", [{"to": t, "data": sel}, "latest"]) for t in tokens for sel in (SEL_SYMBOL, SEL_NAME, SEL_DECIMALS)]
        res = await self.batch(calls)
        out = {}
        for i, t in enumerate(tokens):
            sym, name, dec = res[3 * i : 3 * i + 3]
            try:
                decimals = decode_uint(dec) if dec and dec != "0x" else None
                out[t] = {"symbol": decode_string(sym), "name": decode_string(name),
                          "decimals": decimals if decimals is not None and decimals <= 77 else None}
            except Exception:  # noqa: BLE001
                out[t] = {"symbol": None, "name": None, "decimals": None}
        return out

    async def block_number(self) -> int:
        return decode_uint(await self.call("eth_blockNumber", []))

    async def transfer_logs(self, token: str, from_block: int, to_block: int) -> list[dict]:
        return await self.call(
            "eth_getLogs",
            [{"address": token, "topics": [TRANSFER_TOPIC], "fromBlock": hex(from_block), "toBlock": hex(to_block)}],
        ) or []

    async def aclose(self) -> None:
        await self._client.aclose()
