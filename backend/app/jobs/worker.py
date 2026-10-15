"""Background worker: `python -m app.jobs.worker` (separate container from the API)."""
import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.redis import get_redis
from app.jobs.alert_job import deliver as deliver_alerts
from app.jobs.alert_job import price_moves
from app.jobs.copy_job import collect as collect_copy
from app.jobs.copy_job import drain_backfill
from app.jobs.discovery_job import discover_pools
from app.jobs.rank_job import rank_wallets
from app.jobs.scan_job import drain_priority, drain_queue, drain_retries, refresh_stale, warmup
from app.jobs.snapshot_job import snapshot_positions

log = get_logger("worker")


async def main() -> None:
    s = get_settings()
    setup_logging(s.log_level)
    redis = get_redis()
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(drain_priority, "interval", seconds=1, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(drain_queue, "interval", seconds=5, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(drain_retries, "interval", seconds=5, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(refresh_stale, "interval", minutes=s.safety_refresh_min, args=[redis], max_instances=1)
    sched.add_job(snapshot_positions, "interval", minutes=s.position_snapshot_min, args=[redis], max_instances=1)
    sched.add_job(collect_copy, "interval", minutes=2, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(drain_backfill, "interval", seconds=20, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(discover_pools, "interval", minutes=5, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(rank_wallets, "interval", minutes=60, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(warmup, "interval", minutes=2, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(deliver_alerts, "interval", seconds=30, args=[redis], max_instances=1, coalesce=True)
    sched.add_job(price_moves, "interval", minutes=10, args=[redis], max_instances=1, coalesce=True)
    sched.start()
    log.info("worker_started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    sched.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
