"""Minimal async JSON-RPC client for Robinhood Chain (EVM)."""
import re
from typing import Any

import httpx

from app.core.config import get_settings

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
    def __init__(self, url: str | None = None, client: httpx.AsyncClient | None = None):
        if url is None:
            s = get_settings()
            url = ",".join([s.rpc_url, *s.rpc_fallback_urls.split(",")])
        self.urls = [u.strip() for u in url.split(",") if u.strip()]
        self.url = self.urls[0]
        self._client = client or httpx.AsyncClient(timeout=15)
        self._id = 0

    async def call(self, method: str, params: list[Any]) -> Any:
        return (await self.batch([(method, params)]))[0]

    async def batch(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        if not calls:
            return []
        payload = []
        for method, params in calls:
            self._id += 1
            payload.append({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        last: Exception | None = None
        for url in self.urls:  # fail over in order
            try:
                resp = await self._client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
                break
            except (httpx.HTTPError, ValueError) as e:
                last = e
        else:
            raise RPCError(f"all RPC endpoints failed: {last}")
        by_id = {r["id"]: r for r in body}
        out = []
        for req in payload:
            r = by_id.get(req["id"], {})
            if "error" in r:
                out.append(None)  # per-call failure (e.g. reverted balanceOf) shouldn't kill the batch
            else:
                out.append(r.get("result"))
        return out

    # helpers
    async def native_balance(self, address: str) -> int:
        return decode_uint(await self.call("eth_getBalance", [address, "latest"]))

    async def erc20_balances(self, owner: str, tokens: list[str]) -> list[int]:
        calls = [("eth_call", [{"to": t, "data": SEL_BALANCE_OF + _pad(owner)}, "latest"]) for t in tokens]
        return [decode_uint(r) for r in await self.batch(calls)]

    async def erc20_meta(self, token: str) -> dict:
        sels = [SEL_SYMBOL, SEL_NAME, SEL_DECIMALS, SEL_TOTAL_SUPPLY]
        res = await self.batch([("eth_call", [{"to": token, "data": s}, "latest"]) for s in sels])
        return {
            "symbol": decode_string(res[0]),
            "name": decode_string(res[1]),
            "decimals": decode_uint(res[2]) if res[2] else 18,
            "total_supply": decode_uint(res[3]),
        }

    async def block_number(self) -> int:
        return decode_uint(await self.call("eth_blockNumber", []))

    async def transfer_logs(self, token: str, from_block: int, to_block: int) -> list[dict]:
        return await self.call(
            "eth_getLogs",
            [{"address": token, "topics": [TRANSFER_TOPIC], "fromBlock": hex(from_block), "toBlock": hex(to_block)}],
        ) or []

    async def aclose(self) -> None:
        await self._client.aclose()
