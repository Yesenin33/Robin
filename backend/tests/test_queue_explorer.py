"""Scan queues (priority / bounded / concurrent), refresh invalidation, explorer pacing + stale holders,
wallet tiers and the fresh-account copy feed."""
import io
import time
import urllib.error

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.jobs import copy_job, scan_job
from app.models import CopyEvent, HoldersSnapshot, SmartWallet, Token
from app.services import copy_service, smart_wallets
from app.services.chain_index import Trade
from app.services.intelligence import http as ihttp
from app.services.intelligence import config as icfg
from app.services.safety_service import SafetyService
from tests.test_safety_service import FakeEngine, report

CA = "0x" + "ab" * 20
OTHERS = ["0x" + f"{i:02x}" * 20 for i in range(1, 8)]


@pytest.fixture
def engine(monkeypatch):
    holder = {"eng": FakeEngine(report())}
    monkeypatch.setattr(scan_job, "get_engine", lambda: holder["eng"])
    monkeypatch.setattr("app.api.deps.get_engine", lambda: holder["eng"])
    return holder


# ---------- TASK 3: queues ----------

async def test_priority_scan_does_not_wait_behind_bulk_and_clears_marker(appdb, redis, engine):
    for a in OTHERS:
        await scan_job.enqueue_scan(redis, a)
    await scan_job.request_refresh(redis, CA)
    assert await redis.lrange(scan_job.PRIO_QUEUE, 0, -1) == [CA]
    assert await redis.llen(scan_job.QUEUE) == len(OTHERS)
    assert await scan_job.drain_priority(redis) == 1  # lands without touching the 7 bulk items
    assert engine["eng"].calls == 1 and await redis.llen(scan_job.QUEUE) == len(OTHERS)
    assert not await redis.exists(scan_job.stale_marker(CA)) and not await redis.sismember(scan_job.PRIO_SET, CA)


async def test_refresh_pulls_the_token_out_of_the_bulk_queue(redis):
    await scan_job.enqueue_scan(redis, CA)
    await scan_job.request_refresh(redis, CA)
    assert await redis.lrange(scan_job.QUEUE, 0, -1) == [] and await redis.llen(scan_job.PRIO_QUEUE) == 1
    assert await scan_job.enqueue_scan(redis, CA) is False  # bulk enqueue is a no-op while a priority scan is pending


async def test_bulk_queue_is_capped_and_refresh_stale_only_fills_the_room(appdb, redis, monkeypatch):
    monkeypatch.setattr(get_settings(), "scan_queue_max", 3)
    results = [await scan_job.enqueue_scan(redis, a) for a in OTHERS]
    assert results == [True, True, True] + [False] * 4 and await redis.llen(scan_job.QUEUE) == 3
    assert await scan_job.refresh_stale(redis) == 0  # full: nothing added
    await redis.delete(scan_job.QUEUE, scan_job.QUEUED_SET)
    async with appdb() as db:
        db.add_all([Token(address=a) for a in OTHERS])
        await db.commit()
    assert await scan_job.refresh_stale(redis) == 3  # room for 3 only, however many are stale
    assert await redis.llen(scan_job.QUEUE) == 3


async def test_drain_runs_scans_concurrently_and_bounded(appdb, redis, monkeypatch):
    class Slow(FakeEngine):
        def scan(self, address):
            time.sleep(0.25)
            return super().scan(address)

    monkeypatch.setattr(scan_job, "get_engine", lambda: Slow(report()))
    monkeypatch.setattr(get_settings(), "scan_concurrency", 4)
    for a in OTHERS[:4]:
        await scan_job.enqueue_scan(redis, a)
    t0 = time.perf_counter()
    assert await scan_job.drain_queue(redis, max_items=4) == 4
    assert time.perf_counter() - t0 < 0.7  # sequential would take >= 1.0 s
    monkeypatch.setattr(get_settings(), "scan_bulk_per_tick", 2)
    for a in OTHERS:
        await scan_job.enqueue_scan(redis, a)
    assert await scan_job.drain_queue(redis) == 2  # bounded per tick


async def test_refresh_invalidates_cache_and_db_shortcut(client, appdb, redis, engine, signed_in):
    first = (await client.get(f"/safety/{CA}")).json()
    assert first["verdict"] == "CLEAN"
    engine["eng"] = FakeEngine(report(honeypot=True))  # the world changed
    assert (await client.get(f"/safety/{CA}")).json()["verdict"] == "CLEAN"  # cache/DB shortcut still answer
    assert (await client.post(f"/safety/{CA}/refresh")).status_code == 202
    assert not await redis.exists(f"safety:{CA}")
    assert (await client.get(f"/safety/{CA}")).json()["verdict"] == "DANGER"  # new scan, not the previous row


async def test_failed_refresh_serves_previous_result_marked_stale(client, appdb, redis, engine):
    from app.services.intelligence import ScanUnavailable

    await client.get(f"/safety/{CA}")

    class Down(FakeEngine):
        def scan(self, address):
            raise ScanUnavailable("rpc down")

    engine["eng"] = Down(report())
    await client.post(f"/safety/{CA}/refresh")
    body = (await client.get(f"/safety/{CA}")).json()
    assert body["verdict"] == "CLEAN" and body["stale"] is True


# ---------- TASK 4: explorer ----------

def test_pacer_spaces_bulk_lets_priority_through_and_penalty_delays_all():
    now, slept = [100.0], []
    p = ihttp.Pacer(clock=lambda: now[0], sleep=slept.append, jitter=lambda: 1.0)
    assert [p.acquire(False, 20) for _ in range(3)] == [0.0, 3.0, 6.0]  # 20 rpm -> 3 s apart
    assert p.acquire(True, 20) == 0.0  # user-requested read doesn't queue behind them
    assert p.acquire(False, 20) > 6.0  # ...but it consumed one bulk slot
    p.penalize(30)
    assert p.acquire(True, 20) >= 30.0
    assert slept[0] == 3.0


def test_backoff_honours_retry_after_and_is_jittered_and_capped():
    assert ihttp.backoff_seconds(0, None, rand=lambda: 0.5) == 2.0
    assert ihttp.backoff_seconds(1, None, rand=lambda: 0.5) == 4.0
    assert ihttp.backoff_seconds(0, "20", rand=lambda: 0.5) == 20.0  # Retry-After is a floor
    assert ihttp.backoff_seconds(9, "999", rand=lambda: 1.0) == pytest.approx(30 * 1.25)
    lo, hi = ihttp.backoff_seconds(2, None, rand=lambda: 0.0), ihttp.backoff_seconds(2, None, rand=lambda: 1.0)
    assert (lo, hi) == (6.0, 10.0)


def test_explorer_429_backs_off_for_everyone_then_succeeds(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_API_URL": "https://explorer.test", "EXPLORER_RPM": "6000"})
    monkeypatch.setattr(icfg, "_proxy_prefix", "")
    slept, penalties = [], []
    monkeypatch.setattr(ihttp.time, "sleep", slept.append)
    monkeypatch.setattr(ihttp.PACER, "penalize", penalties.append)
    monkeypatch.setattr(ihttp.PACER, "acquire", lambda priority, rpm: 0.0)
    calls = {"n": 0}

    def urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, io.BytesIO(b""))
        return io.BytesIO(b'{"holders_count": "108"}')

    monkeypatch.setattr(ihttp.urllib.request, "urlopen", urlopen)
    assert ihttp.get_json("https://explorer.test/api/v2/tokens/0x1/holders") == {"holders_count": "108"}
    assert calls["n"] == 3 and len(penalties) == 2 and all(7 * 0.75 <= p <= 14 * 1.25 for p in penalties)


def test_non_explorer_urls_are_not_paced_and_404_is_not_retried(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_API_URL": "https://explorer.test"})
    monkeypatch.setattr(ihttp.PACER, "acquire", lambda *a: pytest.fail("market call was paced"))
    n = {"c": 0}

    def urlopen(req, timeout):
        n["c"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b""))

    monkeypatch.setattr(ihttp.urllib.request, "urlopen", urlopen)
    with pytest.raises(ihttp.HttpError):
        ihttp.get_json("https://api.dexscreener.com/x")
    assert n["c"] == 1


def test_explorer_rpm_is_auto_and_key_aware(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {})
    monkeypatch.delenv("EXPLORER_PROXY_KEY", raising=False)
    monkeypatch.delenv("EXPLORER_RPM", raising=False)
    assert icfg.explorer_rpm() == 20
    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_PROXY_KEY": "k"})
    assert icfg.explorer_rpm() == 200
    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_PROXY_KEY": "k", "EXPLORER_RPM": "50"})
    assert icfg.explorer_rpm() == 50


async def test_throttled_holders_fall_back_to_last_good_snapshot(appdb, redis):
    async with appdb() as db:
        good = await SafetyService(db, redis, FakeEngine(report())).scan(CA)
    assert good.holders_stale_at is None and good.metrics[0].value == "18,420"
    # explorer 429: holders/real hands/top holder all empty, as the live scan recorded
    throttled = report(holders_count=None, real_hands_pct=None, top_holder_pct=None,
                       risks=[], errors=["holders_explorer: GET .../holders: HTTP Error 429: Too Many Requests"])
    async with appdb() as db:
        out = await SafetyService(db, redis, FakeEngine(throttled)).scan(CA)
        assert out.holders_stale_at is not None
        assert [m.value for m in out.metrics[:2]] == ["18,420", "94%"]  # no dashes
        assert out.verdict == "CLEAN"
        assert await db.scalar(select(func.count(HoldersSnapshot.id))) == 1  # substituted data isn't stored as new
    async with appdb() as db:  # and it survives a cache miss (read back from the DB row)
        await redis.flushall()
        again = await SafetyService(db, redis, FakeEngine(throttled)).get(CA)
        assert again.holders_stale_at is not None


async def test_no_snapshot_yet_means_honest_dashes(appdb, redis):
    throttled = report(holders_count=None, real_hands_pct=None, top_holder_pct=None)
    async with appdb() as db:
        out = await SafetyService(db, redis, FakeEngine(throttled)).scan(CA)
    assert out.holders_stale_at is None and out.metrics[0].value == "—"


# ---------- TASK 5: formatting ----------

def test_usd_with_cents_everywhere():
    s = smart_wallets.WalletStats("0x1", realised_pnl_usd=0.15, wins=1, tokens=["t"])
    assert smart_wallets.describe(s).startswith("Made +$0.15 in realised profit;")
    s.realised_pnl_usd = 12345.6
    assert smart_wallets.describe(s).startswith("Made +$12,345.60 in realised profit;")
    reason, _ = copy_service.describe("buy", 1000, "HOODX", 0.5, None, "CLEAN", [])
    assert reason.startswith("Bought 1.0K HOODX for $0.50.")


# ---------- TASK 6: tiers + fresh-account feed ----------

def trade(wallet, side, raw, usd, token="0xt1", block=1):
    return Trade(wallet=wallet, token=token, side=side, token_raw=raw, tx=f"0x{wallet[2:6]}{side}{block}{token}",
                 block=block, log_index=0, usd=usd)


def test_rank_tiers_tops_up_with_single_trade_winners():
    t = [trade("0xaa" + "0" * 38, "buy", 10, 100, block=1), trade("0xaa" + "0" * 38, "sell", 10, 300, block=2),
         trade("0xaa" + "0" * 38, "buy", 10, 100, "0xt2", 3), trade("0xaa" + "0" * 38, "sell", 10, 150, "0xt2", 4)]
    for i in range(1, 4):
        w = "0xb" + str(i) + "0" * 38
        t += [trade(w, "buy", 10, 100, block=5), trade(w, "sell", 10, 100 + 10 * i, block=6)]
    strict = smart_wallets.rank(t, None, min_closed=2)
    assert [w.address[:4] for w in strict] == ["0xaa"]  # the old behaviour: 1 wallet
    tiers = smart_wallets.rank_tiers(t, None, strong_min=2, fill_to=3)
    assert [w.address[:4] for w in tiers] == ["0xaa", "0xb3", "0xb2"]  # strong first, then best singles, capped
    assert smart_wallets.describe(tiers[1]).startswith("Made +$30.00 in realised profit; 1 of 1 closed trades were wins")


async def seed_event(maker, wallet, minutes_ago=0, tx="0x1"):
    from datetime import datetime, timedelta, timezone

    async with maker() as db:
        db.add(CopyEvent(address=wallet, tx_hash=tx, side="buy", token_address="0x" + "70" * 20, token_amount=1,
                         amount_usd=12.5, ts=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
                         meta={"symbol": "HOODX"}))
        await db.commit()


async def test_fresh_account_feed_falls_back_to_top_suggested_wallets(client, signed_in, appdb, redis):
    headers, _, _ = signed_in
    top, other, followed = "0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20
    async with appdb() as db:
        db.add(SmartWallet(address=top, score=10, realised_pnl_usd=500, invested_usd=100, trades=2, wins=2, losses=0,
                           tokens=1, early_buys=0))
        await db.commit()
    await seed_event(appdb, top, 5, "0x1")
    await seed_event(appdb, other, 1, "0x2")  # somebody else's followed wallet: not a fresh user's business
    feed = (await client.get("/copy/feed", headers=headers)).json()
    assert [f["address"] for f in feed] == [top] and feed[0]["following"] is False
    assert (await client.get("/copy/feed?only_followed=true", headers=headers)).json() == []  # explicit opt-out
    await client.post("/copy/subscribe", json={"address": followed, "follow": True}, headers=headers)
    await seed_event(appdb, followed, 0, "0x3")
    after = (await client.get("/copy/feed", headers=headers)).json()
    assert {f["address"] for f in after} == {top, other, followed}  # follows someone: the regular feed


async def test_empty_fallback_queues_history_of_top_wallets(client, signed_in, appdb, redis):
    headers, _, _ = signed_in
    async with appdb() as db:
        db.add(SmartWallet(address="0x" + "aa" * 20, score=1, realised_pnl_usd=1, invested_usd=1, trades=1, wins=1,
                           losses=0, tokens=1, early_buys=0))
        await db.commit()
    assert (await client.get("/copy/feed", headers=headers)).json() == []
    assert await redis.lrange(copy_job.BACKFILL_QUEUE, 0, -1) == ["0x" + "aa" * 20]


async def test_collector_watches_top_suggested_wallets_without_any_follower(appdb, redis, monkeypatch):
    seen = {}

    class R:
        def block_number(self):
            return 100

    def wallet_trades(rpc, wallets, start, end, chunk):
        seen["wallets"] = wallets
        return []

    monkeypatch.setattr(copy_job.chain_index, "wallet_trades", wallet_trades)
    monkeypatch.setattr(copy_job.chain_index, "enrich", lambda *a, **k: None)
    async with appdb() as db:
        db.add(SmartWallet(address="0x" + "aa" * 20, score=1, realised_pnl_usd=1, invested_usd=1, trades=1, wins=1,
                           losses=0, tokens=1, early_buys=0))
        await db.commit()

    async def noop(*a, **k):
        return trades_in

    trades_in = []
    monkeypatch.setattr(copy_job.pricing, "enrich_and_value", noop)
    await copy_job.collect(redis, rpc=R())
    assert seen["wallets"] == ["0x" + "aa" * 20]
