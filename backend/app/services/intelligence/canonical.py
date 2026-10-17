"""Canonical infrastructure (wrapped ether, the chain's stablecoin) — recognised by VERIFYING it, not by a list.

A hardcoded "this address is fine" would also hide a real finding the day somebody swaps the contract. So an
address only counts as canonical when it is the one the chain config names AND the chain agrees with what that
asset claims to be:

* wrapped native: the bytecode exposes deposit() and withdraw(uint256), and the contract actually holds at
  least as much ether as its totalSupply (the 1:1 backing a WETH9 promises). An unbacked "WETH" fails.
* stablecoin: the config address, with code, whose symbol still reads like the configured asset and whose
  supply is readable. Issuer upgradeability (proxy) stays a reported finding; only the checks that make no
  sense for it (transfer-fee reading, whale-dump maths, "no website") are skipped.

A verified asset is marked in `sources` ("verified:wrapped-native" / "verified:stable"), which is free-form:
the frozen scan schema gets no new key. scoring.assess reads the marker.
"""
from app.services.intelligence.config import ChainConfig
from app.services.intelligence.rpc import Rpc

DEPOSIT, WITHDRAW = "d0e30db0", "2e1a7d4d"  # deposit(), withdraw(uint256)
WRAPPED = "verified:wrapped-native"
STABLE = "verified:stable"


def verify(rpc: Rpc, cfg: ChainConfig, token: str, meta: dict, selectors: set[str] | None) -> str | None:
    token = token.lower()
    if token == cfg.wrapped_native and selectors is not None and {DEPOSIT, WITHDRAW} <= selectors:
        supply = meta.get("total_supply_raw")
        if supply is not None and rpc.balance(token) >= supply:
            return WRAPPED
    if token == cfg.stable and meta.get("total_supply_raw") and (meta.get("symbol") or "").upper().startswith("USD"):
        return STABLE
    return None
