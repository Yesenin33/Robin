"""Lightweight market data used outside full token scans (portfolio pricing)."""
import json

import httpx
from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger(__name__)
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/{sym}-USD/spot"


class MarketClient:
    def __init__(self, redis: Redis, client: httpx.AsyncClient | None = None):
        self.redis = redis
        self._client = client or httpx.AsyncClient(timeout=10)

    async def native_price_usd(self, symbol: str = "ETH") -> float | None:
        key = f"px:native:{symbol}"
        cached = await self.redis.get(key)
        if cached:
            return float(cached)
        try:
            r = await self._client.get(COINBASE_SPOT.format(sym=symbol))
            r.raise_for_status()
            price = float(r.json()["data"]["amount"])
        except Exception as e:  # noqa: BLE001
            log.warning("native_price_failed", error=str(e))
            return None
        await self.redis.set(key, json.dumps(price), ex=60)
        return price


DEX_BATCH = "https://api.dexscreener.com/tokens/v1/{chain}/{addrs}"
DEX_MAX = 30  # DexScreener's limit per request
MARKET_TTL_S = 60


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def image_of(pair: dict) -> str | None:
    """DexScreener token art. Only https URLs, bounded length: it ends up in an <img src> and a column."""
    url = ((pair.get("info") or {}).get("imageUrl") or "").strip()
    return url if url.startswith("https://") and len(url) <= 512 else None


def best_pairs(pairs: list[dict]) -> dict[str, dict]:
    """token address -> market facts taken from its deepest pool (liquidity decides which price to trust)."""
    out: dict[str, dict] = {}
    for p in pairs:
        base = p.get("baseToken") or {}
        addr = str(base.get("address") or "").lower()
        liq = _num((p.get("liquidity") or {}).get("usd")) or 0.0
        if addr and (addr not in out or liq > out[addr]["liquidity_usd"]):
            out[addr] = {
                "symbol": base.get("symbol"), "name": base.get("name"), "price_usd": _num(p.get("priceUsd")),
                "price_change_24h": _num((p.get("priceChange") or {}).get("h24")), "liquidity_usd": liq,
                "pool_id": (p.get("pairAddress") or "").lower() or None,  # a v4 pool id on this chain
                "image_url": image_of(p),
            }
    return out


async def tokens_market(self, addresses: list[str], chain: str = "robinhood") -> dict[str, dict]:
    """{address: market facts} for the addresses DexScreener knows; absent = no market data (not an error).
    Batched (30 per request) with a short per-address Redis cache, negative results included."""
    addrs = list(dict.fromkeys(a.lower() for a in addresses))
    if not addrs:
        return {}
    cached = await self.redis.mget([f"mkt:{a}" for a in addrs])
    out: dict[str, dict] = {}
    todo = []
    for a, c in zip(addrs, cached):
        if c is None:
            todo.append(a)
        elif (v := json.loads(c)):
            out[a] = v
    for i in range(0, len(todo), DEX_MAX):
        chunk = todo[i : i + DEX_MAX]
        try:
            r = await self._client.get(DEX_BATCH.format(chain=chain, addrs=",".join(chunk)))
            r.raise_for_status()
            found = best_pairs([p for p in r.json() if p.get("chainId") == chain])
        except Exception as e:  # noqa: BLE001 — one bad batch must not fail the page; those tokens show without a price
            log.warning("token_market_failed", error=str(e), tokens=len(chunk))
            continue
        for a in chunk:
            await self.redis.set(f"mkt:{a}", json.dumps(found.get(a, {})), ex=MARKET_TTL_S)
            if a in found:
                out[a] = found[a]
    return out


MarketClient.tokens_market = tokens_market
