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
    """Token art for rows that have none: DexScreener's batched endpoint (30 addresses per request), cached.
    A token DexScreener has no art for is marked "" and retried only by its next scan."""
    from app.core.redis import get_redis
    from app.services.market import MarketClient

    market = MarketClient(redis or get_redis())
    found = 0
    async with SessionLocal() as db:
        rows = (await db.execute(select(Token).where(Token.image_url.is_(None)).limit(batch))).scalars().all()
        info = await market.tokens_market([t.address for t in rows]) if rows else {}
        for t in rows:
            url = (info.get(t.address) or {}).get("image_url")
            if url:
                found += 1
            t.image_url = url or ""  # "" = looked, nothing there
        await db.commit()
    log.info("images_backfilled", tried=len(rows), found=found)
    return found
