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
from app.services import chain_index
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
