import pytest
from eth_abi import decode
from eth_utils import keccak

from app.core.config import get_settings
from app.services import swap as sw

TOKEN = "0x" + "11" * 20
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
ROUTER = "0x" + "99" * 20
OWNER = "0x" + "22" * 20


def test_min_received_rounds_down_and_slippage_is_capped():
    assert sw.min_received(1_000_000, 100) == 990_000
    assert sw.min_received(999, 50) == 994  # 999 * 0.995 = 994.005 -> floor
    assert sw.clamp_slippage(None, 100, 1000) == 100
    with pytest.raises(sw.SwapError):
        sw.clamp_slippage(5000, 100, 1000)
    with pytest.raises(sw.SwapError):
        sw.clamp_slippage(0, 100, 1000)


def test_to_raw_is_exact_and_rejects_nonsense():
    assert sw.to_raw("0.05", 18) == 5 * 10**16
    assert sw.to_raw("1", 6) == 10**6
    for bad in ("0", "-1", "abc", "1.1234567", ""):
        with pytest.raises(sw.SwapError):
            sw.to_raw(bad, 6)


def test_price_impact_is_shortfall_minus_fee_and_unknown_without_prices():
    assert sw.price_impact_pct(100, 99.0, 30) == 0.7  # 1% short, 0.30% of it is the pool fee
    assert sw.price_impact_pct(100, 99.9, 30) == 0.0  # never negative
    assert sw.price_impact_pct(None, 5, 30) is None and sw.price_impact_pct(100, None, 30) is None


def _decode_execute(data: str):
    raw = bytes.fromhex(data[2:])
    assert raw[:4] == keccak(text="execute(bytes,bytes[],uint256)")[:4]
    return decode(["bytes", "bytes[]", "uint256"], raw[4:])


def test_v3_buy_with_eth_wraps_then_swaps_to_the_sender():
    route = sw.Route("v3", WETH, TOKEN.replace("0x", "0x"), 3000)
    plan = sw.build_swap(route, 10**17, 5, 1234, eth_in=True, eth_out=False, router=ROUTER, weth=WETH)
    cmds, inputs, deadline = _decode_execute(plan.data)
    assert cmds == bytes([sw.CMD_WRAP_ETH, sw.CMD_V3_SWAP_EXACT_IN]) and deadline == 1234
    assert plan.value == 10**17 and plan.to == ROUTER
    assert decode(["address", "uint256"], inputs[0]) == (sw.ADDRESS_THIS, 10**17)
    recipient, amt, mn, path, payer_is_user = decode(["address", "uint256", "uint256", "bytes", "bool"], inputs[1])
    assert (recipient, amt, mn, payer_is_user) == (sw.MSG_SENDER, 10**17, 5, False)  # router pays from its wrapped ETH
    assert len(path) == 43 and int.from_bytes(path[20:23], "big") == 3000


def test_v3_sell_for_eth_unwraps_with_the_min_out_floor():
    route = sw.Route("v3", TOKEN, WETH, 500)
    plan = sw.build_swap(route, 777, 9, 1, eth_in=False, eth_out=True, router=ROUTER, weth=WETH)
    cmds, inputs, _ = _decode_execute(plan.data)
    assert cmds == bytes([sw.CMD_V3_SWAP_EXACT_IN, sw.CMD_UNWRAP_WETH]) and plan.value == 0
    assert decode(["address", "uint256", "uint256", "bytes", "bool"], inputs[0])[0] == sw.ADDRESS_THIS
    assert decode(["address", "uint256"], inputs[1]) == (sw.MSG_SENDER, 9)  # the floor is enforced on the unwrap


def test_v4_sell_pulls_through_permit2_and_takes_to_the_sender():
    c0, c1 = sorted([sw._addr(TOKEN), sw._addr(USDG)], key=lambda a: int(a, 16))
    key = sw.PoolKey(c0, c1, 3000, 60, "0x" + "00" * 20)
    route = sw.Route("v4", sw._addr(TOKEN), sw._addr(USDG), 3000, key)
    plan = sw.build_swap(route, 1000, 900, 5, eth_in=False, eth_out=False, router=ROUTER, weth=WETH)
    cmds, inputs, _ = _decode_execute(plan.data)
    assert cmds == bytes([sw.CMD_V4_SWAP])
    actions, params = decode(["bytes", "bytes[]"], inputs[0])
    assert actions == bytes([sw.ACT_SWAP_EXACT_IN_SINGLE, sw.ACT_SETTLE_ALL, sw.ACT_TAKE_ALL])
    (k, zfo, amt, mn, hook), = decode([sw.V4_SWAP_PARAMS], params[0])
    assert (tuple(x.lower() if isinstance(x, str) else x for x in k), amt, mn, hook) == (
        tuple(x.lower() if isinstance(x, str) else x for x in key.tuple()), 1000, 900, b"")
    assert zfo == (sw._addr(TOKEN) == c0)
    assert decode(["address", "uint256"], params[1])[1] == 1000  # settle at most what we sell
    assert decode(["address", "uint256"], params[2])[1] == 900  # take at least the floor


def test_quote_decoding_and_no_route_never_becomes_a_number():
    assert sw.decode_amount_out("0x" + (123).to_bytes(32, "big").hex() + "00" * 96) == 123
    for empty in (None, "0x", ""):
        with pytest.raises(sw.NoRoute):
            sw.decode_amount_out(empty)


class FakeRpc:
    def __init__(self, outs):
        self.outs, self.calls = outs, []

    async def batch(self, calls):
        self.calls.append(len(calls))
        return self.outs[: len(calls)]

    async def call(self, *a):
        raise AssertionError("no logs expected")


async def test_best_v3_tier_wins_and_reverting_tiers_are_skipped(redis, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "swap_quoter_v2", "0x" + "55" * 20)
    out = lambda n: "0x" + n.to_bytes(32, "big").hex() + "00" * 96  # noqa: E731
    rpc = FakeRpc([None, out(500), out(900), None])  # tiers 100 and 10000 revert
    route, amount = await sw.find_route(rpc, redis, s, TOKEN, WETH, 10**17, False, None)
    assert (route.kind, route.fee, amount) == ("v3", 3000, 900) and rpc.calls == [4]  # one batch, not 4 requests
    with pytest.raises(sw.NoRoute):
        await sw.find_route(FakeRpc([None] * 4), redis, s, TOKEN, WETH, 1, False, None)


async def test_swap_is_off_by_default_and_says_so(redis):
    s = get_settings()
    assert s.swap_enabled is False
    with pytest.raises(sw.SwapError, match="switched on"):
        await sw.quote(FakeRpc([]), redis, s, sw.QuoteRequest(TOKEN, "buy", "0.1", None, OWNER), {"decimals": 18}, {}, 3000.0)


async def test_api_config_never_exposes_more_than_public_addresses_and_quote_refuses_when_off(client):
    cfg = (await client.get("/swap/config")).json()
    assert cfg["enabled"] is False and set(cfg) == {"enabled", "chain_id", "default_slippage_bps", "max_slippage_bps"}
    r = await client.post("/swap/quote", json={"token": TOKEN, "side": "buy", "amount": "0.1", "wallet": OWNER})
    assert r.status_code in (400, 502)  # off: a plain refusal, never a number
    assert "amount_out_raw" not in r.text
