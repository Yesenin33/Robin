"""Which URLs go through which route: market data direct, explorer via the Chromium sidecar, the paid proxy only
as an optional fallback - and a full scan that never mentions a proxy."""
import io
import json
import urllib.error

import pytest

from app.services import intelligence
from app.services.intelligence import config as icfg
from app.services.intelligence import http as ihttp
from tests.test_intelligence import SCAM, Chain, E18, P, T, W, bytecode  # noqa: F401

EXPLORER = "https://explorer.test"
BROWSER = "http://browser:8765"
PROXY = "https://proxy.test"
DEX = "https://api.dexscreener.com/latest/dex/tokens/0xabc"
GECKO = "https://api.geckoterminal.com/api/v2/networks/robinhood/tokens/0xabc/pools?page=1"
EXPLORER_URL = f"{EXPLORER}/api/v2/tokens/0xabc/holders"


class Router:
    """Fake urllib.request.urlopen: records every request, answers from a function (url, data) -> body | Exception."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, req, timeout=None):
        data = req.data.decode() if req.data else None
        self.calls.append({"url": req.full_url, "data": data, "auth": req.get_header("Authorization"),
                           "respond": req.get_header("X-respond-with")})
        out = self.answer(req.full_url, data)
        if isinstance(out, Exception):
            raise out
        return io.BytesIO(json.dumps(out).encode())

    def urls(self):
        return [c["url"] for c in self.calls]


@pytest.fixture
def cfg(monkeypatch):
    def apply(**values):
        monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_API_URL": EXPLORER, "EXPLORER_RPM": "600000", **{
            k.upper(): v for k, v in values.items()}})
        monkeypatch.setattr(icfg, "_proxy_prefix", values.get("explorer_proxy_prefix", ""))
        monkeypatch.setattr(ihttp.PACER, "acquire", lambda *a, **k: 0.0)
        monkeypatch.setattr(ihttp.time, "sleep", lambda s: None)
    return apply


def install(monkeypatch, answer):
    r = Router(answer)
    monkeypatch.setattr(ihttp.urllib.request, "urlopen", r)
    return r


# ---------- 2. only the explorer is proxied ----------

def test_market_urls_are_never_sent_through_the_proxy(cfg, monkeypatch):
    cfg(explorer_proxy_prefix=PROXY, explorer_proxy_key="k")
    r = install(monkeypatch, lambda url, data: {"pairs": []})
    ihttp.get_json(DEX)
    ihttp.get_json(GECKO)
    assert r.urls() == [DEX, GECKO]  # direct, byte for byte
    assert all(c["auth"] is None and c["respond"] is None for c in r.calls)  # no proxy key, no proxy headers


def test_explorer_url_goes_through_the_proxy_when_that_is_all_there_is(cfg, monkeypatch):
    cfg(explorer_proxy_prefix=PROXY, explorer_proxy_key="k")
    r = install(monkeypatch, lambda url, data: {"data": {"content": json.dumps({"items": []})}})
    assert ihttp.get_json(EXPLORER_URL) == {"items": []}
    assert r.urls() == [f"{PROXY}/{EXPLORER_URL}"] and r.calls[0]["auth"] == "Bearer k"


def test_explorer_url_is_direct_when_nothing_is_configured(cfg, monkeypatch):
    cfg()
    r = install(monkeypatch, lambda url, data: {"items": []})
    assert ihttp.get_json(EXPLORER_URL) == {"items": []} and r.urls() == [EXPLORER_URL]


# ---------- 3. the Chromium sidecar ----------

def test_explorer_goes_through_the_browser_sidecar_not_the_proxy(cfg, monkeypatch):
    cfg(explorer_browser_url=BROWSER, explorer_browser_token="s3cret", explorer_proxy_prefix="")
    r = install(monkeypatch, lambda url, data: {"items": [1, 2, 3]})
    assert ihttp.get_json(EXPLORER_URL) == {"items": [1, 2, 3]}
    assert r.urls() == [f"{BROWSER}/internal/fetch"]
    assert json.loads(r.calls[0]["data"]) == {"url": EXPLORER_URL} and r.calls[0]["auth"] == "Bearer s3cret"


def test_market_urls_stay_direct_even_with_the_browser_configured(cfg, monkeypatch):
    cfg(explorer_browser_url=BROWSER)
    r = install(monkeypatch, lambda url, data: {"pairs": []})
    ihttp.get_json(DEX)
    assert r.urls() == [DEX]


def test_browser_failure_falls_back_to_the_proxy_only_if_one_is_configured(cfg, monkeypatch):
    cfg(explorer_browser_url=BROWSER, explorer_proxy_prefix=PROXY)

    def answer(url, data):
        if url.startswith(BROWSER):
            return urllib.error.URLError("connection refused")
        return {"data": {"content": json.dumps({"ok": True})}}

    r = install(monkeypatch, answer)
    assert ihttp.get_json(EXPLORER_URL) == {"ok": True}
    assert r.urls()[-1] == f"{PROXY}/{EXPLORER_URL}" and any(u.startswith(BROWSER) for u in r.urls())
    assert sum(u.startswith(BROWSER) for u in r.urls()) == 2  # unreachable sidecar: two tries, then the fallback


def test_browser_failure_without_proxy_reports_the_browser_and_never_a_proxy(cfg, monkeypatch):
    cfg(explorer_browser_url=BROWSER, explorer_proxy_prefix="")
    r = install(monkeypatch, lambda url, data: urllib.error.HTTPError(url, 503, "challenge_unsolved", {}, io.BytesIO(b"")))
    with pytest.raises(ihttp.HttpError) as e:
        ihttp.get_json(EXPLORER_URL)
    assert "browser:" in str(e.value) and "proxy" not in str(e.value).lower()
    assert all(u.startswith(BROWSER) for u in r.urls())


def test_upstream_404_through_the_browser_is_not_retried(cfg, monkeypatch):
    cfg(explorer_browser_url=BROWSER)
    r = install(monkeypatch, lambda url, data: urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"")))
    with pytest.raises(ihttp.HttpError):
        ihttp.get_json(EXPLORER_URL)
    assert len(r.calls) == 1


def test_explorer_rpm_default_for_the_browser_route(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {"EXPLORER_BROWSER_URL": BROWSER})
    monkeypatch.delenv("EXPLORER_RPM", raising=False)
    monkeypatch.delenv("EXPLORER_PROXY_KEY", raising=False)
    assert icfg.explorer_rpm() == 60


# ---------- acceptance: a scan without any proxy still has holders, real hands and liquidity ----------

def test_scan_with_the_jina_key_removed_still_has_holders_real_hands_and_liquidity(cfg, monkeypatch):
    cfg(explorer_browser_url=BROWSER, explorer_proxy_prefix="", explorer_proxy_key="", rpc_url="http://rpc.test")
    holders = {"items": [{"address": {"hash": a, "is_contract": False}, "value": str(v * E18)}
                         for a, v in [(W[0], 100), (W[1], 90), ("0x" + "b0" * 20, 5), ("0x" + "b1" * 20, 5)]]}

    def answer(url, data):
        if url.startswith(BROWSER):
            target = json.loads(data)["url"]
            assert target.startswith(EXPLORER)
            return {"holders_count": "108"} if target.endswith(T) else holders
        if "dexscreener" in url:
            return {"pairs": [{"chainId": "robinhood", "pairAddress": P, "priceUsd": "0.5", "fdv": 5_000_000,
                               "liquidity": {"usd": 750_000}, "priceChange": {"h24": 1.0},
                               "baseToken": {"symbol": "MOON", "name": "Mooncat"},
                               "info": {"websites": [{"url": "x"}], "socials": [{"type": "twitter"}]}}]}
        raise AssertionError(f"unexpected URL {url}")

    r = install(monkeypatch, answer)
    chain = Chain(SCAM, bytecode("a9059cbb"), owner="0x" + "00" * 20)
    out = intelligence.scan(T, transport=chain)
    assert out["holders_count"] == 108 and out["holders_count_exact"] is True
    assert out["real_hands_pct"] is not None and out["liquidity_usd"] == 750_000
    assert out["verdict"] != "UNKNOWN"
    assert not any("proxy" in e.lower() or "jina" in e.lower() for e in out["errors"]), out["errors"]
    assert not any("jina" in u or PROXY in u for u in r.urls())
    assert "explorer" not in "".join(r.urls()).replace(f"{BROWSER}/internal/fetch", "")  # explorer only via the sidecar
    assert any(u == f"{BROWSER}/internal/fetch" for u in r.urls()) and any("dexscreener" in u for u in r.urls())
