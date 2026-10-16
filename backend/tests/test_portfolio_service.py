import json

import httpx
import pytest

from app.models import Token
from app.services.portfolio_service import PortfolioService, change_from_pct, next_avg_cost
from app.services.rpc import SEL_BALANCE_OF, RPCClient

OWNER = "0x" + "11" * 20
HOOD = "0x" + "aa" * 20
PLUM = "0x" + "bb" * 20
EMPTY = "0x" + "cc" * 20
BALANCES = {HOOD: 4 * 10**18, PLUM: 860 * 10**9, EMPTY: 0}


def fake_rpc() -> RPCClient:
    def handler(request: httpx.Request) -> httpx.Response:
        out = []
        for c in json.loads(request.content):
            if c["method"] == "eth_getBalance":
                res = hex(10**18)  # 1 ETH
            elif c["method"] == "eth_call" and c["params"][0]["data"].startswith(SEL_BALANCE_OF):
                res = hex(BALANCES[c["params"][0]["to"]])
            else:
                return httpx.Response(400)
            out.append({"jsonrpc": "2.0", "id": c["id"], "result": res})
        return httpx.Response(200, json=out)

    return RPCClient("http://rpc", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


class FakeMarket:
    async def native_price_usd(self, symbol="ETH"):
        return 3000.0

    async def tokens_market(self, addresses):
        return {}


@pytest.fixture
async def svc(db, redis):
    db.add_all([
        Token(address=HOOD, symbol="HOOD-X", name="Hood Index", decimals=18, price_usd=1250, price_change_24h=8.4),
        Token(address=PLUM, symbol="PLUM", decimals=9, price_usd=2.826, price_change_24h=-5.2),
        Token(address=EMPTY, symbol="ZERO", decimals=18, price_usd=1),
    ])
    await db.commit()
    return PortfolioService(db, redis, fake_rpc(), FakeMarket())


def test_helpers():
    assert change_from_pct(110, 10) == pytest.approx(10)
    assert change_from_pct(100, None) == 0
    assert next_avg_cost(0, None, 10, 5) == 5
    assert next_avg_cost(10, 5, 20, 15) == pytest.approx(10)  # buy blends
    assert next_avg_cost(20, 10, 5, 30) == 10  # sell keeps avg


async def test_portfolio_holdings_and_24h(svc):
    p = await svc.get(OWNER)
    assert [h.symbol for h in p.holdings] == ["HOOD-X", "ETH", "PLUM"]  # sorted by value, zero balance hidden
    hood = p.holdings[0]
    assert hood.amount == 4 and hood.value_usd == 5000
    assert p.total_usd == pytest.approx(5000 + 3000 + 860 * 2.826, abs=0.01)
    expected_change = change_from_pct(5000, 8.4) + change_from_pct(860 * 2.826, -5.2)
    assert p.change_24h_usd == pytest.approx(expected_change, abs=0.01)
    assert hood.pnl_pct is None  # no snapshots yet


async def test_snapshot_then_pnl(svc, db):
    assert await svc.snapshot(OWNER) == 3
    tok = await db.get(Token, 1)
    tok.price_usd = 1500  # +20% since entry
    await db.commit()
    p = await svc.get(OWNER, use_cache=False)
    hood = next(h for h in p.holdings if h.symbol == "HOOD-X")
    assert hood.pnl_pct == pytest.approx(20)


async def test_portfolio_is_cached(svc, redis):
    await svc.get(OWNER)
    assert await redis.exists(f"portfolio:{OWNER}")


# How the given pool's liquidity compares to a holding: a token worth more than its own pool can pay out is
# paper value, so the portfolio keeps the row but leaves it out of the total (Holding.unrealizable).
async def test_invalid_address(svc):
    with pytest.raises(ValueError):
        await svc.get("0x123")


def held_rpc(balances: dict[str, int], seen: list[str], metas: dict[str, tuple[str, int]] | None = None) -> RPCClient:
    """alchemy_getTokenBalances answers `balances`; eth_call answers symbol/name/decimals from `metas`."""
    from app.services.rpc import SEL_DECIMALS, SEL_NAME, SEL_SYMBOL

    def abi(text: str) -> str:
        b = text.encode()
        return "0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex() + b.ljust(32, b"\0").hex()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls = body if isinstance(body, list) else [body]
        seen.append(f"http:{len(calls)}")
        out = []
        for c in calls:
            seen.append(c["method"])
            if c["method"] == "eth_getBalance":
                res = hex(0)
            elif c["method"] == "alchemy_getTokenBalances":
                res = {"tokenBalances": [{"contractAddress": a, "tokenBalance": hex(v)} for a, v in balances.items()]}
            elif c["method"] == "eth_call":
                to, data = c["params"][0]["to"], c["params"][0]["data"]
                sym, dec = (metas or {}).get(to, (None, None))
                if sym is None:
                    out.append({"jsonrpc": "2.0", "id": c["id"], "error": {"code": 3, "message": "reverted"}})
                    continue
                res = abi(sym) if data == SEL_SYMBOL else abi(sym + " token") if data == SEL_NAME else hex(dec)
            else:
                return httpx.Response(400)
            out.append({"jsonrpc": "2.0", "id": c.get("id"), "result": res})
        return httpx.Response(200, json=out if isinstance(body, list) else out[0])

    return RPCClient("http://rpc", httpx.AsyncClient(transport=httpx.MockTransport(handler)), batch_size=50)


class Market(FakeMarket):
    def __init__(self, data=None, boom=False):
        self.data, self.boom, self.asked = data or {}, boom, []

    async def tokens_market(self, addresses):
        self.asked.append(list(addresses))
        if self.boom:
            raise RuntimeError("dexscreener down")
        return {a: self.data[a] for a in addresses if a in self.data}


def mk(i: int) -> str:
    return "0x" + f"{i:040x}"


async def test_every_nonzero_balance_gets_a_row_not_just_indexed_tokens(db, redis):
    """Measured: 47 balances on chain, 3 in our tokens table -> 44 holdings were invisible."""
    addrs = [mk(i) for i in range(1, 48)]
    balances = {a: 10**18 for a in addrs}
    metas = {a: (f"T{i}", 18) for i, a in enumerate(addrs) if i != 5}  # one token's calls revert
    db.add_all([Token(address=a, symbol=f"DB{i}", decimals=18, price_usd=1.0, liquidity_usd=1e9) for i, a in enumerate(addrs[:3])])
    await db.commit()
    market = {a: {"symbol": "X", "name": "X", "price_usd": 2.0, "price_change_24h": 1.0, "liquidity_usd": 1e6} for a in addrs[3:20]}
    seen: list[str] = []
    mkt = Market(market)
    p = await PortfolioService(db, redis, held_rpc(balances, seen, metas), mkt).get(OWNER)
    assert len(p.holdings) == 47 and p.excluded_count == 0
    priced = [h for h in p.holdings if h.price_usd is not None]
    assert len(priced) == 3 + 16  # 17 had market data, one of them (index 5) has unreadable decimals -> not priced
    nodata = next(h for h in p.holdings if h.token == addrs[40])
    assert (nodata.symbol, nodata.price_usd, nodata.value_usd) == ("T40", None, 0.0)  # appears, worth nothing, no number
    assert next(h for h in p.holdings if h.token == addrs[5]).symbol == "X"  # unreadable on chain: the market symbol, still listed
    assert seen.count("alchemy_getTokenBalances") == 1
    assert max(int(x.split(":")[1]) for x in seen if x.startswith("http:")) <= 50  # batched, never 1 request per token
    assert sum(x.startswith("http:") for x in seen) <= 6  # not one request per token
    assert len(mkt.asked) == 1 and len(mkt.asked[0]) == 44


async def test_a_failing_market_lookup_does_not_fail_the_page(db, redis):
    seen: list[str] = []
    p = await PortfolioService(db, redis, held_rpc({mk(1): 10**18}, seen, {mk(1): ("AAA", 18)}), Market(boom=True)).get(OWNER)
    assert [(h.symbol, h.value_usd) for h in p.holdings] == [("AAA", 0.0)]


async def test_metadata_is_cached_between_loads(db, redis):
    seen: list[str] = []
    svc = PortfolioService(db, redis, held_rpc({mk(1): 10**18}, seen, {mk(1): ("AAA", 18)}), Market())
    await svc.get(OWNER, use_cache=False)
    calls = seen.count("eth_call")
    await svc.get(OWNER, use_cache=False)
    assert calls == 3 and seen.count("eth_call") == 3


def test_market_pairs_pick_the_deepest_pool_per_token():
    from app.services.market import best_pairs

    pairs = [{"baseToken": {"address": "0xAA", "symbol": "A"}, "priceUsd": "1", "liquidity": {"usd": 10}},
             {"baseToken": {"address": "0xaa", "symbol": "A"}, "priceUsd": "2", "liquidity": {"usd": 500}}]
    assert best_pairs(pairs)["0xaa"]["price_usd"] == 2.0


async def test_unrealizable_holding_is_left_out_entirely(db, redis):
    """A 66-unit token calling itself ETH in a $2 055 pool was shown as a $161 609 balance."""
    db.add(Token(address=HOOD, symbol="ETH", name="Ethereum", decimals=18, price_usd=2448.63, liquidity_usd=2055.63))
    await db.commit()
    seen: list[str] = []
    svc = PortfolioService(db, redis, held_rpc({HOOD: 66 * 10**18}, seen), Market())
    p = await svc.get(OWNER)
    assert p.holdings == [] and p.excluded_count == 1 and p.total_usd == 0  # no row, no fantasy number, counted
    assert "161" not in p.model_dump_json()

    tok = await db.get(Token, 1)
    tok.liquidity_usd = 10_000_000  # a pool that CAN pay out is counted normally
    await db.commit()
    p2 = await svc.get(OWNER, use_cache=False)
    assert p2.total_usd == pytest.approx(66 * 2448.63, abs=0.01) and p2.excluded_count == 0


def indexed_rpc(seen: list[str]) -> RPCClient:
    """An endpoint that indexes wallet balances (alchemy_getTokenBalances) — the portfolio must use it
    ONCE and never send a balanceOf per known token."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls = body if isinstance(body, list) else [body]
        out = []
        for c in calls:
            seen.append(c["method"])
            if c["method"] == "eth_getBalance":
                res = hex(10**18)
            elif c["method"] == "alchemy_getTokenBalances":
                res = {"tokenBalances": [
                    {"contractAddress": HOOD, "tokenBalance": hex(4 * 10**18)},
                    {"contractAddress": EMPTY, "tokenBalance": "0x0"},          # zero -> dropped by the node
                    {"contractAddress": "0x" + "dd" * 20, "tokenBalance": hex(7)},  # unknown to us -> ignored
                ]}
            elif c["method"] == "eth_call":  # symbol()/decimals() of the unindexed token: unreadable
                out.append({"jsonrpc": "2.0", "id": c.get("id"), "error": {"code": 3, "message": "reverted"}})
                continue
            else:
                return httpx.Response(400)
            out.append({"jsonrpc": "2.0", "id": c.get("id"), "result": res})
        return httpx.Response(200, json=out if isinstance(body, list) else out[0])

    return RPCClient("http://rpc", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_portfolio_asks_the_chain_once_instead_of_per_token(db, redis):
    seen: list[str] = []
    svc = PortfolioService(db, redis, indexed_rpc(seen), FakeMarket())
    db.add(Token(address=HOOD, symbol="HOOD-X", name="Hood Index", decimals=18, price_usd=1250))
    db.add(Token(address=PLUM, symbol="PLUM", decimals=9, price_usd=2.826))
    await db.commit()

    p = await svc.get(OWNER)
    assert [h.symbol for h in p.holdings] == ["HOOD-X", "ETH", "?"]  # PLUM isn't held; the unknown token still shows
    assert seen.count("alchemy_getTokenBalances") == 1
    assert not any(h.value_usd for h in p.holdings if h.symbol == "?")  # ...with no invented value

