import pytest

from app.services.intelligence import rpc as irpc
from app.services.intelligence.http import HttpError


OFFICIAL = "https://rpc.mainnet.chain.robinhood.com"
DRPC = "https://robinhood.drpc.org"
ALCHEMY = "https://robinhood-mainnet.g.alchemy.com/v2/KEY"
TIER_ERR = {"code": -32600, "message": "Under the Free tier plan, you can make eth_getLogs requests with up to a 10 block range"}


class Poster:
    """Scripted HTTP layer: per-host list of (status, body-or-callable) answers, last one repeats."""

    def __init__(self, script):
        self.script, self.sent = script, []

    def __call__(self, url, payload, timeout):
        host = url.split("/")[2]
        self.sent.append((host, payload[0]["method"]))
        answers = self.script[host]
        status, body = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(body, Exception):
            raise body
        return status, body(payload) if callable(body) else body


def ok(result):
    return lambda payload: [{"jsonrpc": "2.0", "id": c["id"], "result": result} for c in payload]


def logs_payload(span):
    return [{"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
             "params": [{"fromBlock": hex(100), "toBlock": hex(100 + span - 1)}]}]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(irpc.time, "sleep", lambda s: None)


def test_getlogs_never_sent_to_capped_endpoint_and_alchemy_not_promoted():
    p = Poster({"robinhood-mainnet.g.alchemy.com": [(200, ok("0x10"))], "rpc.mainnet.chain.robinhood.com": [(200, ok([]))]})
    r = irpc.Rpc(f"{ALCHEMY},{OFFICIAL}", poster=p)
    assert r.block_number() == 16  # Alchemy may serve cheap calls...
    assert r.call("eth_getLogs", [{"fromBlock": hex(0), "toBlock": hex(9_998)}]) == []
    # ...but never a 9 999-block getLogs: it went straight to the official node, which is now first
    assert p.sent == [("robinhood-mainnet.g.alchemy.com", "eth_blockNumber"), ("rpc.mainnet.chain.robinhood.com", "eth_getLogs")]
    assert r.urls[0] == OFFICIAL


def test_json_rpc_error_body_fails_over_and_is_not_promoted():
    p = Poster({"robinhood.drpc.org": [(200, {"jsonrpc": "2.0", "id": 1, "error": TIER_ERR})],
                "rpc.mainnet.chain.robinhood.com": [(200, ok("0x5"))]})
    r = irpc.Rpc(f"{DRPC},{OFFICIAL}", poster=p)
    assert r.block_number() == 5 and r.urls == [OFFICIAL, DRPC]


def test_batch_array_with_node_error_fails_over():
    def batch_err(payload):
        return [{"jsonrpc": "2.0", "id": payload[0]["id"], "error": {"code": -32005, "message": "limit exceeded"}}]

    p = Poster({"robinhood.drpc.org": [(200, batch_err)], "rpc.mainnet.chain.robinhood.com": [(200, ok("0x7"))]})
    assert irpc.Rpc(f"{DRPC},{OFFICIAL}", poster=p).batch([("eth_blockNumber", [])]) == ["0x7"]


def test_execution_revert_is_an_answer_not_a_node_failure():
    def revert(payload):
        return [{"jsonrpc": "2.0", "id": payload[0]["id"], "error": {"code": 3, "message": "execution reverted"}}]

    p = Poster({"rpc.mainnet.chain.robinhood.com": [(200, revert)], "robinhood.drpc.org": [(200, ok("0x1"))]})
    r = irpc.Rpc(f"{OFFICIAL},{DRPC}", poster=p)
    assert r.eth_call("0x" + "11" * 20, "0x06fdde03") is None
    assert p.sent == [("rpc.mainnet.chain.robinhood.com", "eth_call")]  # no pointless failover


def test_debug_log_names_the_getlogs_endpoint(caplog):
    caplog.set_level("DEBUG", logger="intelligence.rpc")
    p = Poster({"rpc.mainnet.chain.robinhood.com": [(200, ok([]))]})
    irpc.Rpc(OFFICIAL, poster=p).call("eth_getLogs", [{"fromBlock": "0x0", "toBlock": "0x9"}])
    assert "eth_getLogs served by rpc.mainnet.chain.robinhood.com (range 10)" in caplog.text


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
    detail = by["rpc https://rpc.example/v2/abcd…"]["detail"]
    assert detail["block"] == 123 and detail["pool"] == {"fails": 0, "cooling_s": 0.0, "log_range_cap": None, "last_error": None}


async def test_rate_limit_fails_open_without_redis(client, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnError

    from app.core import ratelimit

    async def broken(*a, **k):
        raise RedisConnError("down")

    monkeypatch.setattr(ratelimit, "_hit", broken)
    assert (await client.get("/auth/siwe/nonce")).status_code == 200
