"""Robinhood Chain (Uniswap V4) indexing primitives — sync, stdlib RPC from the intelligence package.

Every V4 swap settles through the PoolManager, so for any ERC-20:
  buy  = Transfer(PoolManager -> wallet)     sell = Transfer(wallet -> PoolManager)
The quote leg (what the trade was paid in) comes from the PoolManager `Swap` event in the receipt.
The pool id in that event names both currencies (from `Initialize`), so the side that is not our
token is the quote — USDG, WETH, native ETH or anything else. Unknown pools are resolved from their
Initialize log; failing that, the quote is inferred from which other ERC-20 moved through the
PoolManager in the same tx (none = native ETH). Valuing the quote in USD is pricing.py's job.
"""
from dataclasses import dataclass

from app.services.intelligence.rpc import TRANSFER_TOPIC, ZERO, Rpc, RpcError, pad_addr, to_int, topic_addr

POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
# keccak("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
INITIALIZE_TOPIC = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
# keccak("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
SWAP_TOPIC = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
MATCH_TOLERANCE = 0.05  # hook / transfer fees make the swap delta differ slightly from the transfer


@dataclass
class Pool:
    pool_id: str
    currency0: str
    currency1: str
    block: int


@dataclass
class Trade:
    wallet: str
    token: str
    side: str  # buy | sell
    token_raw: int
    tx: str
    block: int
    log_index: int
    eth_wei: int | None = None  # quote amount when the quote is ETH (native or WETH)
    ts: int | None = None  # unix seconds
    quote: str | None = None  # quote currency address (ZERO = native ETH); None = unresolved
    quote_raw: int | None = None
    usd: float | None = None  # trade value in USD (set by pricing.value_trades); None = can't value


def _word(data: str, i: int) -> int:
    return int(data[2 + 64 * i : 2 + 64 * (i + 1)], 16)


def _int128(v: int) -> int:
    v &= (1 << 256) - 1
    return v - (1 << 256) if v >> 255 else v


def get_logs(rpc: Rpc, flt: dict, start: int, end: int, chunk: int) -> list[dict]:
    """eth_getLogs over [start, end] in chunks; halves the chunk on range errors."""
    out: list[dict] = []
    while start <= end:
        stop = min(start + chunk - 1, end)
        try:
            out += rpc.call("eth_getLogs", [{**flt, "fromBlock": hex(start), "toBlock": hex(stop)}]) or []
            start = stop + 1
        except RpcError:
            if chunk <= 100:
                raise
            chunk //= 2
    return out


# ---------- pools ----------

def parse_initialize(log: dict) -> Pool:
    t = log["topics"]
    return Pool(pool_id=t[1].lower(), currency0=topic_addr(t[2]), currency1=topic_addr(t[3]), block=to_int(log["blockNumber"]))


def new_pools(rpc: Rpc, start: int, end: int, chunk: int) -> list[Pool]:
    logs = get_logs(rpc, {"address": POOL_MANAGER, "topics": [INITIALIZE_TOPIC]}, start, end, chunk)
    return [parse_initialize(lg) for lg in logs if len(lg.get("topics") or []) == 4]


# ---------- trades ----------

def _to_trade(lg: dict, wallets: set[str] | None) -> Trade | None:
    t = lg.get("topics") or []
    if len(t) != 3:
        return None
    frm, to = topic_addr(t[1]), topic_addr(t[2])
    if frm == POOL_MANAGER and to != POOL_MANAGER:
        side, wallet = "buy", to
    elif to == POOL_MANAGER and frm != POOL_MANAGER:
        side, wallet = "sell", frm
    else:
        return None
    if wallet == ZERO or (wallets is not None and wallet not in wallets):
        return None
    return Trade(wallet=wallet, token=lg["address"].lower(), side=side, token_raw=to_int(lg.get("data")),
                 tx=lg["transactionHash"], block=to_int(lg["blockNumber"]), log_index=to_int(lg.get("logIndex")))


def wallet_trades(rpc: Rpc, wallets: list[str], start: int, end: int, chunk: int) -> list[Trade]:
    """Every swap (any token) of the given wallets: two topic-filtered getLogs, no token list needed."""
    ws = {w.lower() for w in wallets}
    padded = ["0x" + pad_addr(w) for w in ws]
    pm = "0x" + pad_addr(POOL_MANAGER)
    logs = get_logs(rpc, {"topics": [TRANSFER_TOPIC, pm, padded]}, start, end, chunk)
    logs += get_logs(rpc, {"topics": [TRANSFER_TOPIC, padded, pm]}, start, end, chunk)
    trades = [tr for lg in logs if (tr := _to_trade(lg, ws))]
    return sorted(trades, key=lambda x: (x.block, x.log_index))


def token_trades(rpc: Rpc, token: str, start: int, end: int, chunk: int, limit: int) -> list[Trade]:
    """All swaps of one token (most recent `limit`)."""
    pm = "0x" + pad_addr(POOL_MANAGER)
    logs = get_logs(rpc, {"address": token, "topics": [TRANSFER_TOPIC, pm]}, start, end, chunk)
    logs += get_logs(rpc, {"address": token, "topics": [TRANSFER_TOPIC, None, pm]}, start, end, chunk)
    trades = sorted((tr for lg in logs if (tr := _to_trade(lg, None))), key=lambda x: (x.block, x.log_index))
    return trades[-limit:]


def _swaps(receipt: dict) -> list[tuple[str, int, int]]:
    out = []
    for lg in receipt.get("logs") or []:
        t = lg.get("topics") or []
        if lg.get("address", "").lower() == POOL_MANAGER and t and t[0] == SWAP_TOPIC and len(t) > 1:
            out.append((t[1].lower(), abs(_int128(_word(lg["data"], 0))), abs(_int128(_word(lg["data"], 1)))))
    return out


def _close(a: int, b: int) -> float | None:
    if not a or not b:
        return None
    d = abs(a - b) / max(a, b)
    return d if d <= MATCH_TOLERANCE else None


def quote_leg(receipt: dict, trade: Trade, pools: dict[str, tuple[str, str]]) -> tuple[str, int] | None:
    """(quote currency, raw amount) paid/received for this trade, or None if it can't be resolved."""
    best: tuple[float, str, int] | None = None
    unknown: list[tuple[int, int]] = []
    for pid, a0, a1 in _swaps(receipt):
        pair = pools.get(pid)
        if pair is None:
            unknown.append((a0, a1))
            continue
        c0, c1 = pair
        for cur, tok_amt, other, q_amt in ((c0, a0, c1, a1), (c1, a1, c0, a0)):
            if cur == trade.token and (d := _close(tok_amt, trade.token_raw)) is not None:
                if best is None or d < best[0]:
                    best = (d, other, q_amt)
    if best:
        return best[1], best[2]
    # pool not known: infer the quote from the other ERC-20s that crossed the PoolManager
    moved: dict[str, list[int]] = {}
    for lg in receipt.get("logs") or []:
        t = lg.get("topics") or []
        if t and t[0] == TRANSFER_TOPIC and len(t) == 3 and lg["address"].lower() != trade.token \
                and POOL_MANAGER in (topic_addr(t[1]), topic_addr(t[2])):
            moved.setdefault(lg["address"].lower(), []).append(to_int(lg.get("data")))
    for a0, a1 in unknown:
        for tok_amt, q_amt in ((a1, a0), (a0, a1)):
            if _close(tok_amt, trade.token_raw) is None:
                continue
            if not moved:
                return ZERO, q_amt  # nothing else moved: paid in native ETH
            for cur, amounts in moved.items():
                if any(_close(v, q_amt) is not None for v in amounts):
                    return cur, q_amt
    return None


def resolve_pools(rpc: Rpc, pool_ids: set[str], pools: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Fill `pools` for unknown ids from their Initialize logs (one wide getLogs per 50 ids).

    Returns only the newly resolved entries (caller persists them)."""
    missing = sorted(pid for pid in pool_ids if pid not in pools)
    found: dict[str, tuple[str, str]] = {}
    if not missing:
        return found
    head = rpc.block_number()
    for i in range(0, len(missing), 50):
        try:
            logs = rpc.call("eth_getLogs", [{"address": POOL_MANAGER, "topics": [INITIALIZE_TOPIC, missing[i:i + 50]],
                                             "fromBlock": "0x0", "toBlock": hex(head)}]) or []
        except RpcError:
            continue  # quote falls back to the transfer heuristic
        for lg in logs:
            if len(lg.get("topics") or []) == 4:
                p = parse_initialize(lg)
                found[p.pool_id] = (p.currency0, p.currency1)
    pools.update(found)
    return found


def enrich(rpc: Rpc, trades: list[Trade], weth: str | None = None,
           pools: dict[str, tuple[str, str]] | None = None) -> list[Trade]:
    """Fill quote/quote_raw/eth_wei (receipts) and ts (block headers) in batched RPC calls.

    `pools` (pool id -> currencies) is used and extended in place; unknown ids are resolved on-chain."""
    if not trades:
        return trades
    pools = {} if pools is None else pools
    weth = (weth or "").lower()
    txs = sorted({t.tx for t in trades})
    receipts = dict(zip(txs, rpc.batch([("eth_getTransactionReceipt", [h]) for h in txs])))
    ids = {pid for rc in receipts.values() if rc for pid, _, _ in _swaps(rc)}
    resolve_pools(rpc, ids, pools)
    blocks = sorted({t.block for t in trades if t.ts is None})  # explorer rows already carry a timestamp
    headers = dict(zip(blocks, rpc.batch([("eth_getBlockByNumber", [hex(b), False]) for b in blocks])))
    for t in trades:
        if (rc := receipts.get(t.tx)) and (leg := quote_leg(rc, t, pools)):
            t.quote, t.quote_raw = leg
            if t.quote in (ZERO, weth):
                t.eth_wei = t.quote_raw
        if t.ts is None and (hd := headers.get(t.block)):
            t.ts = to_int(hd.get("timestamp"))
    return trades
