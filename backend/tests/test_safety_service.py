import pytest
from sqlalchemy import func, select

from app.models import HoldersSnapshot, SafetyScore, Token
from app.services.engine import ScanReport
from app.services.engine.crawlscan import map_report
from app.services.safety_service import (
    SafetyService, ScanInProgress, evaluate, sell_impact_pct, verdict_for,
)

CA = "0x" + "ab" * 20


def report(**kw) -> ScanReport:
    base = dict(address=CA, symbol="HOOD-X", name="Hood Index", score=88, holders_count=18420,
                real_hands_pct=94, top_holder_pct=0.5, liquidity_usd=2_400_000, fdv_usd=8_000_000,
                price_usd=1.25, honeypot=False, can_mint=False, owner_renounced=True,
                has_site=True, has_twitter=True)
    base.update(kw)
    return ScanReport(**base)


class FakeEngine:
    name = "fake"

    def __init__(self, r: ScanReport):
        self.r, self.calls = r, 0

    def scan(self, address: str) -> ScanReport:
        self.calls += 1
        return self.r


def test_sell_impact_constant_product():
    # dumping value equal to the pool's quote side -> price falls to 1/4
    assert sell_impact_pct(10, 1_000_000, 200_000) == 75.0
    assert sell_impact_pct(None, 1, 1) is None


@pytest.mark.parametrize("score,critical,expected", [(90, False, "CLEAN"), (55, False, "RISKY"),
                                                     (90, True, "DANGER"), (20, False, "DANGER")])
def test_verdict_thresholds(score, critical, expected):
    assert verdict_for(score, critical) == expected


def test_clean_token_matches_design():
    out = evaluate(report())
    assert out.verdict == "CLEAN" and out.buy_recommended
    labels = [m.label for m in out.metrics]
    assert labels == ["Holders", "Real hands", "If top holder sells", "Liquidity"]
    assert out.metrics[0].value == "18,420" and out.metrics[3].value == "$2.4M"
    assert all(m.tone == "ok" for m in out.metrics)
    assert out.metrics[2].value == "-6%"


def test_scam_token_is_danger_and_not_buyable():
    out = evaluate(report(score=15, holders_count=94, real_hands_pct=18, top_holder_pct=70,
                          liquidity_usd=6_000, fdv_usd=100_000, honeypot=True))
    assert out.verdict == "DANGER" and not out.buy_recommended
    assert "You may not be able to sell this token." in out.risks
    assert out.summary.startswith("Very likely a scam.")
    assert {m.tone for m in out.metrics} == {"bad"}


def test_honeypot_overrides_high_score():
    assert evaluate(report(score=95, honeypot=True)).verdict == "DANGER"


def test_fallback_score_when_engine_has_none():
    out = evaluate(report(score=None))
    assert 0 <= out.score <= 100


def test_crawlscan_mapping_nested_and_fractions():
    r = map_report(CA, {
        "token": {"symbol": "PLUM", "decimals": 9}, "safety_score": "52",
        "holders": {"count": 1208, "real_hands": 0.61, "operators": [{"pct": 0.3}, {"pct": 0.12}]},
        "market": {"priceUsd": "0.5", "liquidity": {"usd": 180000}, "fdv": 2e6},
        "security": {"honeypot": False, "sell_tax": 0.05}, "swarm": {"verdict": "CAUTION"},
    })
    assert (r.symbol, r.decimals, r.score, r.holders_count) == ("PLUM", 9, 52, 1208)
    assert r.real_hands_pct == pytest.approx(61) and r.top_holder_pct == pytest.approx(30)
    assert r.liquidity_usd == 180000 and r.sell_tax == pytest.approx(5) and r.swarm_verdict == "CAUTION"


async def test_get_scans_persists_and_caches(db, redis):
    engine = FakeEngine(report())
    svc = SafetyService(db, redis, engine)
    first = await svc.get(CA.upper().replace("0X", "0x"))
    second = await svc.get(CA)
    assert engine.calls == 1 and first.verdict == second.verdict == "CLEAN"
    assert await db.scalar(select(func.count(Token.id))) == 1
    assert await db.scalar(select(func.count(SafetyScore.id))) == 1
    assert await db.scalar(select(func.count(HoldersSnapshot.id))) == 1


async def test_db_used_when_cache_evicted(db, redis):
    engine = FakeEngine(report())
    svc = SafetyService(db, redis, engine)
    await svc.get(CA)
    await redis.flushall()
    out = await svc.get(CA)
    assert engine.calls == 1 and out.metrics[0].label == "Holders"


async def test_concurrent_scan_is_locked(db, redis):
    await redis.set(f"lock:scan:{CA}", "1")
    with pytest.raises(ScanInProgress):
        await SafetyService(db, redis, FakeEngine(report())).get(CA)
