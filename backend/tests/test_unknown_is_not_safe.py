"""Audit of the "unknown == safe" class: wherever a value is absent the tone is neutral, the hint says we couldn't
check it, and no reassuring text or CLEAN verdict is derived from it."""
import itertools

import pytest

from app.schemas.safety import Metric
from app.services import intelligence
from app.services.intelligence.scoring import assess, evidence_gaps, sell_impact_pct
from app.services.safety_service import evaluate
from tests.test_intelligence import FULL, SCAM, Chain, T, _dead, bytecode
from tests.test_safety_service import report

REASSURING = ("Easy to sell", "People who own it", "Not bots or linked wallets", "Price could drop", "Very few owners")
FIELDS = ["holders_count", "real_hands_pct", "top_holder_pct", "fdv", "liquidity_usd"]


@pytest.mark.parametrize("unknown", [c for n in range(len(FIELDS) + 1) for c in itertools.combinations(FIELDS, n)])
def test_every_combination_of_missing_inputs_yields_neutral_metrics(unknown):
    out = evaluate(report(**{f: None for f in unknown}))
    for m in out.metrics:
        if m.value == "—":
            assert (m.tone, m.hint) == ("unknown", "We couldn't check this"), (unknown, m)
        else:
            assert m.tone != "unknown", (unknown, m)
        if m.tone == "unknown":
            assert not any(phrase in m.hint for phrase in REASSURING)
    by = {m.key: m for m in out.metrics}
    if "liquidity_usd" in unknown:  # the reported case
        assert by["liquidity"].value == "—" and by["liquidity"].tone == "unknown"
        assert "Easy to sell" not in by["liquidity"].hint
    # a verdict is never CLEAN while any of the evidence it rests on is missing
    if {"holders_count", "real_hands_pct", "top_holder_pct", "liquidity_usd"} & set(unknown):
        assert out.verdict != "CLEAN" and out.score != 100, unknown


def test_the_metric_model_itself_enforces_it_for_old_rows_and_caches():
    """Rows stored (and cached) before this fix carry tone "ok" next to a "—" value."""
    old = Metric(key="liquidity", label="Liquidity", emoji="💧", value="—", tone="ok", hint="Easy to sell without a big loss")
    assert (old.tone, old.hint) == ("unknown", "We couldn't check this")
    known = Metric(key="liquidity", label="Liquidity", emoji="💧", value="$2.4M", tone="ok", hint="Easy to sell without a big loss")
    assert (known.tone, known.hint) == ("ok", "Easy to sell without a big loss")


def test_clean_summary_does_not_claim_a_price_impact_that_wasnt_computed():
    full = evaluate(report())
    assert full.verdict == "CLEAN" and "no one holder can move the price much" in full.summary
    no_fdv = evaluate(report(fdv=None))
    assert no_fdv.verdict == "CLEAN"  # nothing wrong was found
    assert "no one holder" not in no_fdv.summary and "couldn't work out" in no_fdv.summary
    assert [m for m in no_fdv.metrics if m.key == "top_holder_sell"][0].tone == "unknown"


def test_known_empty_pool_is_a_total_loss_not_an_unknown():
    assert sell_impact_pct(10, 1_000_000, 0) == 100.0
    assert sell_impact_pct(10, 1_000_000, None) is None and sell_impact_pct(10, None, 5_000) is None
    assert sell_impact_pct(None, 1_000_000, 5_000) is None


# ---------- scoring ----------

def test_holder_count_without_a_breakdown_is_not_enough():
    for missing in ("top_holder_pct", "real_hands_pct"):
        a = assess({**FULL, missing: None})
        assert a.verdict == "UNKNOWN" and a.score is None
        assert a.risks[0] == "We couldn't check this token: holder breakdown was unavailable."
    assert evidence_gaps({**FULL, "holders_count": None, "top_holder_pct": None}) == ["holder data"]


def test_no_observed_sell_means_never_clean():
    """honeypot None = nobody was seen selling: absence of evidence of a honeypot is not evidence of none."""
    a = assess({**FULL, "honeypot": None})
    assert a.verdict == "RISKY" and a.score == 69 and not a.critical
    assert "We haven't seen anyone sell it recently, so we can't confirm you could sell it." in a.risks
    assert assess({**FULL, "honeypot": False}).verdict == "CLEAN"  # a sell WAS observed
    assert assess({**FULL, "honeypot": True}).verdict == "DANGER"
    assert assess({**FULL, "honeypot": None, "holders_count": None}).verdict == "UNKNOWN"  # gaps still win


def test_a_failed_or_empty_transfer_read_does_not_fabricate_real_hands():
    """Linking wallets needs transfers. With none, "100% real hands" would only mean we never looked."""
    chain = Chain([], bytecode("a9059cbb"))
    intelligence.configure(explorer_api_url="https://explorer.test", explorer_proxy_prefix="")
    import app.services.intelligence.holders as holders_mod

    original = holders_mod.get_json
    items = [{"address": {"hash": "0x" + f"{i:02x}" * 20, "is_contract": False}, "value": str(10 * 10**18)} for i in range(1, 6)]
    holders_mod.get_json = lambda url, **k: {"holders_count": "5"} if url.endswith(T) else {"items": items}
    try:
        r = intelligence.scan(T, transport=chain, fetch=_dead)
    finally:
        holders_mod.get_json = original
    assert r["holders_count"] == 5 and r["top_holder_pct"] is not None
    assert r["real_hands_pct"] is None  # not 100
    assert r["verdict"] == "UNKNOWN" and r["safety_score"] is None


def test_real_hands_is_still_reported_when_transfers_exist():
    chain = Chain(SCAM, bytecode("a9059cbb"))
    r = intelligence.scan(T, transport=chain, fetch=_dead)
    assert r["real_hands_pct"] is not None


# ---------- fail closed: the browser being down must never turn into "no findings" ----------

def test_browser_sidecar_down_reports_the_source_failed_and_never_clean(monkeypatch):
    import urllib.error

    from app.services.intelligence import config as icfg
    from app.services.intelligence import http as ihttp

    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_API_URL": "https://explorer.test",
                                             "EXPLORER_BROWSER_URL": "http://browser:8765", "EXPLORER_RPM": "600000"})
    monkeypatch.setattr(ihttp.PACER, "acquire", lambda *a, **k: 0.0)
    monkeypatch.setattr(ihttp.time, "sleep", lambda s: None)

    def refused(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ihttp.urllib.request, "urlopen", refused)
    r = intelligence.scan(T, transport=Chain([], bytecode("a9059cbb")), fetch=_dead)
    assert any(e.startswith("holders_explorer:") and "browser" in e for e in r["errors"])  # the source is reported as failed
    assert r["holders_count"] is None
    assert r["verdict"] == "UNKNOWN" and r["safety_score"] is None and r["verdict"] != "CLEAN"
    assert r["risks"][0].startswith("We couldn't check this token:")
