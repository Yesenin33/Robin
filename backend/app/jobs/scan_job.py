"""Safety scans: on-demand queue (Redis list) + periodic refresh of stale tokens."""
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
QUEUE = "scan:queue"
QUEUED_SET = "scan:queued"


async def enqueue_scan(redis: Redis, ca: str) -> bool:
    """Dedupe via a set so the same CA isn't queued twice."""
    if await redis.sadd(QUEUED_SET, ca):
        await redis.rpush(QUEUE, ca)
        return True
    return False


async def run_scan(redis: Redis, ca: str) -> None:
    async with SessionLocal() as db:
        try:
            await SafetyService(db, redis, get_engine()).scan(ca)
        except ScanInProgress:
            log.info("scan_skip_in_progress", ca=ca)
        except Exception as e:  # noqa: BLE001
            log.error("scan_failed", ca=ca, error=str(e))


async def drain_queue(redis: Redis, max_items: int = 20) -> int:
    done = 0
    while done < max_items and (ca := await redis.lpop(QUEUE)):
        await redis.srem(QUEUED_SET, ca)
        await run_scan(redis, ca)
        done += 1
    return done


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


async def refresh_stale(redis: Redis, batch: int = 50) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=get_settings().safety_stale_min)
    async with SessionLocal() as db:
        last = select(SafetyScore.token_id, func.max(SafetyScore.ts).label("ts")).group_by(SafetyScore.token_id).subquery()
        rows = await db.execute(
            select(Token.address).outerjoin(last, last.c.token_id == Token.id)
            .where((last.c.ts.is_(None)) | (last.c.ts < cutoff)).limit(batch)
        )
        stale = [a for (a,) in rows]
    for ca in stale:
        await enqueue_scan(redis, ca)
    log.info("refresh_stale_enqueued", count=len(stale))
    return len(stale)
