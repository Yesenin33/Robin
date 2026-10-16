"""GET /health/sources — live status of every upstream the product depends on."""
import asyncio
import json
import time

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.redis import get_redis
from app.services.engine.loader import rpc_urls
from app.services.intelligence.config import get_chain
from app.services.intelligence.endpoints import POOL
from app.services.intelligence.http import get_json
from app.services.intelligence.rpc import Rpc

router = APIRouter(prefix="/health", tags=["meta"])
CACHE_KEY, CACHE_TTL = "health:sources", 30


async def _probe(name: str, fn, timeout: float = 12) -> dict:
    t0 = time.perf_counter()
    try:
        detail = await asyncio.wait_for(asyncio.to_thread(fn) if not asyncio.iscoroutinefunction(fn) else fn(), timeout)
        return {"name": name, "ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": detail}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "ok": False, "ms": round((time.perf_counter() - t0) * 1000), "error": str(e)[:200]}


def _redact(url: str) -> str:
    """Hide API keys embedded in RPC URLs (e.g. Alchemy /v2/<key>)."""
    head, _, path = url.partition("://")
    host, _, rest = path.partition("/")
    return f"{head}://{host}/" + "/".join(p if len(p) < 24 else p[:4] + "…" for p in rest.split("/")) if rest else url


async def collect() -> dict:
    s, cfg = get_settings(), get_chain("robinhood")

    async def db():
        async with SessionLocal() as session:
            await session.execute(text("select 1"))
        return "select 1"

    async def redis():
        return await get_redis().ping()

    probes = [_probe("postgres", db), _probe("redis", redis)]
    from app.services.intelligence.config import logs_urls

    for url in dict.fromkeys([*rpc_urls().split(","), *logs_urls()]):
        probes.append(_probe(f"rpc {_redact(url)}", lambda u=url: {"block": Rpc(u).block_number(),
                                                                   "pool": POOL.snapshot(u)}))
    if cfg.explorer_api:
        probes.append(_probe("explorer", lambda: {"total_blocks": get_json(
            f"{cfg.explorer_api.rstrip('/')}/api/v2/stats", tries=1).get("total_blocks")}))
    from app.services.intelligence.config import browser_url

    if browser_url():
        probes.append(_probe("explorer browser", lambda: get_json(f"{browser_url()}/health", tries=1)))
    probes.append(_probe("dexscreener", lambda: {"pairs": len(get_json(
        "https://api.dexscreener.com/latest/dex/search?q=robinhood", tries=1).get("pairs") or [])}))
    probes.append(_probe("geckoterminal", lambda: {"network": (get_json(
        f"https://api.geckoterminal.com/api/v2/networks/{cfg.geckoterminal_network}/dexes", tries=1).get("data") or [{}])[0].get("id")}))
    results = await asyncio.gather(*probes)
    results.append({"name": "telegram", "ok": bool(s.telegram_bot_token), "detail": "configured" if s.telegram_bot_token else "TELEGRAM_BOT_TOKEN unset"})
    results.append({"name": "web_push", "ok": bool(s.vapid_private_key), "detail": "configured" if s.vapid_private_key else "VAPID keys unset"})
    critical = {"postgres", "redis"}
    rpc_ok = any(r["ok"] for r in results if r["name"].startswith("rpc"))
    status = "ok" if all(r["ok"] for r in results) else "down" if not rpc_ok or any(
        not r["ok"] for r in results if r["name"] in critical) else "degraded"
    return {"status": status, "sources": results}


@router.get("/sources")
async def sources(fresh: bool = False):
    redis = get_redis()
    if not fresh and (cached := await redis.get(CACHE_KEY)):
        return json.loads(cached)
    out = await collect()
    await redis.set(CACHE_KEY, json.dumps(out, default=str), ex=CACHE_TTL)
    return out
