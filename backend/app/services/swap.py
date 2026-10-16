"""In-app swap: quote + calldata, NEVER a signature. The user's own wallet signs and sends (non-custodial).

Quotes are read on-chain: v3 QuoterV2 and the v4 Quoter via eth_call. No route found = `NoRoute`; we never
invent a number. (A Uniswap Trading API key path is NOT implemented: it would have to be written against an API
nobody could call from the build environment. Quotes never reach the browser with a key in them either way.)

The pure parts (slippage, min received, price impact, calldata encoding) are unit-tested. The on-chain parts
need real addresses: they are configuration, verified with `python -m app.tools.verify_swap`.
"""
from dataclasses import dataclass, field

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

from app.core.config import Settings

NATIVE = "0x0000000000000000000000000000000000000000"
UINT128_MAX = 2**128 - 1
UINT160_MAX = 2**160 - 1
UINT48_MAX = 2**48 - 1
V3_FEES = (100, 500, 3000, 10000)

# Universal Router commands / constants
CMD_V3_SWAP_EXACT_IN = 0x00
CMD_PERMIT2_PERMIT = 0x0A
CMD_WRAP_ETH = 0x0B
CMD_UNWRAP_WETH = 0x0C
CMD_V4_SWAP = 0x10
MSG_SENDER = "0x0000000000000000000000000000000000000001"
ADDRESS_THIS = "0x0000000000000000000000000000000000000002"
# v4 router actions
ACT_SWAP_EXACT_IN_SINGLE = 0x06
ACT_SETTLE_ALL = 0x0C
ACT_SETTLE = 0x0B
ACT_TAKE_ALL = 0x0F
ACT_TAKE = 0x0E
OPEN_DELTA = 0


class SwapError(RuntimeError):
    pass


class NoRoute(SwapError):
    """No pool we can quote. The caller says so; it must not show a made-up price."""


# ---------- pure maths ----------

def clamp_slippage(bps: int | None, default: int, cap: int) -> int:
    if bps is None:
        return default
    if bps < 1:
        raise SwapError("Slippage must be at least 0.01%.")
    if bps > cap:
        raise SwapError(f"Slippage above {cap / 100:g}% is not allowed.")
    return bps


def min_received(amount_out: int, slippage_bps: int) -> int:
    """Worst acceptable output, rounded DOWN so the wallet never signs a floor above the quote."""
    return amount_out * (10_000 - slippage_bps) // 10_000


def to_raw(amount: str, decimals: int) -> int:
    """Decimal string -> integer base units, exactly (no float)."""
    from decimal import Decimal, InvalidOperation

    try:
        d = Decimal(amount.strip())
    except (InvalidOperation, AttributeError):
        raise SwapError("That amount doesn't look right.") from None
    if d <= 0 or d != d.quantize(Decimal(1).scaleb(-decimals)):
        raise SwapError("That amount doesn't look right.")
    return int(d.scaleb(decimals))


def price_impact_pct(value_in_usd: float | None, value_out_usd: float | None, fee_bps: int | None) -> float | None:
    """Shortfall of what you get against what you pay, minus the pool fee. None when a price is unknown."""
    if not value_in_usd or value_out_usd is None or fee_bps is None:
        return None
    return round(max(0.0, (1 - value_out_usd / value_in_usd) * 100 - fee_bps / 100), 2)


# ---------- calldata ----------

def v3_path(token_in: str, fee: int, token_out: str) -> bytes:
    return bytes.fromhex(token_in[2:]) + fee.to_bytes(3, "big") + bytes.fromhex(token_out[2:])


def _execute(commands: list[int], inputs: list[bytes], deadline: int) -> bytes:
    sel = keccak(text="execute(bytes,bytes[],uint256)")[:4]
    return sel + encode(["bytes", "bytes[]", "uint256"], [bytes(commands), inputs, deadline])


def permit2_approve_calldata(token: str, router: str, amount: int, expiry: int) -> bytes:
    """Permit2.approve(token, spender, uint160 amount, uint48 expiration)."""
    sel = keccak(text="approve(address,address,uint160,uint48)")[:4]
    return sel + encode(["address", "address", "uint160", "uint48"], [token, router, min(amount, UINT160_MAX), expiry])


def erc20_approve_calldata(spender: str, amount: int) -> bytes:
    return keccak(text="approve(address,uint256)")[:4] + encode(["address", "uint256"], [spender, amount])


@dataclass
class PoolKey:
    currency0: str
    currency1: str
    fee: int
    tick_spacing: int
    hooks: str

    def tuple(self):
        return (self.currency0, self.currency1, self.fee, self.tick_spacing, self.hooks)


@dataclass
class Route:
    kind: str  # "v3" | "v4"
    token_in: str
    token_out: str
    fee: int
    pool_key: PoolKey | None = None  # v4 only


@dataclass
class Plan:
    to: str
    data: str
    value: int
    notes: list[str] = field(default_factory=list)


V4_SWAP_PARAMS = "((address,address,uint24,int24,address),bool,uint128,uint128,bytes)"


def build_swap(route: Route, amount_in: int, min_out: int, deadline: int, *, eth_in: bool, eth_out: bool,
               router: str, weth: str) -> Plan:
    """Universal Router calldata. ERC-20 input is pulled through Permit2 (the user approved it beforehand);
    native ETH in is wrapped by the router, native ETH out is unwrapped to the sender. Recipient is always the
    sender: funds can only land in the user's wallet."""
    cmds: list[int] = []
    ins: list[bytes] = []
    value = 0
    if eth_in:
        cmds.append(CMD_WRAP_ETH)
        ins.append(encode(["address", "uint256"], [ADDRESS_THIS, amount_in]))
        value = amount_in
    if route.kind == "v3":
        cmds.append(CMD_V3_SWAP_EXACT_IN)
        recipient = ADDRESS_THIS if eth_out else MSG_SENDER
        ins.append(encode(["address", "uint256", "uint256", "bytes", "bool"],
                          [recipient, amount_in, 0 if eth_out else min_out,
                           v3_path(route.token_in, route.fee, route.token_out), not eth_in]))
    elif route.kind == "v4":
        key = route.pool_key
        if key is None:
            raise SwapError("v4 route without a pool key")
        zero_for_one = route.token_in.lower() == key.currency0.lower()
        swap = encode([V4_SWAP_PARAMS], [(key.tuple(), zero_for_one, amount_in, 0 if eth_out else min_out, b"")])
        actions = [ACT_SWAP_EXACT_IN_SINGLE]
        params = [swap]
        if eth_in:  # the router holds the wrapped ETH: settle from its own balance
            actions.append(ACT_SETTLE)
            params.append(encode(["address", "uint256", "bool"], [route.token_in, OPEN_DELTA, False]))
        else:
            actions.append(ACT_SETTLE_ALL)
            params.append(encode(["address", "uint256"], [route.token_in, amount_in]))
        if eth_out:
            actions.append(ACT_TAKE)
            params.append(encode(["address", "address", "uint256"], [route.token_out, ADDRESS_THIS, OPEN_DELTA]))
        else:
            actions.append(ACT_TAKE_ALL)
            params.append(encode(["address", "uint256"], [route.token_out, min_out]))
        cmds.append(CMD_V4_SWAP)
        ins.append(encode(["bytes", "bytes[]"], [bytes(actions), params]))
    else:
        raise SwapError(f"unknown route kind {route.kind}")
    if eth_out:
        cmds.append(CMD_UNWRAP_WETH)
        ins.append(encode(["address", "uint256"], [MSG_SENDER, min_out]))
    return Plan(to=router, data="0x" + _execute(cmds, ins, deadline).hex(), value=value)


# ---------- on-chain quoting ----------

def v3_quote_call(token_in: str, token_out: str, amount_in: int, fee: int) -> str:
    sel = keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4]
    return "0x" + (sel + encode(["(address,address,uint256,uint24,uint160)"], [(token_in, token_out, amount_in, fee, 0)])).hex()


def v4_quote_call(key: PoolKey, zero_for_one: bool, amount_in: int) -> str:
    sel = keccak(text="quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))")[:4]
    body = encode(["((address,address,uint24,int24,address),bool,uint128,bytes)"], [(key.tuple(), zero_for_one, amount_in, b"")])
    return "0x" + (sel + body).hex()


def decode_amount_out(result: str | None) -> int:
    if not result or result == "0x":
        raise NoRoute("empty quote")
    return decode(["uint256"], bytes.fromhex(result[2:])[:32])[0]


def parse_initialize_data(data: str) -> tuple[int, int, str]:
    """Initialize(bytes32 id, address c0, address c1, uint24 fee, int24 tickSpacing, address hooks, ...) data part."""
    fee, spacing, hooks, *_ = decode(["uint24", "int24", "address", "uint160", "int24"], bytes.fromhex(data[2:]))
    return fee, spacing, to_checksum_address(hooks)


def addresses_missing(s: Settings) -> list[str]:
    need = {"SWAP_UNIVERSAL_ROUTER": s.swap_universal_router, "SWAP_PERMIT2": s.swap_permit2,
            "SWAP_QUOTER_V2": s.swap_quoter_v2, "SWAP_V4_QUOTER": s.swap_v4_quoter}
    return [k for k, v in need.items() if not v]


# ---------- orchestration: discover a route and quote it ----------
import json  # noqa: E402
import time  # noqa: E402

from app.services.chain_index import INITIALIZE_TOPIC, POOL_MANAGER  # noqa: E402

SEL_ALLOWANCE = keccak(text="allowance(address,address)")[:4]
SEL_P2_ALLOWANCE = keccak(text="allowance(address,address,address)")[:4]
APPROVAL_TTL_S = 30 * 86400
DEADLINE_S = 20 * 60


def _addr(a: str) -> str:
    return to_checksum_address(a)


async def v4_pool_key(rpc, redis, pool_id: str, token: str, quote: str) -> PoolKey | None:
    """The PoolKey (fee, tick spacing, hooks) of a v4 pool, read from its Initialize event; cached forever."""
    ck = f"swap:poolkey:{pool_id}"
    if cached := await redis.get(ck):
        d = json.loads(cached)
        return PoolKey(*d)
    logs = await rpc.call("eth_getLogs", [{"address": POOL_MANAGER, "topics": [INITIALIZE_TOPIC, pool_id],
                                           "fromBlock": "0x0", "toBlock": "latest"}])
    if not logs:
        return None
    lg = logs[0]
    fee, spacing, hooks = parse_initialize_data(lg["data"])
    c0, c1 = sorted([_addr(token), _addr(quote)], key=lambda a: int(a, 16))
    key = PoolKey(c0, c1, fee, spacing, hooks)
    await redis.set(ck, json.dumps(list(key.tuple())))
    return key


async def find_route(rpc, redis, s: Settings, token: str, quote_asset: str, amount_in: int, selling: bool,
                     pool_id: str | None) -> tuple[Route, int]:
    """Best (route, amount_out) among v3 fee tiers and the v4 pool, in ONE batch of eth_calls."""
    t_in, t_out = (_addr(token), _addr(quote_asset)) if selling else (_addr(quote_asset), _addr(token))
    cands: list[tuple[Route, str, str]] = []  # (route, quoter, calldata)
    if s.swap_quoter_v2:
        for fee in V3_FEES:
            cands.append((Route("v3", t_in, t_out, fee), s.swap_quoter_v2, v3_quote_call(t_in, t_out, amount_in, fee)))
    if s.swap_v4_quoter and pool_id:
        key = await v4_pool_key(rpc, redis, pool_id, token, quote_asset)
        if key:
            zfo = t_in.lower() == key.currency0.lower()
            cands.append((Route("v4", t_in, t_out, key.fee, key), s.swap_v4_quoter, v4_quote_call(key, zfo, amount_in)))
    if not cands:
        raise NoRoute("no quoter configured or no pool known")
    res = await rpc.batch([("eth_call", [{"to": q, "data": d}, "latest"]) for _, q, d in cands])
    best: tuple[Route, int] | None = None
    for (route, _, _), r in zip(cands, res):
        try:
            out = decode_amount_out(r)
        except Exception:  # noqa: BLE001 — a tier without a pool reverts: that is "no pool", not an error
            continue
        if out > 0 and (best is None or out > best[1]):
            best = (route, out)
    if best is None:
        raise NoRoute("no pool could fill this order")
    return best


async def approval_steps(rpc, s: Settings, owner: str, token: str, amount_in: int) -> list[dict]:
    """ERC-20 -> Permit2, then Permit2 -> router. Only the steps that are actually missing."""
    p2, router = _addr(s.swap_permit2), _addr(s.swap_universal_router)
    a1, a2 = await rpc.batch([
        ("eth_call", [{"to": token, "data": "0x" + (SEL_ALLOWANCE + encode(["address", "address"], [owner, p2])).hex()}, "latest"]),
        ("eth_call", [{"to": p2, "data": "0x" + (SEL_P2_ALLOWANCE + encode(["address", "address", "address"], [owner, token, router])).hex()}, "latest"]),
    ])
    steps = []
    if decode_amount_out(a1) < amount_in:
        steps.append({"kind": "approve_token", "label": "Allow Permit2 to move this token (one time)",
                      "tx": {"to": token, "data": "0x" + erc20_approve_calldata(p2, 2**256 - 1).hex(), "value": "0x0"}})
    amt, exp, _nonce = decode(["uint160", "uint48", "uint48"], bytes.fromhex(a2[2:])) if a2 and a2 != "0x" else (0, 0, 0)
    if amt < amount_in or exp <= int(time.time()) + 60:
        steps.append({"kind": "approve_router", "label": "Allow the swap router for 30 days",
                      "tx": {"to": p2, "data": "0x" + permit2_approve_calldata(token, router, UINT160_MAX, int(time.time()) + APPROVAL_TTL_S).hex(),
                             "value": "0x0"}})
    return steps


@dataclass
class QuoteRequest:
    token: str
    side: str  # buy | sell
    amount: str  # human units of what the user pays
    slippage_bps: int | None
    wallet: str
    pay_with: str = "ETH"  # buy: ETH | USDG; sell: receive ETH | USDG


async def quote(rpc, redis, s: Settings, req: QuoteRequest, meta: dict, market: dict | None, eth_usd: float | None) -> dict:
    """meta: {symbol, decimals} of the token. market: its DexScreener facts (price, pool id). Returns the sheet payload."""
    if not s.swap_enabled:
        raise SwapError("In-app swap isn't switched on yet.")
    if addresses_missing(s):
        raise SwapError("Swap isn't configured on this server yet.")
    slip = clamp_slippage(req.slippage_bps, s.swap_default_slippage_bps, s.swap_max_slippage_bps)
    selling, token = req.side == "sell", _addr(req.token)
    eth_leg = req.pay_with.upper() == "ETH"
    quote_asset = _addr(s.swap_weth if eth_leg else s.swap_usdg)
    q_dec = 18 if eth_leg else 6
    in_dec = meta["decimals"] if selling else (18 if eth_leg else 6)
    amount_in = to_raw(req.amount, in_dec)
    pool_id = (market or {}).get("pool_id")
    route, amount_out = await find_route(rpc, redis, s, token, quote_asset, amount_in, selling, pool_id)
    out_dec = q_dec if selling else meta["decimals"]
    min_out = min_received(amount_out, slip)
    deadline = int(time.time()) + DEADLINE_S
    plan = build_swap(route, amount_in, min_out, deadline, eth_in=eth_leg and not selling, eth_out=eth_leg and selling,
                      router=_addr(s.swap_universal_router), weth=_addr(s.swap_weth))
    steps = []
    if not (eth_leg and not selling):  # ERC-20 input goes through Permit2
        steps = await approval_steps(rpc, s, _addr(req.wallet), route.token_in, amount_in)
    steps.append({"kind": "swap", "label": "Confirm the swap",
                  "tx": {"to": plan.to, "data": plan.data, "value": hex(plan.value)}})

    px_token = (market or {}).get("price_usd")
    px_q = eth_usd if eth_leg else 1.0
    px_in, px_out = (px_token, px_q) if selling else (px_q, px_token)
    v_in = amount_in / 10 ** in_dec * px_in if px_in else None
    v_out = amount_out / 10 ** out_dec * px_out if px_out else None
    return {
        "source": "onchain", "route": route.kind, "side": req.side, "token": token.lower(), "pay_with": "ETH" if eth_leg else "USDG",
        "amount_in_raw": str(amount_in), "amount_out_raw": str(amount_out), "min_out_raw": str(min_out),
        "decimals_in": in_dec, "decimals_out": out_dec, "slippage_bps": slip, "fee_bps": route.fee // 100,
        "price_impact_pct": price_impact_pct(v_in, v_out, route.fee // 100),
        "deadline": deadline, "steps": steps, "chain_id": s.chain_id,
    }
