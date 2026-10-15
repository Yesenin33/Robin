"""ERC-20 metadata + static contract security checks from bytecode and storage."""
from app.services.intelligence.rpc import DEAD, ZERO, Rpc, to_int

# 4-byte selectors (keccak256(signature)[:4]); verified in tests/test_intelligence.py
SEL = {
    "symbol": "95d89b41", "name": "06fdde03", "decimals": "313ce567", "totalSupply": "18160ddd", "owner": "8da5cb5b",
}
MINT = {"40c10f19", "a0712d68", "449a52f8"}  # mint(address,uint256) mint(uint256) mintTo(address,uint256)
BLACKLIST = {"f9f92be4", "44337ea1", "153b0d1e", "455a4396", "d34628cc", "9c0db5f3", "342aa8b5"}
PAUSE = {"8456cb59"}  # pause()
TRADING_TOGGLE = {"8a8c523c", "c9567bf9", "c2e5ec04"}  # enableTrading() openTrading() setTradingEnabled(bool)
FEE_SETTER = {"0b78f9c0", "c647b20e", "0cc835a3", "8b4cee08", "6db79437"}

EIP1967_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_BEACON = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
EIP1167_PREFIX = "363d3d373d3d3d363d73"


def decode_string(h: str | None) -> str | None:
    if not h or h == "0x":
        return None
    b = bytes.fromhex(h[2:])
    try:
        if len(b) >= 64:
            n = int.from_bytes(b[32:64], "big")
            return b[64 : 64 + n].decode("utf-8", "ignore").strip() or None
        return b.rstrip(b"\x00").decode("utf-8", "ignore").strip() or None
    except Exception:
        return None


def push4_selectors(code_hex: str) -> set[str]:
    """All PUSH4 immediates in the bytecode (function dispatch table), skipping other PUSH data."""
    code = bytes.fromhex(code_hex.removeprefix("0x"))
    out, i = set(), 0
    while i < len(code):
        op = code[i]
        if 0x60 <= op <= 0x7F:  # PUSH1..PUSH32
            n = op - 0x5F
            if n == 4:
                out.add(code[i + 1 : i + 5].hex())
            i += 1 + n
        else:
            i += 1
    return out


def read_meta(rpc: Rpc, token: str) -> dict:
    keys = ["symbol", "name", "decimals", "totalSupply", "owner"]
    res = rpc.batch([("eth_call", [{"to": token, "data": "0x" + SEL[k]}, "latest"]) for k in keys])
    r = dict(zip(keys, res))
    decimals = to_int(r["decimals"]) if r["decimals"] else 18
    owner = None
    if r["owner"] and len(r["owner"]) >= 66:
        owner = "0x" + r["owner"][-40:].lower()
    return {
        "symbol": decode_string(r["symbol"]),
        "name": decode_string(r["name"]),
        "decimals": decimals,
        "total_supply_raw": to_int(r["totalSupply"]) if r["totalSupply"] else None,
        "owner": owner,  # None = no owner() function
    }


def _slot_addr(v: str) -> str | None:
    a = "0x" + v[-40:].lower() if v and len(v) >= 42 else None
    return None if a in (None, ZERO) else a


def security(rpc: Rpc, token: str, owner: str | None) -> dict:
    code = rpc.get_code(token).lower()
    if code in ("0x", "0x0"):
        raise ValueError("not a contract")

    impl = None
    if EIP1167_PREFIX in code[:40]:
        impl = "0x" + code[code.index(EIP1167_PREFIX) + len(EIP1167_PREFIX):][:40]
    else:
        impl = _slot_addr(rpc.get_storage(token, EIP1967_IMPL))
        if impl is None and (beacon := _slot_addr(rpc.get_storage(token, EIP1967_BEACON))):
            res = rpc.eth_call(beacon, "0x5c60da1b")  # implementation()
            impl = _slot_addr(res or "")
    proxy = impl is not None
    selectors = push4_selectors(code)
    if impl:
        selectors |= push4_selectors(rpc.get_code(impl))

    if owner is None:
        owner_renounced = None  # no owner() — can't tell from the ABI alone
    else:
        owner_renounced = owner in (ZERO, DEAD)
    return {
        "proxy": proxy,
        "implementation": impl,
        "can_mint": bool(selectors & MINT),
        "blacklist": bool(selectors & BLACKLIST),
        "pausable": bool(selectors & PAUSE),
        "trading_toggle": bool(selectors & TRADING_TOGGLE),
        "mutable_fees": bool(selectors & FEE_SETTER),
        "owner_renounced": owner_renounced,
    }
