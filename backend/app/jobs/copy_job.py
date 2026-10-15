"""Copy feed jobs.

collect  — live: every followed wallet's swaps (any token) since the last cursor, via two
           topic-filtered eth_getLogs; ETH legs from receipts; new rows -> follower alerts.
backfill — on first follow: the wallet's history from the explorer (token-transfers API),
           falling back to an RPC log scan, so the feed isn't empty for hours.
"""
import asyncio
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.jobs.scan_job import enqueue_scan
from app.models import CopyTarget
from app.services import alert_service, chain_index, copy_service
from app.services.chain import make_rpc
from app.services.chain_index import POOL_MANAGER, Trade
from app.services.intelligence.config import get_chain
from app.services.intelligence.http import HttpError, get_json
from app.services.market import MarketClient

log = get_logger(__name__)
CURSOR = "copy:cursor"
BACKFILL_QUEUE = "copy:backfill"


async def _store(redis: Redis, rpc, trades: list[Trade], alert: bool) -> int:
    if not trades:
        return 0
    eth_usd = await MarketClient(redis).native_price_usd(get_settings().native_symbol)
    async with SessionLocal() as db:
        tokens, new = await copy_service.ensure_tokens(db, rpc, {t.token for t in trades})
        await db.commit()
        events = await copy_service.store_trades(db, trades, tokens, eth_usd)
        if alert and events:
            await alert_service.on_copy_events(db, redis, events)
    for addr in new:
        await enqueue_scan(redis, addr)  # so the feed can say whether the token is safe
    return len(events)


async def collect(redis: Redis, rpc=None) -> int:
    s = get_settings()
    async with SessionLocal() as db:
        wallets = sorted({a for (a,) in (await db.execute(
            select(CopyTarget.address).where(CopyTarget.active.is_(True)).distinct())).all()})
    if not wallets:
        return 0
    rpc = rpc or make_rpc()
    try:
        head = await asyncio.to_thread(rpc.block_number)
        cursor = await redis.get(CURSOR)
        start = int(cursor) + 1 if cursor else max(0, head - s.copy_lookback_blocks)
        trades: list[Trade] = []
        for i in range(0, len(wallets), 50):  # keep the topic OR-list small
            trades += await asyncio.to_thread(chain_index.wallet_trades, rpc, wallets[i:i + 50], start, head,
                                              s.log_chunk_blocks)
        await asyncio.to_thread(chain_index.enrich, rpc, trades, s.weth_address or None)
    except Exception as e:  # noqa: BLE001 — next run retries from the same cursor
        log.error("copy_collect_failed", error=str(e))
        return 0
    n = await _store(redis, rpc, trades, alert=True)
    await redis.set(CURSOR, head)
    log.info("copy_collected", wallets=len(wallets), start=start, head=head, trades=len(trades), events=n)
    return n


# ---------- backfill ----------

async def enqueue_backfill(redis: Redis, wallet: str) -> None:
    if await redis.set(f"copy:backfilled:{wallet}", "1", nx=True, ex=7 * 86400):
        await redis.rpush(BACKFILL_QUEUE, wallet)


def explorer_trades(wallet: str, pages: int, fetch=None) -> list[Trade]:
    """Swaps from Blockscout /api/v2/addresses/{a}/token-transfers (PoolManager as counterparty)."""
    fetch = fetch or get_json
    base = get_chain("robinhood").explorer_api.rstrip("/")
    if not base:
        raise HttpError("explorer not configured")
    url = f"{base}/api/v2/addresses/{wallet}/token-transfers?type=ERC-20"
    out: list[Trade] = []
    params = None
    for _ in range(pages):
        page = fetch(url + ("&" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""))
        for it in page.get("items", []):
            frm = (it.get("from") or {}).get("hash", "").lower()
            to = (it.get("to") or {}).get("hash", "").lower()
            if POOL_MANAGER not in (frm, to):
                continue
            tok = it.get("token") or {}
            ts = it.get("timestamp")
            out.append(Trade(
                wallet=wallet, token=(tok.get("address_hash") or tok.get("address") or "").lower(),
                side="buy" if frm == POOL_MANAGER else "sell",
                token_raw=int((it.get("total") or {}).get("value") or 0),
                tx=it.get("transaction_hash") or it.get("tx_hash"), block=int(it.get("block_number") or 0),
                log_index=int(it.get("log_index") or 0),
                ts=int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()) if ts else None,
            ))
        params = page.get("next_page_params")
        if not params:
            break
    return [t for t in out if t.token and t.tx]


async def backfill(redis: Redis, wallet: str, rpc=None, fetch=None) -> int:
    s = get_settings()
    rpc = rpc or make_rpc()
    source = "explorer"
    try:
        trades = await asyncio.to_thread(explorer_trades, wallet, s.backfill_pages, fetch)
    except Exception as e:  # noqa: BLE001
        log.warning("backfill_explorer_failed", wallet=wallet, error=str(e))
        source = "rpc"
        head = await asyncio.to_thread(rpc.block_number)
        trades = await asyncio.to_thread(chain_index.wallet_trades, rpc, [wallet], max(0, head - s.backfill_blocks),
                                         head, s.log_chunk_blocks)
    try:
        await asyncio.to_thread(chain_index.enrich, rpc, trades, s.weth_address or None)
    except Exception as e:  # noqa: BLE001 — rows are still useful without ETH legs
        log.warning("backfill_enrich_failed", wallet=wallet, error=str(e))
    n = await _store(redis, rpc, trades, alert=False)  # history isn't news: no alerts
    log.info("copy_backfilled", wallet=wallet, source=source, trades=len(trades), events=n)
    return n


async def drain_backfill(redis: Redis, max_items: int = 5) -> int:
    done = 0
    while done < max_items and (wallet := await redis.lpop(BACKFILL_QUEUE)):
        try:
            await backfill(redis, wallet)
        except Exception as e:  # noqa: BLE001
            log.error("backfill_failed", wallet=wallet, error=str(e))
            await redis.delete(f"copy:backfilled:{wallet}")  # allow a retry on next follow
        done += 1
    return done
