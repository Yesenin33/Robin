"""A token that fails once ends up with a complete report after the backoff retry; a token that keeps failing is
retried a bounded number of times and then left alone (no hot loop through warmup / refresh_stale / discovery)."""
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.jobs import rank_job, scan_job
from app.models import SafetyScore, Token
from app.services.chain_index import Trade
from app.services.intelligence import ScanUnavailable
from app.services.intelligence.rpc import RpcError
from app.services.safety_service import SafetyService
from tests.test_safety_service import FakeEngine, report

CA = "0x" + "ab" * 20


class Flaky(FakeEngine):
    """Fails `fail_times` times (raises, or returns an incomplete report), then returns the full report."""

    def __init__(self, r, fail_times=1, mode="raise"):
        super().__init__(r)
        self.fail_times, self.mode = fail_times, mode

    def scan(self, address):
        self.calls += 1
        if self.calls <= self.fail_times:
            if self.mode == "raise":
                raise ScanUnavailable("all RPC endpoints failed: HTTP 500")
            import copy

            bad = copy.deepcopy(self.r)  # what a throttled explorer produces: no holders, an error recorded
            bad.holders_count = bad.real_hands_pct = bad.top_holder_pct = None
            bad.errors = ["holders_explorer: HTTP Error 429: Too Many Requests"]
            return bad
        import copy

        return copy.deepcopy(self.r)


async def make_due(redis):
    for member in await redis.zrange(scan_job.RETRY_ZSET, 0, -1):
        await redis.zadd(scan_job.RETRY_ZSET, {member: 0})


@pytest.fixture
def use(monkeypatch):
    def _use(engine):
        monkeypatch.setattr(scan_job, "get_engine", lambda: engine)
        return engine
    return _use


def test_retry_delays_back_off_and_are_jittered():
    assert [scan_job.retry_delay(n, rand=lambda a, b: 1.0) for n in (1, 2, 3, 4, 9)] == [30, 120, 480, 480, 480]
    assert scan_job.retry_delay(1, rand=lambda a, b: a) == pytest.approx(24)
    assert scan_job.retry_delay(1, rand=lambda a, b: b) == pytest.approx(36)


@pytest.mark.parametrize("mode", ["raise", "incomplete"])
async def test_token_that_fails_once_ends_with_a_complete_report(appdb, redis, use, mode):
    engine = use(Flaky(report(), fail_times=1, mode=mode))
    await scan_job.run_scan(redis, CA)  # attempt 1 fails
    assert await redis.get(scan_job.fails_key(CA)) == "1"
    assert await redis.zcard(scan_job.RETRY_ZSET) == 1
    assert await scan_job.enqueue_scan(redis, CA) is False  # in backoff: warmup/refresh_stale/discovery can't re-queue it
    assert await scan_job.drain_retries(redis) == 0  # not due yet
    await make_due(redis)
    assert await scan_job.drain_retries(redis) == 1  # the backoff elapsed: queued again
    assert await scan_job.drain_queue(redis) == 1  # attempt 2 succeeds
    assert engine.calls == 2
    assert await redis.get(scan_job.fails_key(CA)) is None and not await redis.exists(scan_job.cool_key(CA))
    async with appdb() as db:
        latest = (await db.execute(select(SafetyScore).order_by(SafetyScore.ts.desc()).limit(1))).scalar_one()
        assert latest.metrics["incomplete"] is False
        out = await SafetyService(db, redis, engine).get(CA)
    assert [m.value for m in out.metrics][:2] == ["18,420", "94%"]  # no dashes


async def test_failing_token_is_retried_a_bounded_number_of_times_then_left_alone(appdb, redis, use):
    engine = use(Flaky(report(), fail_times=99))
    await scan_job.run_scan(redis, CA)
    for _ in range(get_settings().scan_retry_max):  # three retries
        await make_due(redis)
        assert await scan_job.drain_retries(redis) == 1
        await scan_job.drain_queue(redis)
    assert engine.calls == 1 + get_settings().scan_retry_max
    assert await redis.zcard(scan_job.RETRY_ZSET) == 0  # gave up: nothing scheduled
    ttl = await redis.ttl(scan_job.cool_key(CA))
    assert 3500 < ttl <= 3600
    # every bulk path is shut for an hour...
    assert await scan_job.enqueue_scan(redis, CA) is False
    async with appdb() as db:
        db.add(Token(address=CA))
        await db.commit()
    assert await scan_job.refresh_stale(redis) == 0
    assert await scan_job.drain_queue(redis) == 0 and engine.calls == 4
    # ...except a user pressing refresh: clean slate, immediate priority scan
    engine.fail_times = 0
    await scan_job.request_refresh(redis, CA)
    assert not await redis.exists(scan_job.cool_key(CA))
    assert await scan_job.drain_priority(redis) == 1 and engine.calls == 5


async def test_priority_retry_stays_priority(appdb, redis, use):
    use(Flaky(report(), fail_times=1))
    await scan_job.request_refresh(redis, CA)
    await scan_job.drain_priority(redis)
    assert await redis.zrange(scan_job.RETRY_ZSET, 0, -1) == [f"p:{CA}"]
    await make_due(redis)
    await scan_job.drain_retries(redis)
    assert await redis.lrange(scan_job.PRIO_QUEUE, 0, -1) == [CA]  # back in the priority queue, marker restored
    assert await scan_job.drain_priority(redis) == 1
    assert not await redis.exists(scan_job.stale_marker(CA))


async def test_incomplete_result_is_not_cached_for_long_nor_served_from_db_outside_backoff(appdb, redis):
    engine = Flaky(report(), fail_times=1, mode="incomplete")
    async with appdb() as db:
        svc = SafetyService(db, redis, engine)
        first = await svc.scan(CA)
        assert svc.last_incomplete and first.metrics[0].value == "—"
        assert 0 < await redis.ttl(f"safety:{CA}") <= 60  # short-lived, not the usual 10 minutes
        await redis.flushall()  # cache gone, no backoff recorded: the dashes row must not be served from the DB
        second = await SafetyService(db, redis, engine).get(CA)
        assert second.metrics[0].value == "18,420" and engine.calls == 2  # re-scanned on demand
        # while a token IS in backoff, an incomplete row is served rather than re-scanned in the request path
        engine2 = Flaky(report(), fail_times=99, mode="incomplete")
        await redis.flushall()
        db.add(SafetyScore(token_id=(await db.execute(select(Token.id))).scalar_one(), score=50, verdict="RISKY",
                           risks=[], metrics={"summary": "x", "items": [], "incomplete": True}))
        await db.commit()
        await redis.set(scan_job.cool_key(CA), "1", ex=60)
        served = await SafetyService(db, redis, engine2).get(CA)
        assert engine2.calls == 0 and served.verdict == "UNKNOWN"  # no metrics behind the stored RISKY


async def test_rank_skips_a_token_that_just_failed(appdb, redis, monkeypatch):
    calls = []

    def boom(rpc, token, *a, **k):
        calls.append(token)
        raise RpcError("all RPC endpoints failed: HTTP 429")

    class R:
        def block_number(self):
            return 1000

        def codes(self, addrs):
            return {}

    monkeypatch.setattr(rank_job.chain_index, "token_trades", boom)
    async with appdb() as db:
        db.add(Token(address=CA, pool_init_block=10))
        await db.commit()
    await rank_job.rank_wallets(redis, rpc=R())
    await rank_job.rank_wallets(redis, rpc=R())
    assert calls == [CA]  # the second run left the failing token alone
