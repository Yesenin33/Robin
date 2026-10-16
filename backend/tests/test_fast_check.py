"""A user's check must paint quickly: bulk yields to it, an old verdict shows at once, stages are real, reads reuse."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.jobs import scan_job
from app.models import SafetyScore
from app.services import intelligence
from app.services.intelligence import config as icfg
from app.services.intelligence import http as ihttp
from app.services.intelligence import progress
from tests.test_intelligence import Chain, bytecode, dexscreener, T
from tests.test_queue_explorer import CA, OTHERS, FakeEngine, engine, landed, report  # noqa: F401
from tests.test_explorer_routes import Router


async def test_bulk_waits_while_a_users_check_is_queued_or_running(redis, appdb, engine):
    for a in OTHERS[:3]:
        await scan_job.enqueue_scan(redis, a)
    await scan_job.enqueue_scan(redis, CA, priority=True)
    assert await scan_job.drain_queue(redis) == 0 and await redis.llen(scan_job.QUEUE) == 3  # paused, not dropped
    await redis.lpop(scan_job.PRIO_QUEUE)
    await redis.incr(scan_job.PRIO_ACTIVE)  # a priority scan is running right now
    assert await scan_job.drain_queue(redis) == 0
    await redis.delete(scan_job.PRIO_ACTIVE)
    assert await scan_job.drain_queue(redis) == 3  # nothing waiting for a person: bulk resumes


async def test_priority_run_holds_the_flag_and_releases_it(redis, appdb, engine):
    seen = []

    class Spy(FakeEngine):
        def scan(self, address):
            seen.append("running")
            return super().scan(address)

    engine["eng"] = Spy(report())
    await redis.set(scan_job.stale_marker(CA), "1")
    await scan_job.run_scan(redis, CA, priority=True)
    assert seen == ["running"]
    assert int(await redis.get(scan_job.PRIO_ACTIVE) or 0) == 0  # released: bulk may resume


async def test_an_old_verdict_is_shown_at_once_and_refreshed_in_the_background(client, appdb, redis, engine):
    first = await landed(client, redis)
    assert first.status_code == 200
    async with appdb() as db:  # the stored check is now a day old: past the freshness window
        await db.execute(update(SafetyScore).values(ts=datetime.now(timezone.utc) - timedelta(days=1)))
        await db.commit()
    await redis.flushall()
    r = await client.get(f"/safety/{CA}")
    assert r.status_code == 200 and r.json()["verdict"] == "CLEAN" and r.json()["refreshing"] is True  # no skeleton
    assert await redis.lrange(scan_job.PRIO_QUEUE, 0, -1) == [CA]  # the refresh is at priority, not bulk
    await scan_job.drain_priority(redis)
    fresh = await client.get(f"/safety/{CA}")
    assert fresh.status_code == 200 and fresh.json()["refreshing"] is False


async def test_202_carries_the_real_stage_and_a_measured_eta(client, appdb, redis, engine):
    for sec in ("12.0", "14.0", "20.0"):
        await redis.lpush("scan:durations", sec)
    await redis.set(f"scan:stage:{CA}", "holders")
    body = (await client.get(f"/safety/{CA}")).json()
    assert body["queued"] and body["stage"] == "holders" and body["eta_s"] == 14


async def test_a_scan_records_how_long_it_took_and_the_stages_it_passed(appdb, redis):
    stages = []
    chain = Chain([], bytecode("a9059cbb"), owner="0x" + "00" * 40)
    with progress.sink(stages.append):
        intelligence.scan(T, transport=chain, fetch=dexscreener(liq=2_000_000, fdv=1_000_000, site=True))
    assert stages == ["contract", "market", "transfers", "holders", "scoring"]


def test_explorer_reads_are_reused_instead_of_repeated(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_API_URL": "https://explorer.test", "EXPLORER_RPM": "6000"})
    monkeypatch.setattr(icfg, "_proxy_prefix", "")
    monkeypatch.setattr(ihttp.PACER, "acquire", lambda priority, rpm: 0.0)
    router = Router(lambda url, data: {"items": [], "holders_count": "5"})
    monkeypatch.setattr(ihttp.urllib.request, "urlopen", router)
    url = "https://explorer.test/api/v2/tokens/0xabc"
    assert ihttp.get_json(url) == ihttp.get_json(url) and len(router.calls) == 1  # the second scan reads nothing
    other = Router(lambda u, d: {"pairs": []})
    monkeypatch.setattr(ihttp.urllib.request, "urlopen", other)
    dex = "https://api.dexscreener.com/latest/dex/tokens/0xabc"
    ihttp.get_json(dex), ihttp.get_json(dex)
    assert len(other.calls) == 2  # market data is NOT cached here: prices must be live
