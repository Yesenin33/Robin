import httpx

from app.models import Token
from app.services.market import MarketClient, best_pairs, image_of
from tests.test_portfolio_service import OWNER, Market, PortfolioService, check, held_rpc, mk


def pair(addr, url, liq=10, chain="robinhood"):
    return {"chainId": chain, "baseToken": {"address": addr, "symbol": "S"}, "priceUsd": "1", "liquidity": {"usd": liq},
            "info": {"imageUrl": url} if url is not None else {}}


def test_only_https_bounded_urls_are_kept_as_art():
    assert image_of(pair("0x1", "https://cdn.test/a.png")) == "https://cdn.test/a.png"
    for bad in ("http://cdn.test/a.png", "javascript:alert(1)", "data:image/png;base64,AAAA", "", None, "https://x/" + "a" * 600):
        assert image_of(pair("0x1", bad)) is None
    assert best_pairs([pair("0xAA", "https://cdn.test/deep.png", 500), pair("0xaa", "https://cdn.test/thin.png", 5)])["0xaa"]["image_url"] == "https://cdn.test/deep.png"


async def test_market_batch_returns_art_and_caches_it(redis):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json=[pair("0x" + "11" * 20, "https://cdn.test/t.png")])

    m = MarketClient(redis, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    first = await m.tokens_market(["0x" + "11" * 20, "0x" + "22" * 20])
    again = await m.tokens_market(["0x" + "11" * 20, "0x" + "22" * 20])  # served from the cache, negatives included
    assert first["0x" + "11" * 20]["image_url"] == "https://cdn.test/t.png" and "0x" + "22" * 20 not in first
    assert again == first and len(calls) == 1


async def test_portfolio_rows_carry_art_when_known_and_none_otherwise(db, redis):
    a, b = mk(1), mk(2)
    db.add(Token(address=a, symbol="AAA", decimals=18, price_usd=1.0, liquidity_usd=1e9, image_url="https://cdn.test/a.png"))
    await db.commit()
    await check(db, 1)
    market = Market({b: {"symbol": "BBB", "name": "B", "price_usd": 1.0, "price_change_24h": None, "liquidity_usd": 1e9,
                         "image_url": "https://cdn.test/b.png"}})
    p = await PortfolioService(db, redis, held_rpc({a: 10**18, b: 10**18, mk(3): 10**18}, [], {mk(3): ("CCC", 18), b: ("BBB", 18)}), market).get(OWNER)
    art = {h.symbol: h.image_url for h in p.holdings}
    assert art["AAA"] == "https://cdn.test/a.png" and art["BBB"] == "https://cdn.test/b.png" and art["CCC"] is None


async def test_art_learned_for_an_indexed_token_is_stored_for_next_time(db, redis):
    a = mk(1)
    db.add(Token(address=a, symbol="AAA", decimals=18, price_usd=1.0, liquidity_usd=1e9))
    await db.commit()
    market = Market({a: {"price_usd": 1.0, "liquidity_usd": 1e9, "image_url": "https://cdn.test/a.png"}})
    await PortfolioService(db, redis, held_rpc({a: 10**18}, []), market).get(OWNER)
    assert (await db.get(Token, 1)).image_url == "https://cdn.test/a.png"


async def test_art_without_a_dexscreener_image_comes_from_geckoterminal(db, redis):
    """Measured: DexScreener has no art for this chain (0 of the owner's 47 holdings, WETH included), while
    GeckoTerminal carries it for ~99 % of the catalogue. A held token with no art must ask the other source
    and remember the answer, instead of showing a generated mark forever."""
    a = mk(1)
    db.add(Token(address=a, symbol="AAA", decimals=18, price_usd=1.0, liquidity_usd=1e9))
    await db.commit()
    await check(db, 1)
    market = Market(art={a: "https://assets.geckoterminal.test/a.png"})
    p = await PortfolioService(db, redis, held_rpc({a: 10**18}, []), market).get(OWNER)
    assert p.holdings[0].image_url == "https://assets.geckoterminal.test/a.png"
    assert (await db.get(Token, 1)).image_url == "https://assets.geckoterminal.test/a.png"  # no second fetch


async def test_gecko_images_batches_filters_and_caches(redis):
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"data": [
            {"attributes": {"address": "0x" + "11" * 20, "image_url": "https://assets.gecko.test/a.png"}},
            {"attributes": {"address": "0x" + "22" * 20, "image_url": None}},
            {"attributes": {"address": "0x" + "33" * 20, "image_url": "http://insecure.test/x.png"}},
        ]})

    m = MarketClient(redis, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    got = await m.gecko_images(["0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20])
    assert got == {"0x" + "11" * 20: "https://assets.gecko.test/a.png"}  # http:// is not art we can render
    assert await m.gecko_images(["0x" + "11" * 20, "0x" + "22" * 20]) == {"0x" + "11" * 20: "https://assets.gecko.test/a.png"}
    assert len(calls) == 1  # the hit AND the miss are cached: a miss is re-asked later, not per page load
