"""Safety scans: on-demand queue (Redis list) + periodic refresh of stale tokens."""
import asyncio
import random
import time
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
PRIO_ACTIVE = "scan:prio:active"  # >0 while a priority scan runs


RETRY_ZSET = "scan:retry"  # members "p:<ca>" / "b:<ca>", score = due time (epoch s)


def stale_marker(ca: str) -> str:
    return f"safety:stale:{ca}"


def bg_marker(ca: str) -> str:
    return f"safety:bg:{ca}"  # a verdict is being shown while this refresh runs (GET keeps serving it)


def fails_key(ca: str) -> str:
    return f"scan:fails:{ca}"  # consecutive failed/incomplete scans (expires after 6 h of quiet)


def cool_key(ca: str) -> str:
    return f"scan:cool:{ca}"  # present = in backoff: bulk jobs must not re-queue this token


async def enqueue_scan(redis: Redis, ca: str, priority: bool = False, force: bool = False) -> bool:
    """Queue a scan, deduped. Bulk is capped (SCAN_QUEUE_MAX); priority is not and jumps the bulk queue.

    A token in backoff (it failed recently) is skipped by bulk callers - discovery, warmup, refresh_stale -
    so a failing token can't be re-queued every few minutes; only its scheduled retry (`force`) or a user
    refresh gets through."""
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
    if not force and await redis.exists(cool_key(ca)):
        return False
    if await redis.llen(QUEUE) >= get_settings().scan_queue_max:
        return False  # bounded: refresh_stale re-adds what was dropped
    if await redis.sadd(QUEUED_SET, ca):
        await redis.rpush(QUEUE, ca)
        return True
    return False


async def request_refresh(redis: Redis, ca: str) -> None:
    """User asked for a fresh scan: invalidate what GET would serve, then queue at top priority."""
    await redis.delete(f"safety:{ca}", fails_key(ca), cool_key(ca))  # a user's request starts from a clean slate
    await redis.set(stale_marker(ca), "1", ex=get_settings().refresh_marker_ttl_s)
    await enqueue_scan(redis, ca, priority=True)


def retry_delay(attempt: int, rand=random.uniform) -> float:
    """Seconds before retry number `attempt` (1-based): SCAN_RETRY_DELAYS, jittered +-20%."""
    delays = [float(x) for x in get_settings().scan_retry_delays.split(",") if x.strip()] or [30.0]
    return delays[min(attempt, len(delays)) - 1] * rand(0.8, 1.2)


async def note_success(redis: Redis, ca: str) -> None:
    await redis.delete(fails_key(ca), cool_key(ca))


async def note_failure(redis: Redis, ca: str, priority: bool, reason: str) -> str:
    """Count a failed/incomplete scan. Returns "retry" (scheduled with backoff) or "gave_up" (cap reached)."""
    s = get_settings()
    n = await redis.incr(fails_key(ca))
    await redis.expire(fails_key(ca), 6 * 3600)
    if n > s.scan_retry_max:
        await redis.set(cool_key(ca), "1", ex=s.scan_give_up_cooldown_s)
        log.warning("scan_gave_up", ca=ca, attempts=n, reason=reason)
        return "gave_up"
    delay = retry_delay(n)
    await redis.zadd(RETRY_ZSET, {f"{'p' if priority else 'b'}:{ca}": time.time() + delay})
    await redis.set(cool_key(ca), "1", ex=int(delay) + 60)
    log.info("scan_retry_scheduled", ca=ca, attempt=n, of=s.scan_retry_max, in_s=round(delay), reason=reason)
    return "retry"


async def drain_retries(redis: Redis, max_items: int = 20) -> int:
    """Move retries that are due into the scan queues (past the cool-off that scheduled them)."""
    due = await redis.zrangebyscore(RETRY_ZSET, "-inf", time.time(), start=0, num=max_items)
    moved = 0
    for member in due:
        if not await redis.zrem(RETRY_ZSET, member):
            continue  # another worker took it
        kind, _, ca = member.partition(":")
        if kind == "p":
            await redis.set(stale_marker(ca), "1", ex=get_settings().refresh_marker_ttl_s)
        moved += await enqueue_scan(redis, ca, priority=kind == "p", force=True)
    return moved


async def run_scan(redis: Redis, ca: str, priority: bool = False) -> None:
    if priority and not (await redis.exists(stale_marker(ca)) or await redis.exists(bg_marker(ca))):
        return  # someone (the API's GET) already produced the fresh scan
    if priority:
        await redis.incr(PRIO_ACTIVE)
        await redis.expire(PRIO_ACTIVE, 180)
    try:
        await _run_scan(redis, ca, priority)
    finally:
        if priority:
            await redis.decr(PRIO_ACTIVE)


async def _run_scan(redis: Redis, ca: str, priority: bool) -> None:
    async with SessionLocal() as db:
        svc = SafetyService(db, redis, get_engine())
        try:
            await svc.scan(ca, priority=priority)
        except ScanInProgress:
            log.info("scan_skip_in_progress", ca=ca)
            return
        except Exception as e:  # noqa: BLE001
            log.error("scan_failed", ca=ca, error=str(e))
            await note_failure(redis, ca, priority, str(e)[:200])
            return
    if svc.last_incomplete:
        await note_failure(redis, ca, priority, "incomplete: " + "; ".join((svc.last_report.errors or [])[:2])[:200])
    else:
        await note_success(redis, ca)


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
    """Bulk: a bounded number per tick, run `scan_concurrency` at a time.

    Bulk pauses while a user's check is waiting or running: both draw on the same explorer slots, and a person
    watching a spinner outranks a catalogue refresh (measured: 47 bulk scans made a user's check take ~105 s)."""
    s = get_settings()
    if await redis.llen(PRIO_QUEUE) or await redis.exists(PRIO_ACTIVE):
        return 0
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
