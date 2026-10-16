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


async def test_invalid_address(svc):
    with pytest.raises(ValueError):
        await svc.get("0x123")


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
    assert [h.symbol for h in p.holdings] == ["HOOD-X", "ETH"]  # PLUM isn't held; the unknown token is skipped
    assert seen.count("alchemy_getTokenBalances") == 1
    assert "eth_call" not in seen  # no balanceOf sweep at all

