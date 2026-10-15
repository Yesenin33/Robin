from app.services import chain_index, smart_wallets
from app.services.chain_index import Trade
from app.services.intelligence.rpc import Rpc
from tests.chainmock import E18, T0, V4Chain

TOK = "0x" + "70" * 20
POOL = "0x" + "ab" * 32
A, B, C = "0x" + "a1" * 20, "0x" + "b2" * 20, "0x" + "c3" * 20


def chain() -> tuple[V4Chain, Rpc]:
    c = V4Chain(head=1000)
    c.token(TOK, "HOODX")
    c.pool(100, TOK, POOL)
    return c, Rpc("mock", c)


def test_new_pools_from_initialize():
    c, rpc = chain()
    pools = chain_index.new_pools(rpc, 0, 1000, 9999)
    assert len(pools) == 1 and pools[0].pool_id == POOL and pools[0].currency1 == TOK and pools[0].block == 100


def test_wallet_trades_any_token_with_eth_legs_and_time():
    c, rpc = chain()
    c.swap(150, A, TOK, "buy", 1000 * E18, 0.5, POOL)
    c.swap(160, B, TOK, "buy", 10 * E18, 0.01, POOL)  # not followed
    c.swap(170, A, TOK, "sell", 400 * E18, 0.3, POOL)
    trades = chain_index.enrich(rpc, chain_index.wallet_trades(rpc, [A], 0, 1000, 9999))
    assert [(t.side, t.token_raw // E18, t.eth_wei / E18) for t in trades] == [("buy", 1000, 0.5), ("sell", 400, 0.3)]
    assert all(t.wallet == A and t.token == TOK and t.ts == T0 + t.block for t in trades)


def test_eth_leg_unknown_when_counter_currency_is_another_erc20():
    usdc = "0x" + "05" * 20
    t = Trade(A, TOK, "buy", 100, "0x1", 1, 1)
    receipt = {"logs": [
        {"address": usdc, "topics": [chain_index.TRANSFER_TOPIC, "0x" + "0" * 24 + A[2:], "0x" + "0" * 24 + chain_index.POOL_MANAGER[2:]], "data": "0x1"},
    ]}
    assert chain_index.eth_leg(receipt, t) is None


def test_rank_realised_pnl_and_early_buys():
    c, rpc = chain()
    # A: early buyer, two profitable round trips on two tokens
    tok2, pool2 = "0x" + "71" * 20, "0x" + "cd" * 32
    c.token(tok2, "PLUM")
    c.swap(110, A, TOK, "buy", 1000 * E18, 1.0, POOL)
    c.swap(300, A, TOK, "sell", 1000 * E18, 3.0, POOL)
    c.swap(400, A, tok2, "buy", 500 * E18, 1.0, pool2)
    c.swap(500, A, tok2, "sell", 250 * E18, 1.0, pool2)  # sold half for 1.0 -> +0.5
    # B: one loss, one tiny win
    c.swap(600, B, TOK, "buy", 100 * E18, 1.0, POOL)
    c.swap(700, B, TOK, "sell", 100 * E18, 0.2, POOL)
    c.swap(710, B, tok2, "buy", 10 * E18, 0.1, pool2)
    c.swap(720, B, tok2, "sell", 10 * E18, 0.11, pool2)
    # C: only buys -> nothing closed, never suggested
    c.swap(800, C, TOK, "buy", 1 * E18, 0.1, POOL)
    trades = []
    for tok in (TOK, tok2):
        trades += chain_index.enrich(rpc, chain_index.token_trades(rpc, tok, 0, 1000, 9999, 300))
    ranked = smart_wallets.rank(trades, {TOK: 100}, min_closed=2)
    assert [w.address for w in ranked] == [A]  # B is net negative, C has no closed trades
    a = ranked[0]
    assert (a.wins, a.losses, a.early_buys, len(a.tokens)) == (2, 0, 1, 2)
    assert round(a.realised_pnl_eth, 6) == 2.5
    assert smart_wallets.describe(a) == ("Made +2.50 ETH in realised profit; 2 of 2 closed trades were wins across "
                                         "2 tokens. Bought early in 1 new token.")


def test_rank_excludes_contracts():
    c, rpc = chain()
    c.swap(110, A, TOK, "buy", 10 * E18, 1.0, POOL)
    c.swap(120, A, TOK, "sell", 5 * E18, 1.0, POOL)
    c.swap(130, A, TOK, "sell", 5 * E18, 1.0, POOL)
    trades = chain_index.enrich(rpc, chain_index.token_trades(rpc, TOK, 0, 1000, 9999, 300))
    assert smart_wallets.rank(trades, min_closed=1, exclude={A}) == []
