from app.services.intelligence import rpc as irpc
from app.services.intelligence.http import HttpError


def test_rpc_fails_over_to_next_endpoint(monkeypatch):
    seen = []

    def post(url, payload):
        seen.append(url)
        if "primary" in url:
            raise HttpError("503")
        return [{"jsonrpc": "2.0", "id": payload[0]["id"], "result": "0x10"}]

    monkeypatch.setattr(irpc, "post_json", post)
    r = irpc.Rpc("https://primary.example, https://backup.example")
    assert r.block_number() == 16 and seen == ["https://primary.example", "https://backup.example"]
    assert r.block_number() == 16 and seen[-1] == "https://backup.example"  # promoted, primary not retried first


async def test_health_sources_reports_each_source(client, monkeypatch):
    from app.api import health

    monkeypatch.setattr(health, "rpc_urls", lambda: "https://rpc.example/v2/abcdefghijklmnopqrstuvwxyz123456")
    monkeypatch.setattr(health.Rpc, "block_number", lambda self: 123)

    def get_json(url, tries=1):
        if "dexscreener" in url:
            raise HttpError("403")
        return {"total_blocks": "5", "data": [{"id": "uniswap-v4"}]}

    monkeypatch.setattr(health, "get_json", get_json)
    body = (await client.get("/health/sources?fresh=true")).json()
    by = {s["name"]: s for s in body["sources"]}
    assert body["status"] == "degraded"  # market source down, chain + db fine
    assert by["postgres"]["ok"] and by["redis"]["ok"] and not by["dexscreener"]["ok"]
    assert "rpc https://rpc.example/v2/abcd…" in by  # API key redacted
    assert by["rpc https://rpc.example/v2/abcd…"]["detail"] == {"block": 123}


async def test_rate_limit_fails_open_without_redis(client, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnError

    from app.core import ratelimit

    async def broken(*a, **k):
        raise RedisConnError("down")

    monkeypatch.setattr(ratelimit, "_hit", broken)
    assert (await client.get("/auth/siwe/nonce")).status_code == 200
