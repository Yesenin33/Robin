"""HTTP surface of the sidecar (/internal/fetch, /health) over a real Chromium."""
import httpx
import pytest

import app as sidecar
from fetcher import Fetcher
from tests.conftest import CHROMIUM, needs_chromium

pytestmark = needs_chromium


@pytest.fixture
async def client(tmp_path, explorer, monkeypatch):
    monkeypatch.delenv("FETCH_TOKEN", raising=False)
    sidecar.app.state.fetcher = Fetcher(profile_dir=str(tmp_path / "profile"), chromium_path=CHROMIUM,
                                        allowed_hosts={explorer.host}, challenge_timeout=15, nav_timeout=15)
    async with sidecar.lifespan(sidecar.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sidecar.app), base_url="http://sidecar") as c:
            yield c
    sidecar.app.state.fetcher = None


async def test_post_fetch_returns_the_upstream_body_and_status(client, explorer):
    r = await client.post("/internal/fetch", json={"url": f"{explorer.base}/api/v2/tokens/0xabc/holders"})
    assert r.status_code == 200 and r.headers["x-upstream-status"] == "200"
    assert len(r.json()["items"]) == 50
    miss = await client.post("/internal/fetch", json={"url": f"{explorer.base}/api/v2/missing"})
    assert miss.status_code == 404 and miss.json() == {"message": "Not found"}
    limited = await client.get("/internal/fetch", params={"url": f"{explorer.base}/api/v2/rate-limited"})
    assert limited.status_code == 429 and limited.headers["retry-after"] == "7"


async def test_refuses_hosts_that_are_not_allowed(client):
    r = await client.post("/internal/fetch", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 403 and "host not allowed" in r.json()["error"]


async def test_bearer_token_is_enforced_when_configured(client, explorer, monkeypatch):
    monkeypatch.setenv("FETCH_TOKEN", "s3cret")
    url = f"{explorer.base}/api/v2/stats"
    assert (await client.post("/internal/fetch", json={"url": url})).status_code == 401
    assert (await client.post("/internal/fetch", json={"url": url}, headers={"Authorization": "Bearer nope"})).status_code == 401
    ok = await client.post("/internal/fetch", json={"url": url}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


async def test_unsolvable_challenge_is_a_503_the_engine_can_retry(client, explorer):
    explorer.never_solve = True
    sidecar.app.state.fetcher.challenge_timeout = 2.0
    r = await client.post("/internal/fetch", json={"url": f"{explorer.base}/api/v2/stats"})
    assert r.status_code == 503 and r.json() == {"error": "challenge_unsolved"}


async def test_health_reports_state_without_secrets(client, explorer):
    await client.post("/internal/fetch", json={"url": f"{explorer.base}/api/v2/stats"})
    h = (await client.get("/health")).json()
    assert h["ok"] and h["fetches"] == 1 and h["challenges_solved"] == 1 and h["allowed_hosts"] == [explorer.host]
