"""The fetcher against a real Chromium and a fake Cloudflare-challenged explorer."""
import json

import pytest

from fetcher import FetchError, Fetcher
from tests.conftest import CHROMIUM, needs_chromium

pytestmark = needs_chromium


def make(tmp_path, explorer, **kw):
    return Fetcher(profile_dir=str(tmp_path / "profile"), chromium_path=CHROMIUM, allowed_hosts={explorer.host},
                   challenge_timeout=kw.pop("challenge_timeout", 15), nav_timeout=15, **kw)


async def test_solves_the_challenge_and_returns_the_json(tmp_path, explorer):
    f = make(tmp_path, explorer)
    await f.start()
    try:
        res = await f.fetch(f"{explorer.base}/api/v2/tokens/0xabc/holders")
        assert res.status == 200 and "json" in res.content_type
        body = json.loads(res.text)
        assert len(body["items"]) == 50  # the same shape the engine parses
        assert explorer.challenges >= 1 and f.stats["challenges_solved"] == 1
        assert any("cf_clearance=ok" in c for c in explorer.cookies_seen)  # later requests carried the clearance
    finally:
        await f.stop()


async def test_the_cleared_session_is_reused(tmp_path, explorer):
    f = make(tmp_path, explorer)
    await f.start()
    try:
        await f.fetch(f"{explorer.base}/api/v2/tokens/0x1/holders")
        solved_challenges = explorer.challenges
        for i in range(5):
            assert (await f.fetch(f"{explorer.base}/api/v2/tokens/0x{i}/holders")).status == 200
        assert explorer.challenges == solved_challenges  # no new challenge for the next five reads
        assert f.stats["challenges_solved"] == 1 and f.stats["fetches"] == 6
    finally:
        await f.stop()


async def test_clearance_survives_a_restart_in_the_persistent_profile(tmp_path, explorer):
    first = make(tmp_path, explorer)
    await first.start()
    await first.fetch(f"{explorer.base}/api/v2/stats")
    await first.stop()
    explorer.challenges = 0
    second = make(tmp_path, explorer)  # same profile directory = same cookies
    await second.start()
    try:
        assert (await second.fetch(f"{explorer.base}/api/v2/stats")).status == 200
        assert explorer.challenges == 0  # no challenge: the cf_clearance cookie was reloaded from disk
    finally:
        await second.stop()


async def test_expired_clearance_is_solved_again_transparently(tmp_path, explorer):
    f = make(tmp_path, explorer)
    await f.start()
    try:
        await f.fetch(f"{explorer.base}/api/v2/stats")
        await (await f._ensure_context()).clear_cookies()  # Cloudflare revokes the clearance
        before = f.stats["challenges_solved"]
        res = await f.fetch(f"{explorer.base}/api/v2/tokens/0x9/holders")
        assert res.status == 200 and f.stats["challenges_solved"] == before + 1
    finally:
        await f.stop()


async def test_upstream_statuses_pass_through(tmp_path, explorer):
    f = make(tmp_path, explorer)
    await f.start()
    try:
        limited = await f.fetch(f"{explorer.base}/api/v2/rate-limited")
        assert limited.status == 429 and limited.retry_after == "7"
        assert (await f.fetch(f"{explorer.base}/api/v2/missing")).status == 404
    finally:
        await f.stop()


async def test_an_unsolvable_challenge_fails_cleanly_not_forever(tmp_path, explorer):
    explorer.never_solve = True
    f = make(tmp_path, explorer, challenge_timeout=2.5)
    await f.start()
    try:
        with pytest.raises(FetchError) as e:
            await f.fetch(f"{explorer.base}/api/v2/stats")
        assert e.value.status == 503 and e.value.detail == "challenge_unsolved"
        assert f.stats["challenges_failed"] >= 1
    finally:
        await f.stop()


async def test_concurrent_reads_share_one_challenge_solve(tmp_path, explorer):
    import asyncio

    f = make(tmp_path, explorer, concurrency=4)
    await f.start()
    try:
        results = await asyncio.gather(*[f.fetch(f"{explorer.base}/api/v2/tokens/0x{i}/holders") for i in range(6)])
        assert all(r.status == 200 for r in results)
        assert f.stats["challenges_solved"] <= 2  # not one per request
    finally:
        await f.stop()


async def test_only_allowed_hosts_and_http_urls_are_fetched(tmp_path, explorer):
    f = make(tmp_path, explorer)  # no browser needed: validation happens first
    for url, status in [("http://169.254.169.254/latest/meta-data", 403), ("https://example.com/", 403),
                        ("file:///etc/passwd", 400), ("not a url", 400), ("ftp://127.0.0.1/x", 400)]:
        with pytest.raises(FetchError) as e:
            await f.fetch(url)
        assert e.value.status == status, url
    empty = Fetcher(profile_dir=str(tmp_path / "p2"), chromium_path=CHROMIUM, allowed_hosts=set())
    with pytest.raises(FetchError) as e:
        await empty.fetch(f"{explorer.base}/x")
    assert e.value.status == 403  # fail closed: no allowlist, no fetching
