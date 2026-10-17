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


GECKO_BATCH = "https://api.geckoterminal.com/api/v2/networks/{chain}/tokens/multi/{addrs}"
GECKO_MAX = 30  # GeckoTerminal's limit per request
GECKO_HIT_TTL_S = 7 * 86400  # a real logo does not change
GECKO_MISS_TTL_S = 1800  # a token that launched a minute ago usually has none YET: ask again soon


def _ok_image(url) -> str | None:
    u = str(url or "").strip()
    return u if u.startswith("https://") and len(u) <= 512 else None


async def gecko_images(self, addresses: list[str], chain: str = "robinhood") -> dict[str, str]:
    """{address: logo_url} from GeckoTerminal — the source that actually carries art for this chain.

    Measured before wiring this in: DexScreener had an image for **0 of the owner's 47 holdings** and for
    **0 of the 12 newest tokens** on the chain; GeckoTerminal filled **4 582 of 4 624** catalogue rows and
    had one for 1 of those 12. Batched (30 per request), cached, and misses are re-asked on a short TTL on
    purpose: a freshly launched token often gets its art minutes later, and we want to pick it up then
    instead of waiting for a backfill.
    """
    addrs = list(dict.fromkeys(a.lower() for a in addresses))
    if not addrs:
        return {}
    cached = await self.redis.mget([f"geo:{a}" for a in addrs])
    out: dict[str, str] = {}
    todo: list[str] = []
    for a, c in zip(addrs, cached):
        if c is None:
            todo.append(a)
        elif c:
            out[a] = c
    for i in range(0, len(todo), GECKO_MAX):
        chunk = todo[i : i + GECKO_MAX]
        try:
            r = await self._client.get(GECKO_BATCH.format(chain=chain, addrs=",".join(chunk)))
            r.raise_for_status()
            data = (r.json() or {}).get("data") or []
        except Exception as e:  # noqa: BLE001 — art is decoration: a failed lookup must never fail a page
            log.warning("gecko_images_failed", error=str(e), tokens=len(chunk))
            continue
        found: dict[str, str] = {}
        for d in data:
            at = (d or {}).get("attributes") or {}
            a = str(at.get("address") or "").lower()
            if a and (u := _ok_image(at.get("image_url"))):
                found[a] = u
        for a in chunk:
            await self.redis.set(f"geo:{a}", found.get(a, ""),
                                 ex=GECKO_HIT_TTL_S if a in found else GECKO_MISS_TTL_S)
            if a in found:
                out[a] = found[a]
    return out


MarketClient.gecko_images = gecko_images
