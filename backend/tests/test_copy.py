"""Copy feed end to end on a mocked V4 chain: discovery -> follow -> backfill -> live collect -> feed."""
import pytest
from sqlalchemy import select

from app.jobs import copy_job, discovery_job, rank_job
from app.models import Alert, CopyEvent, SafetyScore, Token
from app.services.chain_index import POOL_MANAGER
from app.services.intelligence.rpc import Rpc
from tests.chainmock import E18, V4Chain

TOK = "0x" + "70" * 20
POOL = "0x" + "ab" * 32
WHALE = "0x" + "a1" * 20


@pytest.fixture
def chain(monkeypatch):
    c = V4Chain(head=5000)
    c.token(TOK, "HOODX")
    c.pool(4000, TOK, POOL)
    rpc = Rpc("mock", c)
    for mod in (copy_job, discovery_job, rank_job):
        monkeypatch.setattr(mod, "make_rpc", lambda: rpc)
    return c


async def follow(client, headers, wallet=WHALE):
    r = await client.post("/copy/subscribe", json={"address": wallet, "follow": True}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_discovery_indexes_new_pools_and_queues_scans(appdb, redis, chain):
    out = await discovery_job.discover_pools(redis)
    assert out["pools"] == 1 and out["new_tokens"] == 1
    async with appdb() as db:
        t = (await db.execute(select(Token))).scalar_one()
    assert (t.address, t.symbol, t.decimals, t.pair_address, t.pool_init_block) == (TOK, "HOODX", 18, POOL, 4000)
    assert await redis.lrange("scan:queue", 0, -1) == [TOK]
    assert int(await redis.get(discovery_job.CURSOR)) == 5000
    again = await discovery_job.discover_pools(redis)  # incremental: nothing new past the cursor
    assert again["pools"] == 0


async def test_follow_backfills_from_explorer_then_collects_live(client, signed_in, appdb, redis, chain, monkeypatch):
    headers, _, _ = signed_in
    old_buy = chain.swap(1200, WHALE, TOK, "buy", 2000 * E18, 0.8, POOL)

    def explorer(url):  # Blockscout token-transfers page for the followed wallet
        assert f"/addresses/{WHALE}/token-transfers" in url
        return {"items": [{
            "from": {"hash": POOL_MANAGER}, "to": {"hash": WHALE},
            "token": {"address_hash": TOK, "symbol": "HOODX", "decimals": "18"},
            "total": {"value": str(2000 * E18), "decimals": "18"},
            "transaction_hash": old_buy, "block_number": 1200, "log_index": 1, "timestamp": "2026-10-01T10:00:00Z",
        }], "next_page_params": None}

    monkeypatch.setattr(copy_job, "get_json", explorer)
    r = await follow(client, headers)
    assert r == {"address": WHALE, "active": True, "backfill_queued": True}
    assert await copy_job.drain_backfill(redis) == 1

    # live: cursor starts copy_lookback_blocks back; a fresh sell lands in the window
    await redis.set(copy_job.CURSOR, 4500)
    chain.swap(4900, WHALE, TOK, "sell", 500 * E18, 0.4, POOL)
    assert await copy_job.collect(redis) == 1
    assert await copy_job.collect(redis) == 0  # cursor advanced, dedup holds

    async with appdb() as db:
        db.add(SafetyScore(token_id=(await db.execute(select(Token.id))).scalar_one(), score=55, verdict="RISKY",
                           risks=["A few wallets hold a big share."], metrics={}))
        await db.commit()
        alerts = (await db.execute(select(Alert))).scalars().all()
    # history (backfill) is silent; the live trade alerts the follower
    assert len(alerts) == 1 and alerts[0].kind == "copy_event" and "sold HOODX ($1,200)" in alerts[0].title

    feed = (await client.get("/copy/feed?only_followed=true", headers=headers)).json()
    assert [(f["side"], f["amount_usd"], f["risk_tag"], f["following"]) for f in feed] == [
        ("sell", 1200.0, "medium", True), ("buy", 2400.0, "medium", True)]
    assert feed[0]["reason"] == ("Sold 500 HOODX for $1,200. Our safety check says proceed with care: "
                                 "a few wallets hold a big share.")
    assert feed[1]["ts"].startswith("2026-10-01T10:00:00")


async def test_backfill_falls_back_to_rpc_when_explorer_is_down(appdb, redis, chain, monkeypatch):
    from app.services.intelligence.http import HttpError

    def down(url):
        raise HttpError("cloudflare challenge")

    monkeypatch.setattr(copy_job, "get_json", down)
    chain.swap(3000, WHALE, TOK, "buy", 10 * E18, 0.01, POOL)
    assert await copy_job.backfill(redis, WHALE) == 1
    async with appdb() as db:
        ev = (await db.execute(select(CopyEvent))).scalar_one()
    assert (ev.side, float(ev.eth_amount), float(ev.amount_usd), ev.block_number) == ("buy", 0.01, 30.0, 3000)


async def test_unknown_token_is_created_and_reason_says_unchecked(client, signed_in, appdb, redis, chain):
    headers, _, _ = signed_in
    await follow(client, headers)
    await redis.set(copy_job.CURSOR, 4500)
    new = chain.token("0x" + "77" * 20, "NEWT", decimals=9)
    usdg = chain.token("0x5fc5360d0400a0fd4f2af552add042d716f1d168", "USDG", decimals=6)
    chain.pool(4100, new, "0x" + "ee" * 32, quote=usdg)  # pool never seen by discovery: resolved on the fly
    chain.swap(4600, WHALE, new, "buy", 5 * 10**9, 60.0, "0x" + "ee" * 32)  # paid 60 USDG
    assert await copy_job.collect(redis) == 1
    async with appdb() as db:
        ev = (await db.execute(select(CopyEvent))).scalar_one()
    assert (ev.quote_token, float(ev.amount_usd), ev.eth_amount) == (usdg, 60.0, None)
    assert new in await redis.lrange("scan:queue", 0, -1)
    item = (await client.get("/copy/feed", headers=headers)).json()[0]
    assert item["token_symbol"] == "NEWT" and item["risk_tag"] == "medium"
    assert item["reason"] == "Bought 5 NEWT for $60. We haven't safety-checked this token yet."


async def test_suggested_wallets_come_from_ranked_trades(client, signed_in, appdb, redis, chain):
    headers, _, _ = signed_in
    await discovery_job.discover_pools(redis)
    chain.swap(4010, WHALE, TOK, "buy", 1000 * E18, 1.0, POOL)
    chain.swap(4200, WHALE, TOK, "sell", 600 * E18, 2.0, POOL)
    chain.swap(4300, WHALE, TOK, "sell", 400 * E18, 1.5, POOL)
    from app.core.config import get_settings
    get_settings().suggested_min_closed = 1
    try:
        assert await rank_job.rank_wallets(redis) == 1
    finally:
        get_settings().suggested_min_closed = 2
    s = (await client.get("/copy/suggested", headers=headers)).json()
    assert len(s) == 1 and s[0]["address"] == WHALE and s[0]["realised_pnl_usd"] == pytest.approx(7500)  # ETH @ $3,000
    assert s[0]["early_buys"] == 1 and s[0]["following"] is False and s[0]["risk_tag"] == "medium"
    assert s[0]["reason"].startswith("Made +$7,500 in realised profit; 1 of 1 closed trades were wins")


async def test_no_followed_wallets_means_no_rpc_work(appdb, redis, chain):
    assert await copy_job.collect(redis) == 0
    assert chain.calls == []


async def test_subscribe_validates_address(client, signed_in):
    headers, _, _ = signed_in
    r = await client.post("/copy/subscribe", json={"address": "0x123", "follow": True}, headers=headers)
    assert r.status_code == 422
