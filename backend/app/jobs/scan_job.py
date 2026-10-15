"""Safety scans: on-demand queue (Redis list) + periodic refresh of stale tokens."""
import asyncio
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models import SafetyScore, Token
from app.services.engine import get_engine
from app.services.safety_service import SafetyService, ScanInProgress

log = get_logger(__name__)
QUEUE = "scan:queue"  # bulk: discovery, warmup, stale refresh
QUEUED_SET = "scan:queued"
PRIO_QUEUE = "scan:queue:prio"  # user-requested refreshes: never wait behind bulk work
PRIO_SET = "scan:queued:prio"


def stale_marker(ca: str) -> str:
    return f"safety:stale:{ca}"


async def enqueue_scan(redis: Redis, ca: str, priority: bool = False) -> bool:
    """Queue a scan, deduped. Bulk is capped (SCAN_QUEUE_MAX); priority is not and jumps the bulk queue."""
    if priority:
        # a bulk entry for the same token is now redundant
        await redis.lrem(QUEUE, 0, ca)
        await redis.srem(QUEUED_SET, ca)
        if await redis.sadd(PRIO_SET, ca):
            await redis.rpush(PRIO_QUEUE, ca)
            return True
        return False
    if await redis.sismember(PRIO_SET, ca):
        return False
    if await redis.llen(QUEUE) >= get_settings().scan_queue_max:
        return False  # bounded: refresh_stale re-adds what was dropped
    if await redis.sadd(QUEUED_SET, ca):
        await redis.rpush(QUEUE, ca)
        return True
    return False


async def request_refresh(redis: Redis, ca: str) -> None:
    """User asked for a fresh scan: invalidate what GET would serve, then queue at top priority."""
    await redis.delete(f"safety:{ca}")
    await redis.set(stale_marker(ca), "1", ex=get_settings().refresh_marker_ttl_s)
    await enqueue_scan(redis, ca, priority=True)


async def run_scan(redis: Redis, ca: str, priority: bool = False) -> None:
    if priority and not await redis.exists(stale_marker(ca)):
        return  # someone (the API's GET) already produced the fresh scan
    async with SessionLocal() as db:
        try:
            await SafetyService(db, redis, get_engine()).scan(ca, priority=priority)
        except ScanInProgress:
            log.info("scan_skip_in_progress", ca=ca)
        except Exception as e:  # noqa: BLE001
            log.error("scan_failed", ca=ca, error=str(e))


async def _drain(redis: Redis, queue: str, queued: str, max_items: int, concurrency: int, priority: bool) -> int:
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks: list[asyncio.Task] = []

    async def one(ca: str) -> None:
        async with sem:
            try:
                await run_scan(redis, ca, priority)
            finally:
                await redis.srem(queued, ca)  # allow it to be queued again once it has run

    for _ in range(max_items):
        ca = await redis.lpop(queue)
        if ca is None:
            break
        tasks.append(asyncio.create_task(one(ca)))
    if tasks:
        await asyncio.gather(*tasks)
    return len(tasks)


async def drain_priority(redis: Redis) -> int:
    """User refreshes: polled every second, own concurrency, independent of the bulk drain."""
    s = get_settings()
    return await _drain(redis, PRIO_QUEUE, PRIO_SET, s.scan_concurrency, s.scan_concurrency, True)


async def drain_queue(redis: Redis, max_items: int | None = None) -> int:
    """Bulk: a bounded number per tick, run `scan_concurrency` at a time."""
    s = get_settings()
    return await _drain(redis, QUEUE, QUEUED_SET, max_items or s.scan_bulk_per_tick, s.scan_concurrency, False)


POPULAR = "safety:popular"


async def record_hit(redis: Redis, ca: str) -> None:
    """Popularity for cache warmup (decays: the set is trimmed and expires daily)."""
    await redis.zincrby(POPULAR, 1, ca)
    await redis.expire(POPULAR, 86400)


async def warmup(redis: Redis) -> int:
    """Re-scan the most requested tokens shortly before their cached verdict expires."""
    s = get_settings()
    top = await redis.zrevrange(POPULAR, 0, s.warmup_tokens - 1)
    n = 0
    for ca in top:
        ttl = await redis.ttl(f"safety:{ca}")
        if ttl < 120:  # missing (-2) or about to expire
            n += await enqueue_scan(redis, ca)
    await redis.zremrangebyrank(POPULAR, 0, -(s.warmup_tokens * 5) - 1)
    return n


async def refresh_stale(redis: Redis, batch: int | None = None) -> int:
    """Never-scanned tokens first, then the stalest; only as many as the bulk queue has room for."""
    s = get_settings()
    room = s.scan_queue_max - await redis.llen(QUEUE)
    batch = min(batch or s.refresh_batch, max(room, 0))
    if batch <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=s.safety_stale_min)
    async with SessionLocal() as db:
        last = select(SafetyScore.token_id, func.max(SafetyScore.ts).label("ts")).group_by(SafetyScore.token_id).subquery()
        rows = await db.execute(
            select(Token.address).outerjoin(last, last.c.token_id == Token.id)
            .where((last.c.ts.is_(None)) | (last.c.ts < cutoff))
            .order_by(last.c.ts.asc().nulls_first()).limit(batch)
        )
        stale = [a for (a,) in rows]
    queued = sum([await enqueue_scan(redis, ca) for ca in stale])
    log.info("refresh_stale_enqueued", count=queued, queue=await redis.llen(QUEUE))
    return queued
