"""Pre-flight for the in-app swap: `python -m app.tools.verify_swap` (run on the box, with the real .env).

Checks that every configured address has contract code on the chain and that the quoters answer a tiny quote.
It sends nothing and signs nothing. Switch SWAP_ENABLED on only after this passes AND a first small swap
has been confirmed in a real wallet.
"""
import asyncio

from app.core.config import get_settings
from app.services.rpc import RPCClient


async def main() -> int:
    s, rpc, bad = get_settings(), RPCClient(), 0
    for name, addr in {"UniversalRouter": s.swap_universal_router, "Permit2": s.swap_permit2, "QuoterV2": s.swap_quoter_v2,
                       "v4 Quoter": s.swap_v4_quoter, "WETH": s.swap_weth, "USDG": s.swap_usdg}.items():
        if not addr:
            print(f"MISSING   {name}")
            bad += 1
            continue
        code = await rpc.call("eth_getCode", [addr, "latest"])
        ok = bool(code) and code != "0x"
        bad += not ok
        print(f"{'ok':9} {name} {addr}" if ok else f"NO CODE   {name} {addr}")
    print("chain id:", int(await rpc.call("eth_chainId", []), 16), "(expected", s.chain_id, ")")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
