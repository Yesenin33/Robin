import copy
import dataclasses

import pytest
from sqlalchemy import func, select

from app.models import HoldersSnapshot, SafetyScore, Token
from app.services.engine import ScanReport, map_report
from app.services.intelligence import SCHEMA_KEYS, assess
from app.services.intelligence.schema import empty_result
from app.services.intelligence.scoring import sell_impact_pct
from app.services.safety_service import SafetyService, ScanInProgress, evaluate

CA = "0x" + "ab" * 20


def report(**kw) -> ScanReport:
    """Normalized result -> assessed exactly like intelligence.scan() does."""
    r = empty_result(CA, "robinhood")
    r.update(symbol="HOOD-X", name="Hood Index", holders_count=18420, real_hands_pct=94, top_holder_pct=0.5,
             liquidity_usd=2_400_000, fdv=8_000_000, price_usd=1.25, honeypot=False, can_mint=False,
             owner_renounced=True, blacklist=False, proxy=False, buy_tax=0, sell_tax=0, has_site=True, has_twitter=True)
    r.update(kw)
    a = assess(r)
    r["safety_score"], r["verdict"], r["risks"] = a.score, a.verdict, a.risks
    return map_report(r)


class FakeEngine:
    name = "fake"

    def __init__(self, r: ScanReport):
        self.r, self.calls = r, 0

    def scan(self, address: str) -> ScanReport:
        self.calls += 1
        return copy.deepcopy(self.r)  # a real engine builds a fresh report per scan


def test_sell_impact_constant_product():
    assert sell_impact_pct(10, 1_000_000, 200_000) == 75.0  # dump == pool quote side -> price / 4
    assert sell_impact_pct(None, 1, 1) is None


def test_map_report_is_exact_schema():
    r = empty_result(CA, "robinhood")
    rep = map_report(r)
    assert tuple(k for k in dataclasses.asdict(rep) if k != "raw") == SCHEMA_KEYS
    del r["verdict"]
    with pytest.raises(KeyError, match="verdict"):
        map_report(r)


def test_clean_token_matches_design():
    out = evaluate(report())
    assert out.verdict == "CLEAN" and out.buy_recommended and out.risks == []
    assert [m.label for m in out.metrics] == ["Holders", "Real hands", "If top holder sells", "Liquidity"]
    assert [m.value for m in out.metrics] == ["18,420", "94%", "-6%", "$2.4M"]
    assert all(m.tone == "ok" for m in out.metrics)


def test_risky_token_summary_uses_first_risk():
    out = evaluate(report(top_holder_pct=25, fdv=2_000_000, liquidity_usd=180_000, real_hands_pct=61, holders_count=1208))
    assert out.verdict == "RISKY"
    assert out.summary == "Proceed with care. A few wallets hold a big share."
    assert "It could drop 98% if the top holder sells." in out.risks  
    assert {m.key: m.tone for m in out.metrics}["real_hands"] == "warn"


def test_scam_token_is_danger_and_not_buyable():
    out = evaluate(report(holders_count=94, real_hands_pct=18, top_holder_pct=70, liquidity_usd=6_000,
                          fdv=100_000, honeypot=True, sell_tax=40))
    assert out.verdict == "DANGER" and not out.buy_recommended and out.score < 40
    assert out.risks[:2] == ["You may not be able to sell this token.", "Selling costs a 40% fee."]
    assert out.summary.startswith("Very likely a scam.") and out.summary.endswith("Avoid.")
    assert {m.tone for m in out.metrics} == {"bad"}


@pytest.mark.parametrize("kw,verdict", [
    ({"honeypot": True}, "DANGER"),
    ({"sell_tax": 35}, "DANGER"),
    ({"blacklist": True, "owner_renounced": False}, "DANGER"),
    ({"blacklist": True, "owner_renounced": True}, "CLEAN"),  # renounced -> nobody can use it
    ({"can_mint": True, "owner_renounced": False, "proxy": True}, "RISKY"),
    ({"liquidity_usd": None}, "CLEAN"),
])
def test_verdict_rules(kw, verdict):
    assert evaluate(report(**kw)).verdict == verdict


def test_unknown_values_render_as_dash():
    out = evaluate(report(holders_count=None, real_hands_pct=None, top_holder_pct=None, liquidity_usd=None))
    assert [m.value for m in out.metrics] == ["—", "—", "—", "—"]
    assert len(out.metrics) == 4


async def test_get_scans_persists_and_caches(db, redis):
    engine = FakeEngine(report())
    svc = SafetyService(db, redis, engine)
    first = await svc.get(CA)
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
