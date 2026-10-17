"""Pool/token discovery: index new Uniswap V4 pools on Robinhood Chain and keep `tokens` fresh.

Source of truth is the PoolManager `Initialize` event; a Redis cursor makes each run incremental.
New tokens get on-chain metadata (symbol/decimals) now and price/liquidity from their first safety scan.
"""
import asyncio

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.jobs.scan_job import enqueue_scan
from app.models import Token
from app.services import chain_index, pricing
from app.services.chain import make_rpc
from app.services.copy_service import ensure_tokens
from app.services.intelligence.rpc import ZERO

log = get_logger(__name__)
CURSOR = "idx:pools:cursor"
# how long before we ask the art sources about a token again that had none (a fresh token gets its logo
# minutes later; a spam airdrop never will, and this keeps us from hammering a rate-limited API)
ART_MISS_TTL_S = 6 * 3600
MAX_RANGE_PER_RUN = 200_000  # bound one run; the cursor carries on next time


async def discover_pools(redis: Redis, from_block: int | None = None, rpc=None) -> dict:
    s = get_settings()
    rpc = rpc or make_rpc()
    head = await asyncio.to_thread(rpc.block_number)
    cursor = await redis.get(CURSOR)
    start = from_block if from_block is not None else int(cursor) + 1 if cursor else max(0, head - s.discovery_lookback_blocks)
    end = min(head, start + MAX_RANGE_PER_RUN - 1)
    if start > end:
        return {"pools": 0, "new_tokens": 0, "head": head}

    pools = await asyncio.to_thread(chain_index.new_pools, rpc, start, end, s.log_chunk_blocks)
    async with SessionLocal() as db:
        await pricing.save_pools(db, {p.pool_id: (p.currency0, p.currency1) for p in pools},
                                 {p.pool_id: p.block for p in pools},
                                 {p.pool_id: (p.fee, p.tick_spacing, p.hooks) for p in pools})
    weth = s.weth_address.lower() or None
    by_token: dict[str, chain_index.Pool] = {}
    for p in pools:
        for c in (p.currency0, p.currency1):
            if c not in (ZERO, weth):
                by_token.setdefault(c, p)

    new: list[str] = []
    if by_token:
        async with SessionLocal() as db:
            tokens, new = await ensure_tokens(db, rpc, set(by_token))
            for addr, p in by_token.items():
                t: Token = tokens[addr]
                if t.pool_init_block is None:
                    t.pool_init_block = p.block
                if not t.pair_address:
                    t.pair_address = p.pool_id
            await db.commit()
        for addr in new:
            await enqueue_scan(redis, addr)  # brings price, liquidity and the verdict
    await redis.set(CURSOR, end)
    log.info("pools_discovered", start=start, end=end, pools=len(pools), new_tokens=len(new))
    return {"pools": len(pools), "new_tokens": len(new), "start": start, "end": end, "head": head}


async def tracked_tokens(limit: int) -> list[Token]:
    """Newest pools first — where early-buyer signal lives."""
    async with SessionLocal() as db:
        rows = await db.execute(select(Token).order_by(Token.pool_init_block.desc().nulls_last(), Token.id.desc()).limit(limit))
        return list(rows.scalars())


async def backfill_pool_keys(limit: int = 100, rpc=None) -> int:
    """Pools stored before the key parts were kept: read each pool's Initialize log in the ONE block that created
    it (bounded work per run). After this the swap never needs a historical log read."""
    from app.models import Pool

    rpc = rpc or make_rpc()
    done = 0
    async with SessionLocal() as db:
        rows = (await db.execute(select(Pool).where(Pool.fee.is_(None), Pool.block.is_not(None)).limit(limit))).scalars().all()
        for row in rows:
            try:
                logs = await asyncio.to_thread(
                    rpc.call, "eth_getLogs", [{"address": chain_index.POOL_MANAGER, "topics": [chain_index.INITIALIZE_TOPIC, row.pool_id],
                                              "fromBlock": hex(row.block), "toBlock": hex(row.block)}])
            except Exception as e:  # noqa: BLE001 — try again next run
                log.warning("pool_key_backfill_failed", pool=row.pool_id, error=str(e))
                continue
            for lg in logs or []:
                if len(lg.get("topics") or []) == 4 and (p := chain_index.parse_initialize(lg)).fee is not None:
                    row.fee, row.tick_spacing, row.hooks = p.fee, p.tick_spacing, p.hooks
                    done += 1
                    break
        await db.commit()
    log.info("pool_keys_backfilled", pools=done, tried=len(rows))
    return done


async def backfill_images(batch: int = 300, redis=None) -> int:
    """Token art for rows that have none.

    Two sources, in the order that matches reality on this chain: GeckoTerminal carries art for ~99 % of
    the catalogue while DexScreener had an image for **0 of the owner's 47 holdings** and 0 of the 12
    newest tokens (both measured). GeckoTerminal first, DexScreener for whatever is left.

    A token neither source has art for is NOT written back as "looked, nothing there": a token launched a
    minute ago genuinely has no logo yet, and an empty string in the column would keep it out of this
    query forever. The miss is recorded in Redis with a TTL instead, so it is retried — just not every
    five minutes.
    """
    from app.core.redis import get_redis
    from app.services.market import MarketClient

    r = redis or get_redis()
    market = MarketClient(r)
    found = 0
    async with SessionLocal() as db:
        rows = (await db.execute(select(Token).where(Token.image_url.is_(None)).limit(batch))).scalars().all()
        if not rows:
            return 0
        addrs = [t.address.lower() for t in rows]
        # skip the ones we asked about moments ago; the rest are fair game
        misses = await r.mget([f"artmiss:{a}" for a in addrs])
        todo = [t for t, m in zip(rows, misses) if m is None]
        by_addr = {t.address.lower(): t for t in todo}
        art: dict[str, str] = {}
        if todo:
            try:
                art = await market.gecko_images([t.address for t in todo])
            except Exception as e:  # noqa: BLE001
                log.warning("images_gecko_failed", error=str(e))
            need = [t for t in todo if t.address.lower() not in art]
            if need:
                try:
                    info = await market.tokens_market([t.address for t in need])
                    art.update({a: (info.get(a) or {}).get("image_url") for a in info
                                if (info.get(a) or {}).get("image_url")})
                except Exception as e:  # noqa: BLE001
                    log.warning("images_dex_failed", error=str(e))
        for a, url in art.items():
            if (t := by_addr.get(a)) is not None and url:
                t.image_url = url
                found += 1
        await db.commit()
        missing = [t.address.lower() for t in todo if t.address.lower() not in art]
        if missing:
            pipe = r.pipeline()
            for a in missing:
                pipe.set(f"artmiss:{a}", "1", ex=ART_MISS_TTL_S)
            await pipe.execute()
    log.info("images_backfilled", tried=len(rows), asked=len(todo), found=found, miss=len(rows) - found)
    return found
