"""Smart-wallet discovery: rank wallets by realised PnL on recently created pools."""
import asyncio

from redis.asyncio import Redis
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.jobs.copy_job import enqueue_backfill
from app.jobs.discovery_job import tracked_tokens
from app.models import SmartWallet
from app.services import chain_index, pricing, smart_wallets
from app.services.chain import make_rpc
from app.services.chain_index import POOL_MANAGER

log = get_logger(__name__)


async def rank_wallets(redis: Redis | None = None, rpc=None) -> int:
    s = get_settings()
    redis = redis or get_redis()
    rpc = rpc or make_rpc()
    tokens = await tracked_tokens(s.rank_tokens)
    if not tokens:
        return 0
    head = await asyncio.to_thread(rpc.block_number)
    trades: list[chain_index.Trade] = []
    for t in tokens:
        if await redis.exists(f"rank:cool:{t.address}"):
            continue  # failed recently: don't hammer the node with the same token every run
        start = max(t.pool_init_block or 0, head - s.rank_window_blocks)
        try:
            tt = await asyncio.to_thread(chain_index.token_trades, rpc, t.address, start, head, s.log_chunk_blocks,
                                         s.rank_swaps_per_token)
            trades += await pricing.enrich_and_value(SessionLocal, redis, rpc, tt)
        except Exception as e:  # noqa: BLE001 — one bad token shouldn't sink the run
            log.warning("rank_token_failed", token=t.address, error=str(e))
            await redis.set(f"rank:cool:{t.address}", "1", ex=1200)

    # routers/contracts are not people to follow
    candidates = sorted({t.wallet for t in trades})
    codes = await asyncio.to_thread(rpc.codes, candidates) if candidates else {}
    contracts = {a for a, c in codes.items() if c not in ("0x", "0x0")} | {POOL_MANAGER}
    ranked = smart_wallets.rank_tiers(trades, {t.address: t.pool_init_block for t in tokens if t.pool_init_block},
                                      s.suggested_min_closed, s.suggested_fill_to, contracts)[:100]

    async with SessionLocal() as db:
        await db.execute(delete(SmartWallet))
        for w in ranked:
            db.add(SmartWallet(address=w.address, score=w.score, realised_pnl_usd=w.realised_pnl_usd,
                               invested_usd=w.invested_usd, trades=w.trades, wins=w.wins, losses=w.losses,
                               tokens=len(w.tokens), early_buys=w.early_buys, last_block=w.last_block,
                               token_list=w.tokens))
        await db.commit()
    for w in ranked[: s.spectate_wallets]:  # history for the fresh-account feed (no-op if already backfilled)
        await enqueue_backfill(redis, w.address)
    valued = sum(t.usd is not None for t in trades)
    log.info("wallets_ranked", tokens=len(tokens), trades=len(trades), valued=valued, ranked=len(ranked))
    return len(ranked)
