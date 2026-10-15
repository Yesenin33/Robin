"""RPC reliability: 429 backoff (Retry-After, jitter), benching broken nodes, 5xx never ends the chain,
getLogs bursts bounded, learned range caps, async client batching/failover."""
import threading
import time as real_time

import httpx
import pytest

from app.services.intelligence import config as icfg
from app.services.intelligence import endpoints as ep
from app.services.intelligence import rpc as irpc
from app.services.intelligence.endpoints import POOL
from app.services.rpc import RPCClient, RPCError

OFFICIAL = "https://rpc.mainnet.chain.robinhood.com"
DRPC = "https://robinhood.drpc.org"
PUBLIC = "https://robinhood-rpc.publicnode.com"
ALCHEMY = "https://robinhood-mainnet.g.alchemy.com/v2/KEY"


class FakeTime:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


@pytest.fixture
def ft(monkeypatch):
    t = FakeTime()
    monkeypatch.setattr(irpc, "time", t)
    monkeypatch.setattr(POOL, "_clock", t.monotonic)
    monkeypatch.setattr(POOL, "_jitter", lambda: 1.0)
    monkeypatch.setattr(irpc.random, "uniform", lambda a, b: (a + b) / 2)
    return t


def ok(result):
    return lambda payload: [{"jsonrpc": "2.0", "id": c["id"], "result": result} for c in payload]


class Net:
    """Scripted HTTP: host -> answers [(status, body|callable, retry_after)]; the last answer repeats."""

    def __init__(self, script):
        self.script = {h: list(a) for h, a in script.items()}
        self.hits: list[tuple[str, str]] = []

    def __call__(self, url, payload, timeout):
        host = url.split("/")[2]
        self.hits.append((host, payload[0]["method"]))
        answers = self.script[host]
        status, body, *rest = answers.pop(0) if len(answers) > 1 else answers[0]
        body = body(payload) if callable(body) else body
        return (status, body, rest[0] if rest else None)

    def count(self, host):
        return sum(h == host for h, _ in self.hits)


def logs_call(span, from_block=100):
    return [{"params": [{"fromBlock": hex(from_block), "toBlock": hex(from_block + span - 1)}]}]


def get_logs(r, span=9_999):
    return r.call("eth_getLogs", [{"fromBlock": hex(100), "toBlock": hex(100 + span - 1)}])


# ---------- the reported incident ----------

def test_throttled_official_and_500_drpc_do_not_end_the_chain(ft):
    net = Net({"rpc.mainnet.chain.robinhood.com": [(429, None, "7")], "robinhood.drpc.org": [(500, None)],
               "robinhood-rpc.publicnode.com": [(200, ok("0x10"))]})
    r = irpc.Rpc(f"{OFFICIAL},{DRPC},{PUBLIC}", poster=net)
    assert r.block_number() == 16  # publicnode answers although official 429s and drpc (before it) 500s
    assert r.urls[0] == PUBLIC  # promoted only after a clean answer
    for _ in range(5):
        assert r.block_number() == 16
    # no amplification: the throttled node and the broken node are left alone while cooling
    assert net.count("rpc.mainnet.chain.robinhood.com") == 1
    assert net.count("robinhood.drpc.org") <= 2  # first call's attempt + its one quick retry, then benched
    assert net.count("robinhood-rpc.publicnode.com") == 6


def test_429_cooldown_honours_retry_after_and_expires(ft):
    net = Net({"rpc.mainnet.chain.robinhood.com": [(429, None, "30"), (200, ok("0x1"))],
               "robinhood-rpc.publicnode.com": [(200, ok("0x2"))]})
    r = irpc.Rpc(f"{OFFICIAL},{PUBLIC}", poster=net)
    assert r.block_number() == 2
    assert POOL.snapshot(OFFICIAL)["cooling_s"] == pytest.approx(30)  # Retry-After beats the 2 s default
    ft.now += 29
    assert r.block_number() == 2 and net.count("rpc.mainnet.chain.robinhood.com") == 1  # still cooling
    ft.now += 2
    r.urls = [OFFICIAL, PUBLIC]
    assert r.block_number() == 1  # cooldown over: official is used again and answers


def test_429_without_retry_after_backs_off_exponentially_with_jitter(ft):
    for i, expected in enumerate([2, 4, 8, 16, 32, 60, 60], start=1):
        cool = POOL.failure("https://x.test", ep.EndpointFailure("rate_limited", "HTTP 429"))
        assert cool == expected, (i, cool)
    POOL.reset()
    monkey = [0.75, 1.25]
    low = ep.EndpointPool(clock=ft.monotonic, jitter=lambda: monkey[0])
    high = ep.EndpointPool(clock=ft.monotonic, jitter=lambda: monkey[1])
    f = ep.EndpointFailure("rate_limited", "HTTP 429")
    assert (low.failure("u", f), high.failure("u", f)) == (1.5, 2.5)


def test_single_endpoint_waits_out_a_short_retry_after_then_succeeds(ft):
    net = Net({"robinhood.drpc.org": [(429, None, "7"), (200, ok("0x9"))]})
    assert irpc.Rpc(DRPC, poster=net).block_number() == 9
    assert sum(ft.slept) == pytest.approx(7) and len(net.hits) == 2


def test_long_retry_after_fails_fast_instead_of_sleeping(ft):
    net = Net({"robinhood.drpc.org": [(429, None, "100")]})
    with pytest.raises(irpc.RpcError, match="HTTP 429"):
        irpc.Rpc(DRPC, poster=net).block_number()
    assert sum(ft.slept) == 0 and len(net.hits) == 1


def test_one_500_blip_is_retried_once_and_then_forgiven(ft):
    net = Net({"robinhood.drpc.org": [(500, None), (200, ok("0x3"))]})
    assert irpc.Rpc(DRPC, poster=net).block_number() == 3
    assert len(net.hits) == 2 and POOL.snapshot(DRPC)["fails"] == 0 and ft.slept == [0.5]


def test_persistently_broken_endpoint_is_benched_with_growing_cooldown(ft):
    net = Net({"robinhood.drpc.org": [(500, None)], "robinhood-rpc.publicnode.com": [(200, ok("0x2"))]})
    r = irpc.Rpc(f"{DRPC},{PUBLIC}", poster=net)
    assert r.block_number() == 2
    assert net.count("robinhood.drpc.org") == 2  # attempt + one quick retry; two failures in a row bench it
    assert POOL.snapshot(DRPC)["cooling_s"] == pytest.approx(15)
    for _ in range(5):
        r.urls = [DRPC, PUBLIC]
        assert r.block_number() == 2
    assert net.count("robinhood.drpc.org") == 2  # benched: not even tried again
    ft.now += 16  # cooldown over -> probed once, still broken -> benched for longer
    r.urls = [DRPC, PUBLIC]
    assert r.block_number() == 2 and net.count("robinhood.drpc.org") == 3
    assert POOL.snapshot(DRPC)["cooling_s"] == pytest.approx(30)
    pool = ep.EndpointPool(clock=ft.monotonic, jitter=lambda: 1.0)
    seq = [pool.failure("u", ep.EndpointFailure("server", "HTTP 500")) for _ in range(9)]
    assert seq == [0, 15, 30, 60, 120, 240, 300, 300, 300]  # one blip is free, then exponential, capped at 5 min


def test_json_rpc_internal_error_in_a_200_body_fails_over(ft):
    body = lambda p: [{"jsonrpc": "2.0", "id": p[0]["id"], "error": {"code": -32603, "message": "Internal error"}}]  # noqa: E731
    net = Net({"robinhood.drpc.org": [(200, body)], "rpc.mainnet.chain.robinhood.com": [(200, ok("0x5"))]})
    assert irpc.Rpc(f"{DRPC},{OFFICIAL}", poster=net).block_number() == 5


def test_error_message_lists_every_endpoint(ft):
    net = Net({"rpc.mainnet.chain.robinhood.com": [(429, None, "100")], "robinhood.drpc.org": [(500, None)]})
    with pytest.raises(irpc.RpcError) as e:
        irpc.Rpc(f"{OFFICIAL},{DRPC}", poster=net).block_number()
    assert "rpc.mainnet.chain.robinhood.com: HTTP 429" in str(e.value) and "robinhood.drpc.org: HTTP 500" in str(e.value)


# ---------- eth_getLogs ----------

def test_getlogs_prefers_its_own_endpoint_list(ft):
    net = Net({"rpc.mainnet.chain.robinhood.com": [(200, ok("0x7"))], "robinhood-rpc.publicnode.com": [(200, ok([]))]})
    r = irpc.Rpc(OFFICIAL, poster=net, logs_urls=[PUBLIC])
    assert r.block_number() == 7 and get_logs(r) == []
    assert net.hits == [("rpc.mainnet.chain.robinhood.com", "eth_blockNumber"),
                        ("robinhood-rpc.publicnode.com", "eth_getLogs")]


def test_getlogs_falls_back_to_the_official_node_when_the_logs_endpoint_is_down(ft):
    net = Net({"robinhood-rpc.publicnode.com": [(500, None)], "rpc.mainnet.chain.robinhood.com": [(200, ok([]))]})
    r = irpc.Rpc(OFFICIAL, poster=net, logs_urls=[PUBLIC])
    assert get_logs(r) == []
    assert net.count("rpc.mainnet.chain.robinhood.com") == 1


def test_range_error_teaches_a_cap_and_wide_calls_skip_that_endpoint(ft):
    err = lambda p: [{"jsonrpc": "2.0", "id": p[0]["id"], "error": {"code": -32600, "message": "block range too large, max 100 blocks"}}]  # noqa: E731
    net = Net({"logs.example": [(200, err)], "rpc.mainnet.chain.robinhood.com": [(200, ok([]))]})
    r = irpc.Rpc("https://logs.example,https://rpc.mainnet.chain.robinhood.com", poster=net)
    assert get_logs(r, 9_999) == []
    assert POOL.snapshot("https://logs.example")["log_range_cap"] == 100
    assert POOL.snapshot("https://logs.example")["cooling_s"] == 0  # a narrow node is not a broken node
    net.hits.clear()
    r.urls = ["https://logs.example", OFFICIAL]
    get_logs(r, 9_999)
    assert net.count("logs.example") == 0  # skipped: too wide for it
    r.urls = ["https://logs.example", OFFICIAL]
    get_logs(r, 50)
    assert net.count("logs.example") == 1  # still used for narrow ranges


def test_alchemy_is_never_sent_a_wide_getlogs(ft):
    net = Net({"robinhood-mainnet.g.alchemy.com": [(200, ok([]))], "rpc.mainnet.chain.robinhood.com": [(200, ok([]))]})
    r = irpc.Rpc(f"{ALCHEMY},{OFFICIAL}", poster=net)
    get_logs(r, 9_999)
    assert net.count("robinhood-mainnet.g.alchemy.com") == 0


def test_getlogs_burst_is_bounded_across_threads(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {"RPC_LOGS_CONCURRENCY": "2", "RPC_LOGS_MIN_INTERVAL_MS": "0"})
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def poster(url, payload, timeout):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        real_time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return 200, [{"jsonrpc": "2.0", "id": payload[0]["id"], "result": []}], None

    def work():
        get_logs(irpc.Rpc(OFFICIAL, poster=poster))

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state["peak"] <= 2  # worker + indexer + scans together never exceed the configured burst


def test_getlogs_min_interval_spaces_calls(monkeypatch):
    monkeypatch.setattr(icfg, "_overrides", {"RPC_LOGS_CONCURRENCY": "4", "RPC_LOGS_MIN_INTERVAL_MS": "120"})
    stamps = []

    def poster(url, payload, timeout):
        stamps.append(real_time.monotonic())
        return 200, [{"jsonrpc": "2.0", "id": payload[0]["id"], "result": []}], None

    r = irpc.Rpc(OFFICIAL, poster=poster)
    for _ in range(3):
        get_logs(r)
    assert stamps[1] - stamps[0] >= 0.1 and stamps[2] - stamps[1] >= 0.1


# ---------- async client (portfolio / position snapshots) ----------

def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def echo_ok(request: httpx.Request) -> httpx.Response:
    import json

    return httpx.Response(200, json=[{"jsonrpc": "2.0", "id": c["id"], "result": "0x1"} for c in json.loads(request.content)])


async def test_async_batch_is_chunked():
    sizes = []

    def handler(request):
        import json

        sizes.append(len(json.loads(request.content)))
        return echo_ok(request)

    c = RPCClient("https://a.example", mock_client(handler), batch_size=50)
    out = await c.batch([("eth_blockNumber", [])] * 120)
    assert sizes == [50, 50, 20] and out == ["0x1"] * 120


async def test_async_error_object_and_429_fail_over_and_cool_down():
    import json

    seen = {"a": 0, "b": 0, "c": 0}

    def handler(request):
        host = request.url.host
        seen[host[0]] += 1
        if host.startswith("a"):  # a whole-batch error comes back as ONE object, not a list
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch too large"}})
        if host.startswith("b"):
            return httpx.Response(429, headers={"Retry-After": "30"}, json={})
        return echo_ok(request)

    c = RPCClient("https://a.example,https://b.example,https://c.example", mock_client(handler))
    assert await c.call("eth_blockNumber", []) == "0x1"
    for _ in range(3):
        assert await c.call("eth_blockNumber", []) == "0x1"
    assert seen["b"] == 1 and seen["c"] == 4  # the throttled node isn't re-hit while cooling
    assert POOL.snapshot("https://b.example")["cooling_s"] > 25


async def test_async_all_failing_raises_a_readable_error(monkeypatch):
    c = RPCClient("https://a.example", mock_client(lambda r: httpx.Response(429, headers={"Retry-After": "100"}, json={})))
    with pytest.raises(RPCError, match="a.example: HTTP 429"):
        await c.call("eth_blockNumber", [])
