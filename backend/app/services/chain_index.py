"""Robinhood Chain (Uniswap V4) indexing primitives — sync, stdlib RPC from the intelligence package.

Every V4 swap settles through the PoolManager, so for any ERC-20:
  buy  = Transfer(PoolManager -> wallet)     sell = Transfer(wallet -> PoolManager)
The ETH side of a trade comes from the PoolManager `Swap` event in the same tx receipt: the side
whose |amount| matches the token transfer is the token, the other side is the counter-currency.
If no other ERC-20 moved through the PoolManager in that tx, the counter-currency was native ETH.
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
    eth_wei: int | None = None  # None = counter-currency was not ETH / not resolvable
    ts: int | None = None  # unix seconds


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


def eth_leg(receipt: dict, trade: Trade, weth: str | None = None) -> int | None:
    """ETH paid/received for this trade, from the PoolManager Swap event in the receipt."""
    logs = receipt.get("logs") or []
    other_erc20 = {
        lg["address"].lower() for lg in logs
        if (lg.get("topics") or [None])[0] == TRANSFER_TOPIC and lg["address"].lower() != trade.token
        and len(lg["topics"]) == 3 and POOL_MANAGER in (topic_addr(lg["topics"][1]), topic_addr(lg["topics"][2]))
    }
    eth_is_native = not other_erc20
    eth_is_weth = bool(weth) and other_erc20 == {weth.lower()}
    if not (eth_is_native or eth_is_weth):
        return None
    best: tuple[float, int] | None = None
    for lg in logs:
        if lg.get("address", "").lower() != POOL_MANAGER or (lg.get("topics") or [None])[0] != SWAP_TOPIC:
            continue
        a0, a1 = abs(_int128(_word(lg["data"], 0))), abs(_int128(_word(lg["data"], 1)))
        for tok_side, eth_side in ((a1, a0), (a0, a1)):
            if tok_side and trade.token_raw:
                diff = abs(tok_side - trade.token_raw) / max(tok_side, trade.token_raw)
                if diff <= MATCH_TOLERANCE and (best is None or diff < best[0]):
                    best = (diff, eth_side)
    return best[1] if best else None


def enrich(rpc: Rpc, trades: list[Trade], weth: str | None = None) -> list[Trade]:
    """Fill eth_wei (from receipts) and ts (from block headers) in batched RPC calls."""
    if not trades:
        return trades
    txs = sorted({t.tx for t in trades})
    receipts = dict(zip(txs, rpc.batch([("eth_getTransactionReceipt", [h]) for h in txs])))
    blocks = sorted({t.block for t in trades if t.ts is None})  # explorer rows already carry a timestamp
    headers = dict(zip(blocks, rpc.batch([("eth_getBlockByNumber", [hex(b), False]) for b in blocks])))
    for t in trades:
        if rc := receipts.get(t.tx):
            t.eth_wei = eth_leg(rc, t, weth)
        if t.ts is None and (hd := headers.get(t.block)):
            t.ts = to_int(hd.get("timestamp"))
    return trades
