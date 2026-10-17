from app.services import chain_index, smart_wallets
from app.services.chain_index import Trade
from app.services.intelligence.rpc import ZERO, Rpc
from app.services.pricing import value_trades
from tests.chainmock import E18, T0, V4Chain

TOK = "0x" + "70" * 20
TOK2 = "0x" + "71" * 20
EQUITY = "0x" + "e1" * 20  # tokenized stock used as a quote: not valued
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
P_USDG, P_WETH, P_ETH, P_EQ = "0x" + "ab" * 32, "0x" + "cd" * 32, "0x" + "ef" * 32, "0x" + "12" * 32
A, B, C = "0x" + "a1" * 20, "0x" + "b2" * 20, "0x" + "c3" * 20
KINDS = {ZERO: "ETH", WETH: "ETH", USDG: "USD"}
DEC = {ZERO: 18, WETH: 18, USDG: 6}


def chain() -> tuple[V4Chain, Rpc]:
    c = V4Chain(head=1000)
    c.token(TOK, "HOODX")
    c.token(TOK2, "PLUM")
    c.token(USDG, "USDG", decimals=6)
    c.token(WETH, "WETH")
    c.token(EQUITY, "SPCX")
    c.pool(100, TOK, P_USDG, quote=USDG)
    c.pool(100, TOK2, P_WETH, quote=WETH)
    c.pool(100, TOK2, P_ETH)  # native ETH pool
    c.pool(100, TOK, P_EQ, quote=EQUITY)
    return c, Rpc("mock", c)


def test_new_pools_from_initialize_sorted_currencies():
    _, rpc = chain()
    pools = {p.pool_id: p for p in chain_index.new_pools(rpc, 0, 1000, 9999)}
    assert (pools[P_USDG].currency0, pools[P_USDG].currency1) == (USDG, TOK)  # 0x5f.. < 0x70..
    assert (pools[P_ETH].currency0, pools[P_ETH].currency1) == (ZERO, TOK2)


def test_quote_leg_per_currency_and_usd_value():
    c, rpc = chain()
    c.swap(150, A, TOK, "buy", 1000 * E18, 2500.0, P_USDG)  # paid 2,500 USDG
    c.swap(160, A, TOK2, "sell", 10 * E18, 0.5, P_WETH)  # got 0.5 WETH
    c.swap(170, A, TOK2, "buy", 10 * E18, 0.25, P_ETH)  # paid 0.25 native ETH
    c.swap(180, A, TOK, "buy", 5 * E18, 3.0, P_EQ)  # paid in a tokenized stock
    trades = chain_index.wallet_trades(rpc, [A], 0, 1000, 9999)
    pools: dict = {}
    chain_index.enrich(rpc, trades, WETH, pools)  # pools resolved on-chain from Initialize
    assert set(pools) == {P_USDG, P_WETH, P_ETH, P_EQ}
    value_trades(trades, KINDS, DEC, eth_price=3000.0)
    got = [(t.side, t.quote, t.usd) for t in trades]
    assert got == [("buy", USDG, 2500.0), ("sell", WETH, 1500.0), ("buy", ZERO, 750.0), ("buy", EQUITY, None)]
    assert trades[1].eth_wei == int(0.5 * E18) and trades[0].eth_wei is None
    assert all(t.ts == T0 + t.block for t in trades)


def test_quote_leg_without_pool_map_falls_back_to_transfers():
    c, rpc = chain()
    tx = c.swap(150, A, TOK, "buy", 1000 * E18, 2500.0, P_USDG)
    t = chain_index.wallet_trades(rpc, [A], 0, 1000, 9999)[0]
    assert chain_index.quote_leg({"logs": c.receipts[tx]}, t, pools={}) == (USDG, 2_500_000_000)


def test_rank_in_usd_skips_unvalued_and_counts_early_buys():
    c, rpc = chain()
    # A: early buyer, two profitable round trips (USDG pool and WETH pool)
    c.swap(110, A, TOK, "buy", 1000 * E18, 1000.0, P_USDG)
    c.swap(300, A, TOK, "sell", 1000 * E18, 3000.0, P_USDG)  # +$2,000
    c.swap(400, A, TOK2, "buy", 500 * E18, 1.0, P_WETH)  # $3,000
    c.swap(500, A, TOK2, "sell", 250 * E18, 1.0, P_WETH)  # sold half for $3,000 -> +$1,500
    # B: net loser
    c.swap(600, B, TOK, "buy", 100 * E18, 1000.0, P_USDG)
    c.swap(700, B, TOK, "sell", 100 * E18, 200.0, P_USDG)
    c.swap(710, B, TOK2, "buy", 10 * E18, 0.1, P_WETH)
    c.swap(720, B, TOK2, "sell", 10 * E18, 0.11, P_WETH)
    # C: profitable, but only in a tokenized-stock pool -> can't be valued -> not ranked
    c.swap(800, C, TOK, "buy", 1 * E18, 1.0, P_EQ)
    c.swap(810, C, TOK, "sell", 1 * E18, 9.0, P_EQ)
    trades = []
    for tok in (TOK, TOK2):
        trades += chain_index.enrich(rpc, chain_index.token_trades(rpc, tok, 0, 1000, 9999, 300), WETH, {})
    value_trades(trades, KINDS, DEC, eth_price=3000.0)
    ranked = smart_wallets.rank(trades, {TOK: 100}, min_closed=2)
    assert [w.address for w in ranked] == [A]
    a = ranked[0]
    assert (a.wins, a.losses, a.early_buys, len(a.tokens)) == (2, 0, 1, 2)
    assert round(a.realised_pnl_usd, 6) == 3500.0 and round(a.invested_usd, 6) == 4000.0
    assert smart_wallets.describe(a) == ("Made +$3,500.00 in realised profit; 2 of 2 closed trades were wins across "
                                         "2 tokens. Bought early in 1 new token.")


def test_rank_excludes_contracts():
    c, rpc = chain()
    c.swap(110, A, TOK, "buy", 10 * E18, 10.0, P_USDG)
    c.swap(120, A, TOK, "sell", 5 * E18, 10.0, P_USDG)
    trades = chain_index.enrich(rpc, chain_index.token_trades(rpc, TOK, 0, 1000, 9999, 300), WETH, {})
    value_trades(trades, KINDS, DEC, None)
    assert smart_wallets.rank(trades, min_closed=1) and smart_wallets.rank(trades, min_closed=1, exclude={A}) == []


def test_trade_without_usd_is_not_valued_when_eth_price_unknown():
    t = Trade(A, TOK, "buy", 1, "0x1", 1, 1, quote=WETH, quote_raw=E18)
    value_trades([t], KINDS, DEC, eth_price=None)
    assert t.usd is None
