"""In-memory Robinhood Chain (Uniswap V4) for job/service tests: answers the JSON-RPC calls we make."""
from app.services.chain_index import INITIALIZE_TOPIC, POOL_MANAGER, SWAP_TOPIC
from app.services.intelligence.rpc import TRANSFER_TOPIC, ZERO, pad_addr

E18 = 10**18
T0 = 1_791_000_000  # 2026-10-03, after the explorer fixture timestamps


def word(v: int) -> str:
    return (v & ((1 << 256) - 1)).to_bytes(32, "big").hex()


def abi_string(s: str) -> str:
    b = s.encode()
    return "0x" + word(32) + word(len(b)) + b.ljust(32, b"\0").hex()


class V4Chain:
    def __init__(self, head: int = 1000):
        self.head = head
        self.logs: list[dict] = []
        self.receipts: dict[str, list[dict]] = {}
        self.meta: dict[str, tuple[str, int]] = {}  # token -> (symbol, decimals)
        self.calls: list[str] = []
        self.pools: dict[str, tuple[str, str]] = {}
        self._n = 0

    def token(self, addr: str, symbol: str, decimals: int = 18) -> str:
        self.meta[addr] = (symbol, decimals)
        return addr

    def pool(self, block: int, token: str, pool_id: str, quote: str = ZERO) -> None:
        """V4 Initialize: currencies sorted by address (native ETH = ZERO is always currency0)."""
        c0, c1 = sorted([quote, token], key=lambda a: int(a, 16))
        self.pools[pool_id] = (c0, c1)
        self.logs.append({"address": POOL_MANAGER, "blockNumber": hex(block), "transactionHash": f"0xinit{block}",
                          "logIndex": "0x0", "topics": [INITIALIZE_TOPIC, pool_id, "0x" + pad_addr(c0),
                                                        "0x" + pad_addr(c1)], "data": "0x" + word(3000) * 5})

    def swap(self, block: int, wallet: str, token: str, side: str, tokens: int, paid: float, pool_id: str) -> str:
        """`paid` is in quote units (ETH, USDG, ...) of the pool's quote currency."""
        self._n += 1
        tx = "0x" + f"{self._n:064x}"
        c0, c1 = self.pools[pool_id]
        quote = c0 if c1 == token else c1
        q_raw = int(round(paid * 10 ** (18 if quote == ZERO else self.meta[quote][1])))
        frm, to = (POOL_MANAGER, wallet) if side == "buy" else (wallet, POOL_MANAGER)
        transfer = {"address": token, "blockNumber": hex(block), "transactionHash": tx, "logIndex": "0x1",
                    "topics": [TRANSFER_TOPIC, "0x" + pad_addr(frm), "0x" + pad_addr(to)], "data": "0x" + word(tokens)}
        # BalanceDelta from the swapper's side: negative = paid
        tok_d, q_d = (tokens, -q_raw) if side == "buy" else (-tokens, q_raw)
        a0, a1 = (tok_d, q_d) if c0 == token else (q_d, tok_d)
        swap = {"address": POOL_MANAGER, "blockNumber": hex(block), "transactionHash": tx, "logIndex": "0x0",
                "topics": [SWAP_TOPIC, pool_id, "0x" + pad_addr("0x" + "66" * 20)],
                "data": "0x" + word(a0) + word(a1) + word(1) * 4}
        receipt = [swap, transfer]
        if quote != ZERO:  # an ERC-20 quote settles through the PoolManager too
            qf, qt = (wallet, POOL_MANAGER) if side == "buy" else (POOL_MANAGER, wallet)
            receipt.append({"address": quote, "blockNumber": hex(block), "transactionHash": tx, "logIndex": "0x2",
                            "topics": [TRANSFER_TOPIC, "0x" + pad_addr(qf), "0x" + pad_addr(qt)], "data": "0x" + word(q_raw)})
        self.logs.append(transfer)
        self.receipts[tx] = receipt
        return tx

    # --- JSON-RPC ---
    def _match(self, lg: dict, flt: dict) -> bool:
        lo, hi = int(flt["fromBlock"], 16), int(flt["toBlock"], 16)
        if not lo <= int(lg["blockNumber"], 16) <= hi:
            return False
        if "address" in flt and lg["address"] != flt["address"]:
            return False
        for i, want in enumerate(flt.get("topics") or []):
            if want is None:
                continue
            have = lg["topics"][i] if i < len(lg["topics"]) else None
            if have not in (want if isinstance(want, list) else [want]):
                return False
        return True

    def handle(self, m: str, p: list):
        self.calls.append(m)
        if m == "eth_blockNumber":
            return hex(self.head)
        if m == "eth_getLogs":
            return [lg for lg in self.logs if self._match(lg, p[0])]
        if m == "eth_getTransactionReceipt":
            return {"logs": self.receipts.get(p[0], [])}
        if m == "eth_getBlockByNumber":
            return {"timestamp": hex(T0 + int(p[0], 16))}
        if m == "eth_getCode":
            return "0x6001" if p[0] in self.meta or p[0] == POOL_MANAGER else "0x"
        if m == "eth_call":
            to, sel = p[0]["to"], p[0]["data"][2:10]
            sym, dec = self.meta.get(to, ("?", 18))
            return {"95d89b41": abi_string(sym), "06fdde03": abi_string(sym.title()),
                    "313ce567": "0x" + word(dec), "18160ddd": "0x" + word(10**27), "8da5cb5b": "0x" + word(0)}.get(sel, "0x")
        raise AssertionError(m)

    def __call__(self, payload):
        return [{"jsonrpc": "2.0", "id": c["id"], "result": self.handle(c["method"], c["params"])} for c in payload]
