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
