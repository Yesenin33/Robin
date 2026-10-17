from app.services.intelligence import canonical, trading
from app.services.intelligence.config import get_chain
from app.services.intelligence.holders import Transfer
from app.services.intelligence.scoring import assess
from tests.test_intelligence import FULL

CFG = get_chain("robinhood")
WETH = CFG.wrapped_native
FAKE = "0x" + "ee" * 20
POOL, ROUTER, USER, POOL2 = "0x" + "a1" * 20, "0x" + "a2" * 20, "0x" + "a3" * 20, "0x" + "a4" * 20


class Node:
    def __init__(self, balance):
        self._b = balance

    def balance(self, addr):
        return self._b


META = {"total_supply_raw": 1000, "symbol": "WETH"}
SEL = {canonical.DEPOSIT, canonical.WITHDRAW}


def test_a_backed_wrapper_at_the_configured_address_is_verified():
    assert canonical.verify(Node(1000), CFG, WETH, META, SEL) == canonical.WRAPPED


def test_unbacked_wrong_address_or_no_wrapper_code_is_not_verified():
    assert canonical.verify(Node(999), CFG, WETH, META, SEL) is None  # supply exceeds the ether it holds
    assert canonical.verify(Node(10**30), CFG, FAKE, META, SEL) is None  # a "WETH" somewhere else
    assert canonical.verify(Node(10**30), CFG, WETH, META, {"a9059cbb"}) is None  # no deposit/withdraw
    assert canonical.verify(Node(10**30), CFG, WETH, META, None) is None


def test_stable_needs_its_address_and_a_readable_supply():
    meta = {"total_supply_raw": 5, "symbol": "USDG"}
    assert canonical.verify(Node(0), CFG, CFG.stable, meta, set()) == canonical.STABLE
    assert canonical.verify(Node(0), CFG, CFG.stable, {**meta, "total_supply_raw": None}, set()) is None
    assert canonical.verify(Node(0), CFG, FAKE, meta, set()) is None


FALSE_POSITIVES = dict(buy_tax=100.0, sell_tax=0.0, proxy=True, owner_renounced=False, can_mint=False, honeypot=False,
                       top_holder_pct=43.0, fdv=2_000_000, liquidity_usd=900_000, has_site=False, has_twitter=False)


def test_verified_wrapper_is_not_rated_danger_for_its_design():
    r = {**FULL, **FALSE_POSITIVES, "sources": [canonical.WRAPPED]}
    a = assess(r)
    assert a.verdict == "CLEAN" and a.risks == []


def test_the_same_signals_on_an_unverified_token_stay_dangerous():
    """A fake "USDT"/"ETH" must keep its findings: only verification buys the exemption."""
    a = assess({**FULL, **FALSE_POSITIVES, "sources": []})
    assert a.verdict == "DANGER" and "Buying costs a 100% fee." in a.risks
    marked = assess({**FULL, **FALSE_POSITIVES, "sources": ["verified:wrapped-native-lookalike"]})
    assert marked.verdict == "DANGER"  # an unknown marker is not an exemption


def test_router_hops_through_a_pool_are_not_read_as_a_transfer_fee():
    """Aggregated routes move the same WETH pool -> router -> next pool in one tx: the old reader called that 100%."""
    ts = [Transfer(5, "0xfirst", POOL, USER, 100), Transfer(6, "0xfirst2", USER, POOL, 40)]  # sets first_buy, one sell
    buy_hop = [Transfer(10 + i, f"0xbuy{i}", POOL, ROUTER, 1000) for i in range(3)] + \
              [Transfer(10 + i, f"0xbuy{i}", ROUTER, POOL2, 1000) for i in range(3)]
    sell_hop = [Transfer(20 + i, f"0xsell{i}", ROUTER, POOL, 500) for i in range(3)] + \
               [Transfer(20 + i, f"0xsell{i}", ROUTER, POOL2, 700) for i in range(3)]
    out = trading.analyze(ts + buy_hop + sell_hop, POOL, WETH)
    assert out["buy_tax"] in (None, 0.0) and out["sell_tax"] in (None, 0.0)


def test_a_real_transfer_fee_is_still_read():
    token = "0x" + "77" * 20
    fee_wallet = "0x" + "88" * 20
    ts = [Transfer(5, "0xinit", POOL, USER, 1)]
    for i in range(12):  # a 10% fee leg taken out of every buy
        ts += [Transfer(10 + i, f"0xb{i}", POOL, "0x" + f"{i + 1:040x}", 900), Transfer(10 + i, f"0xb{i}", POOL, fee_wallet, 100)]
    out = trading.analyze(ts, POOL, token)
    assert out["buy_tax"] == 10.0
